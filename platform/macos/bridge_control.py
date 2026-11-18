# SPDX-License-Identifier: GPL-3.0-only
"""Short-lived, non-FUSE control commands used by the CLI and test scripts."""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys

from donor.mac_lifecycle import unmount_owned
from donor.macos_mounts import find_mount, read_mount_table
from macfuse_runtime import load_macfuse
from mount_paths import guard_fuse_paths, validate_layout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("doctor")
    check = subs.add_parser("check")
    check.add_argument("--source", required=True)
    check.add_argument("--cache", required=True)
    check.add_argument("--mountpoint", required=True)
    guard = subs.add_parser("guard")
    guard.add_argument("paths", nargs="+")
    subs.add_parser("mounts")
    stop = subs.add_parser("unmount")
    stop.add_argument("mountpoint")
    stop.add_argument("--force", action="store_true")
    stop.add_argument("--identity")
    args = parser.parse_args()
    if args.command == "doctor":
        binding = load_macfuse()
        print(json.dumps({"python": sys.version.split()[0], "architecture": platform.machine(),
                          "macos": platform.mac_ver()[0], "fuseLibrary": binding._libfuse_path,
                          "mountTableReadable": isinstance(read_mount_table(), list)}))
    elif args.command == "check":
        source, cache, mount = validate_layout(args.source, args.cache, args.mountpoint)
        print(json.dumps({"source": source, "cache": cache, "mountpoint": mount}))
    elif args.command == "guard":
        guard_fuse_paths(args.paths)
        print('{"ok":true}')
    elif args.command == "mounts":
        print(json.dumps([e._asdict() for e in read_mount_table() if e.source.startswith("OrthoStreamXP-")]))
    elif args.command == "unmount":
        entry = find_mount(args.mountpoint)
        if entry is None:
            print("Already unmounted")
        elif args.identity and entry.source != args.identity:
            raise OSError("Mount ownership changed; refusing to unmount it")
        elif not entry.source.startswith("OrthoStreamXP-"):
            raise OSError("This is not an OrthoStreamXP mount; refusing to unmount it")
        elif not unmount_owned(args.mountpoint, entry.source, force=args.force):
            raise OSError("Mount is busy. Close X-Plane and retry; the serving process must stay running")
        else:
            print("Unmounted")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, ImportError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
