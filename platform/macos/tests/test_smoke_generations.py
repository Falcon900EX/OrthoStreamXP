# SPDX-License-Identifier: GPL-3.0-only
"""Version-aware probe regressions on ordinary local files, not a FUSE mount.

Fault injection proves that accepting a changing initial snapshot does NOT
accept stale mounted bytes or skip the full-file mmap check.
"""
from contextlib import redirect_stdout
import io
import mmap
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_smoke import (expected_dds_length, reference_dds, require_bytes,
                        stable_reads, streamed_dds_probe, validate_dds)


def dds_fixture() -> bytes:
    content = bytearray((i * 37 + 11) % 251 for i in range(expected_dds_length()))
    content[:128] = bytes(128)
    content[:4] = b"DDS "
    struct.pack_into("<I", content, 4, 124)
    struct.pack_into("<II", content, 12, 512, 512)
    struct.pack_into("<I", content, 28, 10)
    content[84:88] = b"DXT1"
    return bytes(content)


class SmokeGenerationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="osxp-smoke-generation-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.texture = self.root / "mount.dds"
        self.cache = self.root / "cache.dds"
        self.final = dds_fixture()
        self.cache.write_bytes(self.final)
        self.texture.write_bytes(self.final)
        self.output = io.StringIO()
        self.redirect = redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def test_stable_file_passes_whole_file_and_both_descriptors(self):
        streamed_dds_probe(self.texture, self.cache)
        text = self.output.getvalue()
        self.assertIn("original DDS descriptor after generation: full-file memory-mapped", text)
        self.assertIn("reopened DDS descriptor: full-file memory-mapped", text)
        self.assertNotIn("NOTE:", text)

    def cold_snapshot(self):
        old = bytearray(self.final)
        old[128:65536] = b"\x7f" * (65536 - 128)
        return bytes(old)

    def initial_read_patch(self):
        target, snapshot = self.texture, self.cold_snapshot()
        original_open = Path.open
        used = False

        class InitialSnapshot:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def fileno(self):
                return self.stream.fileno()

            def read(self):
                self.stream.read()
                return snapshot

        def open_with_initial_snapshot(path, *args, **kwargs):
            nonlocal used
            stream = original_open(path, *args, **kwargs)
            if path == target and not used:
                used = True
                return InitialSnapshot(stream)
            return stream

        return patch.object(Path, "open", open_with_initial_snapshot)

    def test_initial_streaming_snapshot_can_change_before_completed_generation(self):
        with self.initial_read_patch():
            streamed_dds_probe(self.texture, self.cache)
        self.assertIn("initial streaming snapshot differs", self.output.getvalue())
        self.assertIn("reopened DDS descriptor: full-file memory-mapped", self.output.getvalue())

    def test_original_exact_comparison_reproduces_false_failure_on_transition(self):
        # The old assertion compares snapshots from different generations.
        with self.texture.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
            self.assertNotEqual(self.cold_snapshot()[128:4096], mapped[128:4096])
        # That difference alone does not show corruption; mapped bytes match
        # the independently completed file. New checks must still verify them.
        with self.initial_read_patch():
            streamed_dds_probe(self.texture, self.cache)

    def test_stale_original_handle_is_not_hidden_by_reopening(self):
        self.texture.write_bytes(self.cold_snapshot())
        with self.assertRaisesRegex(AssertionError, "original DDS descriptor after generation regular read: data mismatch"):
            streamed_dds_probe(self.texture, self.cache)
        self.assertNotIn("reopened DDS descriptor", self.output.getvalue())

    def test_corrupt_mmap_fails_even_when_initial_generation_changed(self):
        corrupt = bytearray(self.final)
        corrupt[160000] ^= 1  # well beyond the old 4096-byte prefix assertion
        with self.initial_read_patch(), patch("live_smoke.mmap.mmap", return_value=memoryview(corrupt)):
            with self.assertRaisesRegex(AssertionError, "mmap: data mismatch at file offset 160000"):
                streamed_dds_probe(self.texture, self.cache)

    def test_reopened_descriptor_is_checked_not_just_original(self):
        original = Path.open
        opens = 0

        def tracked(path, *args, **kwargs):
            nonlocal opens
            if path == self.texture:
                opens += 1
                if opens == 2:
                    with original(path, "wb") as output:
                        output.write(self.cold_snapshot())
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", tracked):
            with self.assertRaisesRegex(AssertionError, "reopened DDS descriptor regular read: data mismatch"):
                streamed_dds_probe(self.texture, self.cache)

    def test_cache_reference_must_remain_unchanged(self):
        original = Path.read_bytes
        reads = 0

        def changed_reference(path):
            nonlocal reads
            data = original(path)
            if path == self.cache:
                reads += 1
                if reads == 2:
                    data = self.cold_snapshot()
            return data

        with patch.object(Path, "read_bytes", changed_reference):
            with self.assertRaisesRegex(AssertionError, "completed cache remained unchanged: data mismatch"):
                streamed_dds_probe(self.texture, self.cache)

    def test_persistent_mismatch_is_not_retried_until_it_passes(self):
        self.texture.write_bytes(self.cold_snapshot())
        with patch("live_smoke.time.sleep") as sleep:
            with self.assertRaises(AssertionError):
                streamed_dds_probe(self.texture, self.cache)
        sleep.assert_not_called()

    def test_missing_completed_cache_has_a_deadline(self):
        with patch("live_smoke.time.sleep") as sleep:
            with self.assertRaisesRegex(TimeoutError, "No completed synthetic DDS"):
                reference_dds(self.root / "missing.dds", timeout=0)
        sleep.assert_not_called()

    def test_wait_is_for_atomic_publication_not_for_byte_equality(self):
        original = Path.read_bytes
        calls = 0

        def delayed(path):
            nonlocal calls
            if path == self.cache:
                calls += 1
                if calls == 1:
                    raise FileNotFoundError(path)
            return original(path)

        with patch.object(Path, "read_bytes", delayed), patch("live_smoke.time.sleep") as sleep:
            self.assertEqual(reference_dds(self.cache), self.final)
        sleep.assert_called_once_with(0.05)

    def test_bad_reference_is_not_treated_as_an_incomplete_build(self):
        self.cache.write_bytes(self.final[:-1])
        with patch("live_smoke.time.sleep") as sleep:
            with self.assertRaisesRegex(AssertionError, "DDS length"):
                reference_dds(self.cache)
        sleep.assert_not_called()

    def test_wrong_format_and_mip_count_are_rejected(self):
        for offset, value in ((84, b"DXT5"), (28, struct.pack("<I", 9)), (12, struct.pack("<I", 256))):
            with self.subTest(offset=offset):
                data = bytearray(self.final)
                data[offset:offset + len(value)] = value
                with self.assertRaises(AssertionError):
                    validate_dds(bytes(data), "test fixture")

    def test_byte_diagnostic_includes_file_offset_lengths_and_hashes(self):
        with self.assertRaisesRegex(AssertionError, "file offset 101") as caught:
            require_bytes(b"abz", b"acz", "random read", offset=100)
        message = str(caught.exception)
        self.assertIn("sha256 actual=", message)
        self.assertIn("lengths actual=3 expected=3", message)

    def test_immutable_regular_and_full_mmap_reads_match_the_reference(self):
        with self.texture.open("rb") as stream:
            stable_reads(stream, self.final, "immutable")
        self.assertIn("immutable: full-file memory-mapped read", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
