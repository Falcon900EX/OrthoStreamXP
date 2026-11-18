# SPDX-License-Identifier: GPL-3.0-only
"""Diagnostic rendering only. These tests do not perform a FUSE mount."""
from contextlib import redirect_stderr, redirect_stdout
import errno
import io
import struct
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_smoke import (IMMUTABLE, PASSTHROUGH, STEM, expected_dds_length,
                        print_log_tail, probe)


class SmokeDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="osxp-smoke-log-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_log_tail_shows_last_80_lines(self):
        path = self.root / "console.log"
        path.write_text("\n".join(f"LINE {i:03d}" for i in range(100)))
        output = io.StringIO()
        with redirect_stderr(output):
            print_log_tail(path)
        self.assertIn("LINE 099", output.getvalue())
        self.assertIn("LINE 020", output.getvalue())
        self.assertNotIn("LINE 019", output.getvalue())

    def test_log_tail_is_byte_bounded_and_tolerates_partial_unicode(self):
        path = self.root / "console.log"
        path.write_bytes(b"OLD-MARKER" + b"x" * 70000 + b"\xff\nFINAL-MARKER\n")
        output = io.StringIO()
        with redirect_stderr(output):
            print_log_tail(path)
        self.assertIn("FINAL-MARKER", output.getvalue())
        self.assertNotIn("OLD-MARKER", output.getvalue())
        self.assertLess(len(output.getvalue()), 66000)

    def test_missing_log_does_not_mask_original_failure(self):
        output = io.StringIO()
        with redirect_stderr(output):
            print_log_tail(self.root / "missing.log")
        self.assertIn("Could not read diagnostic log", output.getvalue())

    def test_probe_performs_600_reopens_not_just_cached_metadata(self):
        (self.root / "textures").mkdir()
        (self.root / "terrain").mkdir()
        (self.root / "fixtures").mkdir()
        (self.root / "fixtures" / "reference.bin").write_bytes(IMMUTABLE)
        mask = self.root / "textures" / "water_transition.png"
        mask.write_bytes(PASSTHROUGH)
        (self.root / "terrain" / (STEM + ".ter")).write_text(
            f"BASE_TEX_NOWRAP ../textures/{STEM}.dds\n")
        content = bytearray(expected_dds_length())
        content[:4] = b"DDS "
        struct.pack_into("<II", content, 12, 512, 512)
        struct.pack_into("<I", content, 28, 10)
        content[84:88] = b"DXT1"
        cache_file = self.root / "completed-cache.dds"
        cache_file.write_bytes(content)
        (self.root / "textures" / (STEM + ".dds")).write_bytes(content)
        reads = []
        original_read = Path.read_bytes
        def tracked_read(path):
            if path == mask:
                reads.append(path)
            return original_read(path)
        output = io.StringIO()
        # Only the final writable open uses os.open. Simulate its rejection;
        # this is a probe-harness regression, not evidence of a FUSE mount.
        with patch.object(Path, "read_bytes", tracked_read), \
             patch("live_smoke.os.open", side_effect=OSError(errno.EROFS, "Read only")), \
             redirect_stdout(output):
            probe(self.root, cache_file)
        self.assertEqual(len(reads), 601)
        self.assertIn("600 repeated opens", output.getvalue())
        self.assertIn("original DDS descriptor after generation: full-file memory-mapped", output.getvalue())
        self.assertIn("reopened DDS descriptor: full-file memory-mapped", output.getvalue())

    def test_probe_announces_metadata_phase_before_file_error(self):
        (self.root / "textures").mkdir()
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(FileNotFoundError):
            probe(self.root, self.root / "unused-cache.dds")
        self.assertIn("PROBE: root metadata", output.getvalue())
        self.assertIn("PROBE: passthrough file metadata", output.getvalue())
        self.assertNotIn("PROBE: passthrough file open/read", output.getvalue())


if __name__ == "__main__":
    unittest.main()
