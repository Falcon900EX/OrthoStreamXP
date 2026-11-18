# SPDX-License-Identifier: GPL-3.0-only
"""Versioned local IPC. DDS bytes are binary, not JSON/base64.

Each in-flight request exclusively leases a persistent socket from a bounded
pool. Do not use threading.local here: ctypes callbacks from native FUSE threads
can lose their Python thread-local state after every callback. Keeping those
sockets alive separately would leak one connection per callback.

A failed request is NEVER replayed: open/release are not idempotent. Only a later
callback may reconnect. File handles belong to the engine, not to a connection.
"""
from __future__ import annotations

import errno
import json
import logging
import math
import select
import socket
import struct
import threading
import time
from typing import Any

VERSION = 1
MAX_REQUEST = 65536
MAX_RESPONSE = 4 * 1024 * 1024
MAX_READ = MAX_RESPONSE
# Leave headroom below EngineServer's 256-connection safety cap.
MAX_CONNECTIONS = 64
REQUEST_HEADER = struct.Struct("<I")
RESPONSE_HEADER = struct.Struct("<iI")
log = logging.getLogger(__name__)


def receive_exact(sock: socket.socket, count: int, closed: threading.Event | None = None) -> bytes:
    data = bytearray(count)
    view = memoryview(data)
    done = 0
    while done < count:
        if closed is not None and closed.is_set():
            raise OSError(errno.EIO, "Engine client closed during read")
        if closed is not None:
            readable, _, _ = select.select([sock], [], [], 0.05)
            if not readable:
                continue
            if closed.is_set():
                raise OSError(errno.EIO, "Engine client closed during read")
        n = sock.recv_into(view[done:])
        if not n:
            raise ConnectionError("OrthoStreamXP engine closed its socket")
        done += n
    return bytes(data)


class EngineClient:
    def __init__(self, path: str, timeout: float = 1700.0,
                 max_connections: int = MAX_CONNECTIONS):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("IPC timeout must be finite and positive")
        if not isinstance(max_connections, int) or not 1 <= max_connections <= 128:
            raise ValueError("IPC pool size must be between 1 and 128")
        self.path = path
        self.timeout = timeout
        self.max_connections = max_connections
        self._sockets: set[socket.socket] = set()
        self._idle: list[socket.socket] = []
        self._condition = threading.Condition()
        self._closed = False
        self._close_event = threading.Event()

    def _connection(self) -> socket.socket:
        """Borrow exclusively; connect outside the lock so close() can interrupt."""
        deadline = time.monotonic() + self.timeout
        with self._condition:
            while True:
                if self._closed:
                    raise OSError(errno.EIO, "Engine client is closed")
                if self._idle:
                    return self._idle.pop()
                if len(self._sockets) < self.max_connections:
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    try:
                        sock.settimeout(self.timeout)
                    except BaseException:
                        sock.close()
                        raise
                    # Include connecting sockets in the bound and in shutdown.
                    self._sockets.add(sock)
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OSError(errno.EIO, "Timed out waiting for a free engine connection")
                self._condition.wait(remaining)
        try:
            sock.connect(self.path)
            with self._condition:
                if self._closed:
                    raise OSError(errno.EIO, "Engine client is closed")
            return sock
        except BaseException:
            self._discard(sock)
            raise

    def _return(self, sock: socket.socket) -> None:
        with self._condition:
            if not self._closed and sock in self._sockets:
                self._idle.append(sock)
                self._condition.notify()

    def _discard(self, sock: socket.socket) -> None:
        # Discarded leases are not returned to the idle pool by the caller.
        with self._condition:
            self._sockets.discard(sock)
            sock.close()
            self._condition.notify_all()

    def call(self, op: str, **fields: Any) -> Any:
        try:
            return self._call(op, fields)
        except OSError as exc:
            if exc.errno == errno.EIO:
                # mfusepy logs ordinary OSError only at DEBUG. Preserve the real
                # failure at ERROR, without logging DDS data or request bodies.
                log.error("Bridge RPC failed: op=%s path=%r fh=%r offset=%r size=%r: %s",
                          op, fields.get("path"), fields.get("fh"),
                          fields.get("offset"), fields.get("size"), exc, exc_info=True)
            raise

    def _call(self, op: str, fields: dict[str, Any]) -> Any:
        payload = json.dumps({"v": VERSION, "op": op, **fields},
                             ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_REQUEST:
            raise OSError(errno.EINVAL, "IPC request too large")
        try:
            sock = self._connection()
        except OSError as exc:
            raise OSError(errno.EIO, f"OrthoStreamXP engine connection unavailable: {exc}") from exc
        try:
            return self._exchange(sock, op, fields, payload)
        finally:
            self._return(sock)

    def _exchange(self, sock: socket.socket, op: str, fields: dict[str, Any], payload: bytes) -> Any:
        try:
            sock.sendall(REQUEST_HEADER.pack(len(payload)) + payload)
            error, size = RESPONSE_HEADER.unpack(receive_exact(sock, RESPONSE_HEADER.size, self._close_event))
            if error < 0 or size > MAX_RESPONSE:
                raise ValueError("Invalid engine response header")
            data = receive_exact(sock, size, self._close_event)
        except (OSError, ConnectionError, ValueError) as exc:
            self._discard(sock)
            raise OSError(errno.EIO, f"OrthoStreamXP engine connection failed: {exc}") from exc
        if error:
            # A complete error reply does not corrupt framing: keep this socket.
            raise OSError(error, data.decode("utf-8", errors="replace"))
        if op == "read":
            if len(data) > fields["size"]:
                self._discard(sock)
                raise OSError(errno.EIO, "Engine returned more bytes than requested")
            return data
        try:
            return json.loads(data)
        except (ValueError, UnicodeDecodeError) as exc:
            self._discard(sock)
            raise OSError(errno.EIO, "Malformed engine metadata") from exc

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._close_event.set()
            sockets = tuple(self._sockets)
            self._sockets.clear()
            self._idle.clear()
            self._condition.notify_all()
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # Socket may be connecting or already closed by its callback.
            sock.close()
