# SPDX-License-Identifier: GPL-3.0-only
"""One isolated macFUSE worker per package; the .NET engine owns its stdin pipe.

Control-pipe/parent-EOF design adapted from AutoOrtho Continued 3.1.0
mount_worker._start_control_thread (Apache-2.0; donor/NOTICE.md).
MODIFIED: bridge IPC, explicit ownership, busy retry, separate worker session.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time

from bridge_fs import OrthoOperations
from donor.mac_lifecycle import unmount_owned
from macfuse_runtime import load_macfuse, mount_options
from mount_paths import prepare_mountpoint, restore_placeholder
from rpc import EngineClient

log = logging.getLogger(__name__)


def run(args) -> int:
    # Do not let Terminal's Ctrl+C terminate the FUSE worker before the parent can
    # unmount it. mfusepy/libfuse manage their own signal handlers in this session.
    if os.getsid(0) != os.getpid():
        os.setsid()
    binding = load_macfuse()
    client = EngineClient(args.socket)
    if client.call("ping") != {"version": 1}:
        raise OSError("Bridge protocol handshake failed")
    prepare_mountpoint(args.mountpoint)
    initialized = threading.Event()
    finished = threading.Event()

    def ready():
        initialized.set()
        try:
            print("READY", flush=True)
        except BrokenPipeError:
            pass  # The control thread sees parent EOF and detaches while FUSE is alive.

    def control():
        while not finished.is_set():
            command = sys.stdin.buffer.readline()
            parent_gone = command == b""
            if command not in (b"stop\n", b"", b"force\n"):
                continue
            # Covers a parent exit during startup. Once init runs, unmount out of
            # band; never ask FUSE to exit from one of its filesystem callbacks.
            if not initialized.wait(60):
                log.error("Shutdown requested before mount initialization; retaining worker")
                if not parent_gone:
                    print("BUSY", flush=True)
                if parent_gone:
                    time.sleep(5)
                continue
            force = command == b"force\n" or parent_gone
            try:
                detached = unmount_owned(args.mountpoint, args.identity, force=force)
            except OSError as exc:
                log.error("Cannot detach mount: %s", exc)
                detached = False
            if detached:
                return
            if not parent_gone:
                print("BUSY", flush=True)
            if parent_gone:
                # No new requests can reach a dead engine, but keep the worker
                # responsive and retry bounded helpers instead of leaving a dead mount.
                time.sleep(5)

    class FuseBridge(OrthoOperations, binding.Operations):
        pass

    thread = threading.Thread(target=control, name="OrthoStreamXP-Control", daemon=True)
    thread.start()
    try:
        binding.FUSE(FuseBridge(client, ready), args.mountpoint,
                     **mount_options(args.identity, args.name, args.direct_io))
    finally:
        finished.set()
        client.close()
        # Do not touch the path until a cached-table check proves it is unmounted.
        try:
            restore_placeholder(args.mountpoint)
        except OSError as exc:
            log.error("Placeholder not restored: %s", exc)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--mountpoint", required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--direct-io", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="[macFUSE %(process)d] %(levelname)s %(message)s")
    try:
        return run(args)
    except Exception:
        log.exception("Mount worker failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
