# SPDX-License-Identifier: Apache-2.0
# Adapted from AutoOrtho Continued 3.1.0, autoortho/macsetup.py,
# unmount_macfuse(), commit 9cf4615. See NOTICE.md and LICENSE-Apache-2.0.
# MODIFIED: require exact filesystem identity; normal stop never force-unmounts;
# no AutoOrtho global config; the parent keeps the engine alive on a busy mount.
"""Detach through a bounded helper while the filesystem worker still responds."""
import logging
import subprocess
import threading

from .macos_mounts import find_mount, mount_path

log = logging.getLogger(__name__)
_pending_unmounts = {}
_unmount_lock = threading.Lock()


def unmount_owned(path: str, identity: str, force: bool = False, timeout: float = 8.0) -> bool:
    path = mount_path(path)
    with _unmount_lock:
        previous = _pending_unmounts.get(path)
        if previous is not None and previous.poll() is None:
            log.error("Unmount helper still blocked for %s; leaving worker alive", path)
            return False
        entry = find_mount(path)
        if entry is None:
            return True
        if entry.source != identity:
            raise OSError(f"Refusing to unmount a different filesystem at {path}: {entry.source}")
        command = ["/sbin/umount"] + (["-f"] if force else []) + [path]
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            log.error("Cannot start unmount helper for %s: %s", path, exc)
            return False
        _pending_unmounts[path] = process
    try:
        result = process.wait(timeout=max(0.0, timeout))
    except subprocess.TimeoutExpired:
        log.error("Unmount timed out for %s; keeping the FUSE worker alive", path)
        # A helper stuck inside the kernel may not exit after SIGKILL. Do not wait
        # without a deadline and do not launch further helpers until it exits.
        try:
            process.kill()
        except OSError:
            pass
        return False
    with _unmount_lock:
        _pending_unmounts.pop(path, None)
    return result == 0 and find_mount(path) is None
