# SPDX-License-Identifier: GPL-3.0-only
"""Fail-closed mount checks. Never recursively delete a mountpoint."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from donor.macos_mounts import MountEntry, read_mount_table

MARKER = ".orthostreamxp-mount"
MARKER_CONTENT = "OrthoStreamXP macOS port v1\n"
PLACEHOLDERS = frozenset({"Earth nav data", "terrain", "textures"})


def within(path: str, root: str) -> bool:
    # Conservative on case-sensitive APFS too: reject ambiguous case-only overlaps.
    path = os.path.normpath(path).casefold()
    root = os.path.normpath(root).casefold()
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def guard_fuse_paths(paths: Iterable[str], entries: Iterable[MountEntry] | None = None) -> None:
    entries = tuple(read_mount_table() if entries is None else entries)
    for path in paths:
        absolute = os.path.abspath(os.path.expanduser(path))
        # First inspect strings, before realpath/stat can traverse a stale mount.
        for entry in entries:
            if "fuse" in entry.fstype or entry.fstype == "nfs":
                if within(absolute, entry.mountpoint) or within(entry.mountpoint, absolute):
                    raise OSError(f"Path is inside a mounted filesystem; use the backing mesh, not a live mount: {path}")
        resolved = os.path.realpath(absolute)
        for entry in entries:
            if "fuse" in entry.fstype or entry.fstype == "nfs":
                if within(resolved, entry.mountpoint) or within(entry.mountpoint, resolved):
                    raise OSError(f"Path resolves inside a mounted filesystem: {path}")


def validate_layout(source: str, cache: str, mountpoint: str,
                    entries: Iterable[MountEntry] | None = None) -> tuple[str, str, str]:
    table = tuple(read_mount_table() if entries is None else entries)
    guard_fuse_paths([source, cache, mountpoint], table)
    absolute_mount = os.path.abspath(os.path.expanduser(mountpoint))
    # Any mounted volume at the target is forbidden, including APFS, not just FUSE.
    for entry in table:
        if within(absolute_mount, entry.mountpoint) and within(entry.mountpoint, absolute_mount):
            raise OSError(f"Already mounted: {mountpoint}")
    if os.path.islink(absolute_mount):
        raise OSError("The mountpoint must not be a symbolic link")
    source = os.path.realpath(os.path.expanduser(source))
    cache = os.path.realpath(os.path.expanduser(cache))
    mountpoint = os.path.realpath(absolute_mount)
    for a, b in [(source, cache), (source, mountpoint), (cache, mountpoint)]:
        if within(a, b) or within(b, a):
            raise OSError(f"Source, cache and mountpoint must be separate, nonnested directories: {a}; {b}")
    if not os.path.isdir(source) or not os.path.isdir(os.path.join(source, "Earth nav data")):
        raise OSError(f"Not a backing scenery package (Earth nav data missing): {source}")
    if not os.path.isdir(os.path.dirname(mountpoint)):
        raise OSError(f"Mountpoint parent does not exist: {os.path.dirname(mountpoint)}")
    inspect_placeholder(mountpoint)
    return source, cache, mountpoint


def inspect_placeholder(mountpoint: str) -> None:
    path = Path(mountpoint)
    if not path.exists():
        return
    if path.is_symlink() or not path.is_dir():
        raise OSError("Mountpoint must be an ordinary directory")
    names = {child.name for child in path.iterdir()}
    if not names:
        return
    marker = path / MARKER
    if (not names.issubset(PLACEHOLDERS | {MARKER}) or marker.is_symlink()
            or not marker.is_file() or marker.read_text() != MARKER_CONTENT):
        raise OSError(f"Refusing to hide or remove existing scenery at {mountpoint}; choose a new empty mountpoint")
    for name in names - {MARKER}:
        child = path / name
        if child.is_symlink() or not child.is_dir() or any(child.iterdir()):
            raise OSError(f"Placeholder contains user data; not changing {child}")


def prepare_mountpoint(mountpoint: str) -> None:
    # Read the cached mount table before ANY filesystem query to this target.
    guard_fuse_paths([mountpoint])
    for entry in read_mount_table():
        if within(mountpoint, entry.mountpoint) and within(entry.mountpoint, mountpoint):
            raise OSError(f"Already mounted: {mountpoint}")
    inspect_placeholder(mountpoint)
    path = Path(mountpoint)
    if path.exists():
        for name in PLACEHOLDERS:
            child = path / name
            if child.exists():
                child.rmdir()  # Fails instead of deleting any user data.
        marker = path / MARKER
        if marker.exists():
            marker.unlink()
    else:
        path.mkdir(mode=0o755)


def restore_placeholder(mountpoint: str) -> None:
    table = read_mount_table()
    guard_fuse_paths([mountpoint], table)
    for entry in table:
        if within(mountpoint, entry.mountpoint) and within(entry.mountpoint, mountpoint):
            raise OSError(f"Still mounted: {mountpoint}")
    path = Path(mountpoint)
    inspect_placeholder(mountpoint)
    path.mkdir(mode=0o755, exist_ok=True)
    for name in PLACEHOLDERS:
        (path / name).mkdir(exist_ok=True)
    (path / MARKER).write_text(MARKER_CONTENT)
