"""Portable tests: no macFUSE driver, network imagery, or third-party Python modules."""
import ctypes
import errno
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rpc import EngineClient, MAX_READ, MAX_RESPONSE, receive_exact
from bridge_fs import OrthoOperations
from donor import mac_lifecycle, macos_mounts
from donor.macos_mounts import MountEntry
import mount_paths as paths
from macfuse_runtime import mount_options, load_macfuse


class FakeServer:
    """A real AF_UNIX server; responses can be fragmented and intentionally invalid."""
    def __init__(self, responder):
        self.temp = tempfile.TemporaryDirectory(prefix="osxp-rpc-")
        self.path = str(Path(self.temp.name) / "s")
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen()
        self.server.settimeout(0.1)
        self.responder = responder
        self.requests = []
        self.clients = []
        self.workers = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.accept, daemon=True)
        self.thread.start()

    def accept(self):
        while not self.stop.is_set():
            try:
                client, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.clients.append(client)
            worker = threading.Thread(target=self.serve, args=(client,), daemon=True)
            self.workers.append(worker)
            worker.start()

    def serve(self, client):
        try:
            while not self.stop.is_set():
                size, = struct.unpack("<I", receive_exact(client, 4))
                request = json.loads(receive_exact(client, size))
                self.requests.append(request)
                reply = self.responder(request)
                if reply is None:
                    return
                # Fragment the header and payload to exercise stream framing.
                for i in range(0, len(reply), 3):
                    client.sendall(reply[i:i + 3])
        except (OSError, ConnectionError):
            pass
        finally:
            client.close()

    def close(self):
        self.stop.set()
        self.server.close()
        for client in self.clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.thread.join(2)
        for worker in self.workers:
            worker.join(2)
        self.temp.cleanup()


def response(data, error=0):
    payload = data if isinstance(data, bytes) else json.dumps(data).encode()
    return struct.pack("<iI", error, len(payload)) + payload


class RpcTests(unittest.TestCase):
    def setUp(self):
        # Expected protocol failures should not look like runtime failures in
        # build output. Logging itself is covered by the pool regression tests.
        logger = patch("rpc.log")
        logger.start()
        self.addCleanup(logger.stop)

    def make(self, responder):
        server = FakeServer(responder)
        self.addCleanup(server.close)
        client = EngineClient(server.path, timeout=2)
        self.addCleanup(client.close)
        return server, client

    def test_fragmented_metadata_and_unicode(self):
        server, client = self.make(lambda r: response({"fh": 42}))
        self.assertEqual(client.call("open", path="/textures/caf\u00e9.png"), {"fh": 42})
        self.assertEqual(server.requests[0]["path"], "/textures/caf\u00e9.png")
        self.assertEqual(server.requests[0]["v"], 1)

    def test_binary_read_not_json(self):
        _, client = self.make(lambda r: response(b"\x00\xffDDS \x80"))
        self.assertEqual(client.call("read", fh=1, offset=7, size=8), b"\x00\xffDDS \x80")

    def test_errno_is_preserved(self):
        _, client = self.make(lambda r: response(b"Not found", errno.ENOENT))
        with self.assertRaises(OSError) as caught:
            client.call("stat", path="/missing")
        self.assertEqual(caught.exception.errno, errno.ENOENT)

    def test_connection_loss_is_not_replayed(self):
        server, client = self.make(lambda r: None)
        with self.assertRaises(OSError) as caught:
            client.call("open", path="/x")
        self.assertEqual(caught.exception.errno, errno.EIO)
        self.assertEqual(len(server.requests), 1)

    def test_oversized_response_rejected_before_payload(self):
        _, client = self.make(lambda r: struct.pack("<iI", 0, MAX_RESPONSE + 1))
        with self.assertRaises(OSError) as caught:
            client.call("ping")
        self.assertEqual(caught.exception.errno, errno.EIO)

    def test_negative_errno_rejected(self):
        _, client = self.make(lambda r: struct.pack("<iI", -1, 0))
        with self.assertRaises(OSError):
            client.call("ping")

    def test_longer_than_requested_read_rejected(self):
        _, client = self.make(lambda r: response(b"abcdef"))
        with self.assertRaises(OSError):
            client.call("read", fh=1, offset=0, size=2)

    def test_malformed_metadata_rejected(self):
        _, client = self.make(lambda r: response(b"not-json"))
        with self.assertRaises(OSError):
            client.call("ping")

    def test_oversized_request_rejected_locally(self):
        server, client = self.make(lambda r: response({}))
        with self.assertRaises(OSError) as caught:
            client.call("open", path="/" + "a" * 65536)
        self.assertEqual(caught.exception.errno, errno.EINVAL)
        self.assertEqual(server.requests, [])

    def test_reuses_connection_for_same_thread(self):
        server, client = self.make(lambda r: response({"version": 1}))
        for _ in range(4):
            client.call("ping")
        self.assertEqual(len(server.clients), 1)

    def test_concurrent_callbacks_have_independent_connections(self):
        # Force genuine overlap. Idle sockets may now be reused across threads,
        # but simultaneously active requests MUST have separate connections.
        barrier = threading.Barrier(8, timeout=3)
        def responder(request):
            barrier.wait()
            return response({"version": 1})
        server, client = self.make(responder)
        results, errors = [], []
        def call():
            try:
                results.append(client.call("ping"))
            except Exception as exc:
                errors.append(exc)
        workers = [threading.Thread(target=call) for _ in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(4)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(results, [{"version": 1}] * 8)
        self.assertEqual(len(server.clients), 8)

    def test_close_prevents_reconnect(self):
        server, client = self.make(lambda r: response({}))
        client.close()
        with self.assertRaises(OSError):
            client.call("ping")
        self.assertEqual(server.requests, [])

    def test_absent_engine_maps_to_io_error(self):
        client = EngineClient("/tmp/osxp-does-not-exist.sock")
        self.addCleanup(client.close)
        with self.assertRaises(OSError) as caught:
            client.call("ping")
        self.assertEqual(caught.exception.errno, errno.EIO)


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.engine = Mock()
        self.ops = OrthoOperations(self.engine)

    def test_readonly_permissions_and_ownership(self):
        self.engine.call.return_value = {"directory": False, "size": 513, "modified": 123, "inode": 9}
        info = self.ops.getattr("/x")
        self.assertEqual(info["st_mode"], stat.S_IFREG | 0o444)
        self.assertEqual(info["st_blocks"], 2)
        self.assertEqual(info["st_uid"], os.getuid())

    def test_handle_stat_uses_open_handle(self):
        self.engine.call.return_value = {"directory": True, "size": 0, "modified": 123, "inode": 9}
        self.ops.getattr(None, 11)
        self.engine.call.assert_called_once_with("fstat", fh=11)

    def test_write_flags_rejected(self):
        for flags in (os.O_WRONLY, os.O_RDWR, os.O_CREAT, os.O_TRUNC, os.O_APPEND):
            with self.subTest(flags=flags), self.assertRaises(OSError) as caught:
                self.ops.open("/x", flags)
            self.assertEqual(caught.exception.errno, errno.EROFS)
        self.engine.call.assert_not_called()

    def test_open_readonly_passes_handle(self):
        self.engine.call.return_value = {"fh": 101}
        self.assertEqual(self.ops.open("/x", os.O_RDONLY), 101)

    def test_large_read_is_split_with_correct_offsets(self):
        self.engine.call.side_effect = lambda op, **kw: b"a" * kw["size"]
        data = self.ops.read("/x", MAX_READ + 19, 128, 2)
        self.assertEqual(len(data), MAX_READ + 19)
        self.assertEqual(self.engine.call.call_args_list[1].kwargs, {"fh": 2, "offset": 128 + MAX_READ, "size": 19})

    def test_short_read_is_eof(self):
        self.engine.call.return_value = b"end"
        self.assertEqual(self.ops.read("/x", 200, 9, 2), b"end")
        self.engine.call.assert_called_once()

    def test_zero_read_never_calls_engine(self):
        self.assertEqual(self.ops.read("/x", 0, 0, 1), b"")
        self.engine.call.assert_not_called()

    def test_negative_ranges_rejected(self):
        for size, offset in ((-1, 0), (1, -1)):
            with self.assertRaises(OSError):
                self.ops.read("/x", size, offset, 1)

    def test_directory_pagination(self):
        self.engine.call.side_effect = [{"names": ["a", "b"], "end": False}, {"names": ["c"], "end": True}]
        self.assertEqual(list(self.ops.readdir("/", 9)), [".", "..", "a", "b", "c"])
        self.assertEqual(self.engine.call.call_args_list[1].kwargs["offset"], 2)

    def test_empty_nonfinal_page_does_not_loop(self):
        self.engine.call.return_value = {"names": [], "end": False}
        with self.assertRaises(OSError):
            list(self.ops.readdir("/", 9))

    def test_every_mutation_is_readonly(self):
        for name in ("chmod", "chown", "create", "mkdir", "rename", "rmdir", "setxattr", "symlink", "truncate", "unlink", "utimens", "write"):
            with self.subTest(name=name), self.assertRaises(OSError) as caught:
                getattr(self.ops, name)("/x")
            self.assertEqual(caught.exception.errno, errno.EROFS)

    def test_access_checks_execution(self):
        self.engine.call.return_value = {"directory": False}
        with self.assertRaises(OSError) as caught:
            self.ops.access("/x", os.X_OK)
        self.assertEqual(caught.exception.errno, errno.EACCES)

    def test_release_and_directory_release(self):
        self.assertEqual(self.ops.release("/x", 5), 0)
        self.assertEqual(self.ops.releasedir("/", 6), 0)
        self.engine.call.assert_called_with("release", fh=6)

    def test_ready_callback(self):
        ready = Mock()
        OrthoOperations(self.engine, ready).init("/")
        ready.assert_called_once_with()


class MountPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="osxp-layout-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "mesh"
        (self.source / "Earth nav data").mkdir(parents=True)
        self.cache = self.root / "cache"
        self.mount = self.root / "mount"
        patcher = patch.object(paths, "read_mount_table", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def check(self, source=None, cache=None, mount=None):
        return paths.validate_layout(str(source or self.source), str(cache or self.cache), str(mount or self.mount))

    def test_valid_separate_paths(self):
        # validate_layout returns canonical paths, including through aliased parents.
        # On macOS a temporary path under /var may resolve under /private/var.
        expected = tuple(str(path.resolve()) for path in (self.source, self.cache, self.mount))
        self.assertEqual(self.check(), expected)

    def test_symlinked_parent_returns_canonical_paths(self):
        # Reproduce a /var -> /private/var style alias on any supported test host.
        canonical_parent = self.root.resolve() / "private" / "var"
        canonical_parent.mkdir(parents=True)
        alias = self.root / "var"
        alias.symlink_to(canonical_parent, target_is_directory=True)
        source, cache, mount = (alias / name for name in ("mesh", "cache", "mount"))
        (source / "Earth nav data").mkdir(parents=True)
        expected = tuple(str(canonical_parent / name) for name in ("mesh", "cache", "mount"))
        self.assertEqual(self.check(source=source, cache=cache, mount=mount), expected)

    def test_nested_cache_rejected(self):
        with self.assertRaises(OSError):
            self.check(cache=self.source / "cache")

    def test_case_only_overlap_rejected(self):
        self.assertTrue(paths.within("/Data/MeSh/cache", "/data/mesh"))

    def test_nonpackage_rejected(self):
        with self.assertRaises(OSError):
            self.check(source=self.root)

    def test_nonempty_mount_preserved(self):
        self.mount.mkdir()
        sentinel = self.mount / "user.txt"
        sentinel.write_text("keep")
        with self.assertRaises(OSError):
            self.check()
        self.assertEqual(sentinel.read_text(), "keep")

    def test_symlink_mount_rejected(self):
        self.mount.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(OSError):
            self.check()

    def test_cache_symlink_overlap_rejected(self):
        self.cache.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(OSError):
            self.check()

    def test_placeholder_round_trip(self):
        paths.prepare_mountpoint(str(self.mount))
        self.assertEqual(list(self.mount.iterdir()), [])
        paths.restore_placeholder(str(self.mount))
        self.assertEqual({p.name for p in self.mount.iterdir()}, paths.PLACEHOLDERS | {paths.MARKER})
        self.check()
        paths.prepare_mountpoint(str(self.mount))
        self.assertEqual(list(self.mount.iterdir()), [])

    def test_marker_does_not_authorize_deleting_files(self):
        paths.restore_placeholder(str(self.mount))
        data = self.mount / "textures" / "keep.dds"
        data.write_bytes(b"keep")
        with self.assertRaises(OSError):
            paths.prepare_mountpoint(str(self.mount))
        self.assertEqual(data.read_bytes(), b"keep")

    def test_mounted_path_rejected_before_realpath(self):
        entry = MountEntry(str(self.mount), "AutoOrtho", "macfuse")
        with patch.object(paths.os.path, "realpath", side_effect=AssertionError("Must not stat")):
            with self.assertRaises(OSError):
                paths.guard_fuse_paths([str(self.mount / "textures")], [entry])

    def test_parent_of_active_mount_rejected(self):
        entry = MountEntry(str(self.mount), "AutoOrtho", "macfuse")
        with self.assertRaises(OSError):
            paths.guard_fuse_paths([str(self.root)], [entry])

    def test_mount_table_failure_is_not_treated_as_empty(self):
        with patch.object(paths, "read_mount_table", side_effect=OSError("denied")):
            with self.assertRaises(OSError):
                paths.prepare_mountpoint(str(self.mount))
        self.assertFalse(self.mount.exists())

    def test_any_mounted_volume_cannot_be_placeholder(self):
        entry = MountEntry(str(self.mount), "/dev/disk9", "apfs")
        with patch.object(paths, "read_mount_table", return_value=[entry]):
            with self.assertRaises(OSError):
                paths.restore_placeholder(str(self.mount))


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        mac_lifecycle._pending_unmounts.clear()
        self.entry = MountEntry("/tmp/osxp-test-mount", "OrthoStreamXP-test", "macfuse")
        self.canonical = patch.object(mac_lifecycle, "mount_path", side_effect=lambda p: p)
        self.canonical.start()
        self.addCleanup(self.canonical.stop)

    def test_unmounted_is_success_without_helper(self):
        with patch.object(mac_lifecycle, "find_mount", return_value=None), patch.object(mac_lifecycle.subprocess, "Popen") as spawn:
            self.assertTrue(mac_lifecycle.unmount_owned(self.entry.mountpoint, self.entry.source))
            spawn.assert_not_called()

    def test_wrong_identity_refused(self):
        with patch.object(mac_lifecycle, "find_mount", return_value=self.entry):
            with self.assertRaises(OSError):
                mac_lifecycle.unmount_owned(self.entry.mountpoint, "AutoOrtho")

    def test_normal_stop_does_not_force(self):
        process = Mock()
        process.wait.return_value = 0
        with patch.object(mac_lifecycle, "find_mount", side_effect=[self.entry, None]), patch.object(mac_lifecycle.subprocess, "Popen", return_value=process) as spawn:
            self.assertTrue(mac_lifecycle.unmount_owned(self.entry.mountpoint, self.entry.source))
            self.assertEqual(spawn.call_args.args[0], ["/sbin/umount", self.entry.mountpoint])

    def test_explicit_force(self):
        process = Mock()
        process.wait.return_value = 0
        with patch.object(mac_lifecycle, "find_mount", side_effect=[self.entry, None]), patch.object(mac_lifecycle.subprocess, "Popen", return_value=process) as spawn:
            self.assertTrue(mac_lifecycle.unmount_owned(self.entry.mountpoint, self.entry.source, force=True))
            self.assertIn("-f", spawn.call_args.args[0])

    def test_busy_mount_returns_false(self):
        process = Mock()
        process.wait.return_value = 1
        with patch.object(mac_lifecycle, "find_mount", return_value=self.entry), patch.object(mac_lifecycle.subprocess, "Popen", return_value=process):
            self.assertFalse(mac_lifecycle.unmount_owned(self.entry.mountpoint, self.entry.source))
            process.kill.assert_not_called()

    def test_timeout_never_waits_unboundedly_and_prevents_stacking_helpers(self):
        process = Mock()
        process.wait.side_effect = subprocess.TimeoutExpired("umount", 1)
        process.poll.return_value = None
        with patch.object(mac_lifecycle, "find_mount", return_value=self.entry), patch.object(mac_lifecycle.subprocess, "Popen", return_value=process) as spawn:
            self.assertFalse(mac_lifecycle.unmount_owned(self.entry.mountpoint, self.entry.source, timeout=0.01))
            self.assertFalse(mac_lifecycle.unmount_owned(self.entry.mountpoint, self.entry.source, timeout=0.01))
            self.assertEqual(process.wait.call_count, 1)
            process.kill.assert_called_once()
            spawn.assert_called_once()

    def test_darwin_cached_table_abi_layout(self):
        self.assertEqual(ctypes.sizeof(macos_mounts._StatFS), 2168)
        self.assertEqual(macos_mounts._StatFS.f_mntonname.offset, 88)

    def test_fuse_options_are_readonly_and_do_not_allow_other_users(self):
        options = mount_options("OrthoStreamXP-test", "test", False)
        self.assertTrue(options["ro"])
        self.assertTrue(options["foreground"])
        self.assertNotIn("allow_other", options)
        self.assertNotIn("direct_io", options)
        self.assertNotIn("kernel_cache", options)
        self.assertTrue(options["auto_cache"])

    def test_direct_io_is_only_opt_in(self):
        self.assertTrue(mount_options("OrthoStreamXP-test", "test", True)["direct_io"])

    def test_rosetta_and_other_platforms_rejected(self):
        for system, architecture in (("Darwin", "x86_64"), ("Linux", "arm64")):
            with patch("macfuse_runtime.platform.system", return_value=system), patch("macfuse_runtime.platform.machine", return_value=architecture):
                with self.assertRaises(OSError):
                    load_macfuse()


if __name__ == "__main__":
    unittest.main()
