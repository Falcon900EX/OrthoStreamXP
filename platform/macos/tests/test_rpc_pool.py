# SPDX-License-Identifier: GPL-3.0-only
"""Connection-lifetime regressions, including real foreign-thread callbacks.

The native helper is test-only, built into a temporary directory when a C
compiler is present. It never mounts a filesystem or loads libfuse.
"""
from concurrent.futures import ThreadPoolExecutor
import ctypes
import errno
import logging
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rpc import EngineClient
from test_bridge import FakeServer, response


class PoolTests(unittest.TestCase):
    def setUp(self):
        logger = patch("rpc.log")
        self.log = logger.start()
        self.addCleanup(logger.stop)

    def make(self, responder, **options):
        server = FakeServer(responder)
        self.addCleanup(server.close)
        client = EngineClient(server.path, timeout=options.pop("timeout", 2), **options)
        self.addCleanup(client.close)
        return server, client

    def test_sequential_short_lived_callers_share_one_socket(self):
        server, client = self.make(lambda r: response({"value": r["value"]}))
        results, errors = [], []
        def call(i):
            try:
                results.append(client.call("ping", value=i))
            except Exception as exc:
                errors.append(exc)
        for i in range(300):
            worker = threading.Thread(target=call, args=(i,))
            worker.start()
            worker.join(3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, [{"value": i} for i in range(300)])
        self.assertEqual(len(server.clients), 1)
        self.assertEqual(len(client._sockets), 1)

    def test_concurrent_requests_keep_bound_and_correct_responses(self):
        def responder(request):
            time.sleep(0.002)
            return response({"value": request["value"]})
        server, client = self.make(responder, max_connections=3)
        with ThreadPoolExecutor(max_workers=12) as pool:
            answers = list(pool.map(lambda i: client.call("ping", value=i), range(120)))
        self.assertEqual(answers, [{"value": i} for i in range(120)])
        self.assertEqual(len(server.clients), 3)
        self.assertEqual(len(client._idle), 3)
        self.assertEqual(len(client._sockets), 3)

    def test_remote_error_does_not_discard_healthy_connection(self):
        def responder(request):
            return response(b"Not found", errno.ENOENT) if request["op"] == "stat" else response({"version": 1})
        server, client = self.make(responder)
        with self.assertRaises(OSError) as caught:
            client.call("stat", path="/missing")
        self.assertEqual(caught.exception.errno, errno.ENOENT)
        self.assertEqual(client.call("ping"), {"version": 1})
        self.assertEqual(len(server.clients), 1)
        self.log.error.assert_not_called()

    def test_later_request_reconnects_but_failed_open_is_not_replayed(self):
        def responder(request):
            return None if len(server.requests) == 1 else response({"version": 1})
        server, client = self.make(responder)
        with self.assertRaises(OSError):
            client.call("open", path="/file")
        self.assertEqual(len(client._idle), 0)
        self.assertEqual(len(client._sockets), 0)
        self.assertEqual(client.call("ping"), {"version": 1})
        self.assertEqual([r["op"] for r in server.requests], ["open", "ping"])
        self.assertEqual(len(server.clients), 2)

    def test_connection_failure_returns_capacity(self):
        server, client = self.make(lambda r: response({"version": 1}), max_connections=1)
        client.path = server.path + "-missing"
        with self.assertRaises(OSError):
            client.call("ping")
        self.assertEqual(len(client._sockets), 0)
        client.path = server.path
        self.assertEqual(client.call("ping"), {"version": 1})

    def test_io_failure_logs_operation_range_and_traceback(self):
        _, client = self.make(lambda r: None)
        with self.assertRaises(OSError):
            client.call("read", fh=19, offset=128, size=4096)
        args, kwargs = self.log.error.call_args
        message = args[0] % args[1:]
        self.assertIn("op=read", message)
        self.assertIn("fh=19", message)
        self.assertIn("offset=128", message)
        self.assertIn("size=4096", message)
        self.assertTrue(kwargs["exc_info"])

    def test_lease_wait_has_a_timeout_and_sends_no_request(self):
        server, client = self.make(lambda r: response({}), max_connections=1, timeout=0.1)
        borrowed = client._connection()
        try:
            started = time.monotonic()
            with self.assertRaises(OSError) as caught:
                client.call("ping")
            self.assertEqual(caught.exception.errno, errno.EIO)
            self.assertIn("free engine connection", str(caught.exception))
            self.assertLess(time.monotonic() - started, 1.0)
            self.assertEqual(server.requests, [])
        finally:
            client._return(borrowed)

    def test_close_interrupts_active_request_and_waiting_borrower(self):
        entered, unblock, second_started = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(unblock.set)
        def responder(request):
            entered.set()
            unblock.wait(3)
            return response({})
        server, client = self.make(responder, max_connections=1)
        def call(second=False):
            if second:
                second_started.set()
            try:
                client.call("ping")
            except OSError as exc:
                return exc.errno
            return 0
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(call)
            self.assertTrue(entered.wait(2))
            second = workers.submit(call, True)
            self.assertTrue(second_started.wait(2))
            client.close()
            try:
                self.assertEqual(first.result(timeout=2), errno.EIO)
                self.assertEqual(second.result(timeout=2), errno.EIO)
            finally:
                unblock.set()
        self.assertEqual(len(server.clients), 1)
        self.assertEqual(len(client._sockets), 0)
        self.assertEqual(client._idle, [])

    def test_close_is_idempotent_and_returned_lease_cannot_resurrect_socket(self):
        _, client = self.make(lambda r: response({}))
        lease = client._connection()
        client.close()
        client.close()
        client._return(lease)
        self.assertEqual(lease.fileno(), -1)
        self.assertEqual(client._idle, [])
        with self.assertRaises(OSError):
            client.call("ping")

    def test_invalid_pool_size_is_rejected(self):
        for value in (0, -1, 129, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                EngineClient("unused", max_connections=value)

    def test_invalid_timeout_is_rejected(self):
        for value in (0, -1, float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                EngineClient("unused", timeout=value)


class NativeCallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.implementation.name != "cpython" or platform.system() not in ("Darwin", "Linux"):
            raise unittest.SkipTest("Native ctypes regression requires CPython on Darwin or Linux")
        compiler = shutil.which("cc")
        if compiler is None:
            raise unittest.SkipTest("Optional native regression needs a C compiler; portable pool tests still run")
        if platform.system() == "Darwin":
            # Do not launch Apple's compiler installation prompt from a test.
            selected = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, timeout=5, check=False)
            if selected.returncode:
                raise unittest.SkipTest("Optional native regression needs existing Command Line Tools")
        cls.temp = tempfile.TemporaryDirectory(prefix="osxp-native-callback-")
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(__file__).with_name("native") / "callbacks.c"
        output = Path(cls.temp.name) / ("callbacks.dylib" if platform.system() == "Darwin" else "callbacks.so")
        flags = ["-dynamiclib"] if platform.system() == "Darwin" else ["-shared", "-fPIC"]
        result = subprocess.run([compiler, *flags, "-pthread", "-Wall", "-Wextra", "-Werror",
                                 str(source), "-o", str(output)], capture_output=True, text=True, timeout=30, check=False)
        if result.returncode:
            raise RuntimeError(f"Native callback test helper failed to compile:\n{result.stderr}")
        cls.callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)
        cls.library = ctypes.CDLL(str(output))
        cls.run_callbacks = cls.library.run_native_callbacks
        cls.run_callbacks.argtypes = [cls.callback_type, ctypes.c_int]
        cls.run_callbacks.restype = ctypes.c_int

    def test_thread_local_state_does_not_survive_native_callbacks(self):
        # Positive control: exercises the exact ctypes behavior behind the leak.
        local = threading.local()
        values = []
        @self.callback_type
        def callback(index):
            local.count = getattr(local, "count", 0) + 1
            values.append(local.count)
            return 0
        self.assertEqual(self.run_callbacks(callback, 3), -1)
        self.assertEqual(values, [1, 1, 1])

    def test_native_callbacks_reuse_socket_beyond_engine_limit(self):
        # Model the existing C# server's cap. The previous client fails at call
        # 257 because all 256 earlier callback sockets are still alive.
        server = FakeServer(lambda r: response({"version": 1}) if len(server.clients) <= 256 else None)
        self.addCleanup(server.close)
        client = EngineClient(server.path, timeout=2)
        self.addCleanup(client.close)
        errors, thread_ids = [], set()
        @self.callback_type
        def callback(index):
            try:
                thread_ids.add(threading.get_ident())
                if client.call("ping") != {"version": 1}:
                    raise AssertionError("Wrong response")
                return 0
            except Exception as exc:
                errors.append((index, str(exc)))
                return 1
        self.assertEqual(self.run_callbacks(callback, 600), -1, errors)
        self.assertEqual(errors, [])
        self.assertEqual(len(thread_ids), 1)
        self.assertEqual(len(server.requests), 600)
        self.assertEqual(len(server.clients), 1)
        self.assertEqual(len(client._sockets), 1)

    def test_concurrent_native_callers_do_not_mix_responses(self):
        server = FakeServer(lambda r: response({"value": r["value"]}))
        self.addCleanup(server.close)
        client = EngineClient(server.path, timeout=2, max_connections=3)
        self.addCleanup(client.close)
        errors = []
        def run(worker):
            @self.callback_type
            def callback(index):
                value = f"{worker}:{index}"
                try:
                    if client.call("ping", value=value) != {"value": value}:
                        raise AssertionError("Response delivered to the wrong callback")
                    return 0
                except Exception as exc:
                    errors.append((value, str(exc)))
                    return 1
            return self.run_callbacks(callback, 200)
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(run, range(4)))
        self.assertEqual(results, [-1] * 4, errors)
        self.assertEqual(errors, [])
        self.assertEqual(len(server.requests), 800)
        self.assertLessEqual(len(server.clients), 3)


if __name__ == "__main__":
    unittest.main()
