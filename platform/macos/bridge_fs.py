# SPDX-License-Identifier: GPL-3.0-only
"""Read-only FUSE operations. Kept independent of libfuse for unit testing."""
from __future__ import annotations

import errno
import os
import stat
from typing import Any, Iterator

from rpc import MAX_READ, EngineClient


class OrthoOperations:
    def __init__(self, engine: EngineClient, ready=None):
        self.engine = engine
        self.ready = ready

    def init(self, path: str) -> None:
        if self.ready is not None:
            self.ready()

    @staticmethod
    def _stat(info: dict[str, Any]) -> dict[str, Any]:
        directory = info["directory"]
        return {
            "st_mode": (stat.S_IFDIR | 0o555) if directory else (stat.S_IFREG | 0o444),
            "st_nlink": 2 if directory else 1,
            "st_size": info["size"],
            "st_uid": os.getuid(), "st_gid": os.getgid(),
            "st_atime": info["modified"], "st_mtime": info["modified"],
            "st_ctime": info["modified"], "st_ino": info["inode"],
            "st_blocks": (info["size"] + 511) // 512,
            "st_blksize": 4096,
        }

    def getattr(self, path: str | None, fh: int | None = None) -> dict[str, Any]:
        info = self.engine.call("fstat", fh=fh) if fh else self.engine.call("stat", path=path)
        return self._stat(info)

    def open(self, path: str, flags: int) -> int:
        if (flags & os.O_ACCMODE) != os.O_RDONLY or flags & (os.O_TRUNC | os.O_CREAT | os.O_APPEND):
            raise OSError(errno.EROFS, "Read-only scenery mount")
        return self.engine.call("open", path=path)["fh"]

    def read(self, path: str, size: int, offset: int, fh: int) -> bytes:
        if size < 0 or offset < 0:
            raise OSError(errno.EINVAL, "Negative read range")
        if size == 0:
            return b""
        parts = []
        while size:
            wanted = min(size, MAX_READ)
            part = self.engine.call("read", fh=fh, offset=offset, size=wanted)
            parts.append(part)
            if len(part) < wanted:
                break
            offset += len(part)
            size -= len(part)
        return b"".join(parts)

    def release(self, path: str, fh: int) -> int:
        self.engine.call("release", fh=fh)
        return 0

    def opendir(self, path: str) -> int:
        return self.engine.call("opendir", path=path)["fh"]

    def readdir(self, path: str, fh: int) -> Iterator[str]:
        yield "."
        yield ".."
        offset = 0
        while True:
            result = self.engine.call("readdir", fh=fh, offset=offset)
            names = result["names"]
            yield from names
            offset += len(names)
            if result["end"]:
                return
            if not names:
                raise OSError(errno.EIO, "Empty nonfinal directory page")

    def releasedir(self, path: str, fh: int) -> int:
        return self.release(path, fh)

    def access(self, path: str, mode: int) -> int:
        if mode & os.W_OK:
            raise OSError(errno.EROFS, "Read-only scenery mount")
        info = self.engine.call("stat", path=path)
        if mode & os.X_OK and not info["directory"]:
            raise OSError(errno.EACCES, "Scenery files are not executable")
        return 0

    def statfs(self, path: str) -> dict[str, int]:
        return {"f_bsize": 4096, "f_frsize": 4096, "f_blocks": 1 << 28,
                "f_bfree": 0, "f_bavail": 0, "f_files": 1 << 30,
                "f_ffree": 0, "f_favail": 0, "f_namemax": 255,
                "f_flag": 1}  # ST_RDONLY; this virtual size is not cache disk space.

    def listxattr(self, path: str) -> list[str]:
        return []

    def getxattr(self, path: str, name: str, position: int = 0) -> bytes:
        raise OSError(getattr(errno, "ENOATTR", errno.ENODATA), "No extended attributes")

    def flush(self, path: str, fh: int) -> int:
        return 0

    def fsync(self, path: str, datasync: int, fh: int) -> int:
        return 0

    def fsyncdir(self, path: str, datasync: int, fh: int) -> int:
        return 0

    def readlink(self, path: str) -> str:
        raise OSError(errno.EINVAL, "Symlinks are not exposed by this mount")

    def _readonly(self, *args, **kwargs):
        raise OSError(errno.EROFS, "Read-only scenery mount")

    chmod = chown = create = link = mkdir = mknod = removexattr = _readonly
    rename = rmdir = setxattr = symlink = truncate = unlink = utimens = write = _readonly
