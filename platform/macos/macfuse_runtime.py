# SPDX-License-Identifier: GPL-3.0-only
"""Select macFUSE before importing ctypes bindings; do not silently use FUSE-T."""
import ctypes
import os
import platform
from pathlib import Path


def load_macfuse():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise OSError("This host requires native Apple Silicon macOS (not Rosetta)")
    override = os.environ.get("FUSE_LIBRARY_PATH")
    candidates = [override] if override else [
        "/usr/local/lib/libfuse.2.dylib",
        "/Library/Filesystems/macfuse.fs/Contents/Frameworks/libfuse.2.dylib",
        "/usr/local/lib/libosxfuse.2.dylib",
    ]
    selected = next((p for p in candidates if p and Path(p).is_file()), None)
    if selected is None:
        raise OSError("macFUSE libfuse.2.dylib not found. Set FUSE_LIBRARY_PATH to your installed macFUSE library")
    if "fuse-t" in os.path.realpath(selected).lower():
        raise OSError("FUSE-T is not supported by this macFUSE port")
    os.environ["FUSE_LIBRARY_PATH"] = selected
    # As in mfusepy's automatic Darwin loader, load iconv globally first.
    ctypes.CDLL("/usr/lib/libiconv.2.dylib", ctypes.RTLD_GLOBAL)
    import mfusepy
    if mfusepy.fuse_version_major != 2:
        raise OSError("This bridge requires the installed macFUSE FUSE-2 compatibility library")
    return mfusepy


def mount_options(identity: str, volume_name: str, direct_io: bool = False) -> dict:
    # Adapted from AutoOrtho 3.1.0 autoortho_fuse.fuse_option_profiles_by_os.
    # MODIFIED: no allow_other; readonly; explicit identity and uncached metadata.
    options = dict(foreground=True, nothreads=False, ro=True, fsname=identity,
                   volname=volume_name, daemon_timeout=1800, attr_timeout=0,
                   entry_timeout=0, negative_timeout=0, noappledouble=True, auto_cache=True)
    # Default retains mmap compatibility. No kernel_cache/keep_cache across opens.
    # Opt-in direct_io is diagnostic: macFUSE may reject mmap with this option.
    if direct_io:
        options["direct_io"] = True
    return options
