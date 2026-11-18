# Smokefix3: distinguish streaming generations from mmap corruption

## Status and scope

This is a correction and strengthening of the **live smoke-test harness**,
not another runtime bridge or driver fix. It builds on bridgefix2. No engine,
IPC, FUSE callbacks, mount options, scenery, cache policy, or AutoOrtho files
are changed. It introduces no dependency. The live smoke script runs from
`platform/macos/live_smoke.py` in the source tree; a .NET rebuild is unnecessary
when applying only this update to an already published bridgefix2 build.

The user-provided `macos-bridgefix2-20261115-105252.log` shows:

- 70 Python tests passed, including the native-callback socket-pool regressions.
- Native ARM64 build succeeded with zero warnings/errors; 268 .NET tests passed,
  zero failed or skipped; the published bridge matched the patched source.
- The first integrated run passed metadata, passthrough, 600 repeated opens,
  terrain, initial DDS/header, EOF and 100 concurrent random-read checks.
- It failed at `assert mapped[128:4096] == content[128:4096]` with
  `AssertionError: mmap data mismatch`, not the previous passthrough EIO.
- The supervisor stopped at the first run. Neither the requested six complete
  mount/unmount cycles nor X-Plane operation is established by that log.

Environment reported by that log: .NET 10.0.15, Python 3.14.6, macOS 26.7.2,
Arm64. These are user-side results, not tests performed in the Linux sandbox.
They do not establish that AutoOrtho's separate startup/manager failure is fixed.

## Why the old assertion is insufficient

`TextureStreamer.ReadLevels` intentionally answers an initial header chunk
without waiting for imagery, using a flat/stand-in level when necessary. It does
not pin that header chunk's level-0 version; subsequent reads can trigger full
resolution generation. See `src/OrthoStreamXP/Scenery/Streaming/ReadPattern.cs`
(`IsHeaderChunk`) and `TextureStreamer.cs` (`ReadLevels` and `LevelStart`). The
upstream `StreamingTests.Level_0_read_on_from_the_header_chunk_waits_for_full_detail_and_stays_real`
and `The_header_chunk_gets_the_first_rows_of_level_0s_stand_in_without_making_the_whole_level`
exercise this behavior.

Consequently, bytes copied by the first `read()` are not necessarily a valid,
immutable reference for a later mmap. A difference can mean either legitimate
imagery completion or a real stale/corrupted page. The failed user log has no
byte hashes, independent reference comparison or modification-time trace to
choose between them. **A generation transition is a plausible explanation, not
a confirmed diagnosis of the particular Mac failure.**

## What the revised probe requires

1. Preserve all prior passthrough, 600-reopen, terrain, EOF and read-only checks.
2. Test conventional/concurrent reads and the entire mmap of a deterministic
   immutable passthrough fixture spanning both 16 KiB and 64 KiB boundaries.
3. Keep mip streaming enabled. Validate the initial DDS structure and log its
   hash plus the descriptor's modification times.
4. Wait, with a deadline, for the atomically published complete DDS in the test's
   private backing cache outside the FUSE mount. Do not wait for mismatched
   bytes to eventually match. Invalid completed files fail immediately.
5. Compare full conventional reads, 100 concurrent random reads, **every byte of
   the mmap**, and post-mmap reads with that independent cache reference.
   Perform these checks on both the original descriptor after generation and a
   newly opened descriptor. Stale original handles cannot be hidden by reopening.
6. Verify the completed cache stayed unchanged. On any mismatch, report the
   exact offset, lengths, SHA-256 hashes and differing byte samples and fail.
7. Still require two mount/read/unmount cycles per invocation. Three invocations
   are the proposed six-cycle same-boot stability gate, not an achieved result.

The isolated configuration explicitly disables colour matching, seasons and
night lights and sets neutral exposure so a direct comparison with the completed
DDS is meaningful. No real scenery or downloaded imagery is used.

The test retains normal cached/mmap I/O. It does not enable `direct_io`, turn off
mip streaming, flush macOS caches, remount to conceal a read failure, change the
driver, ignore bad data, or skip the mmap assertion. A descriptor metadata query
exercises the existing `auto_cache` behavior; active invalidation is not added.
Official macFUSE documentation says `auto_cache` detects modification-time
changes during getattr/open; it also documents that direct I/O disables mmap:
https://github.com/macfuse/macfuse/wiki/Mount-Options

This does **not** prove that an already-created mapping updates automatically,
that a page fault triggers sufficient metadata refresh in X-Plane, or that GPU
textures refresh. These remain flight/native integration gates.

## Local validation

All **84 Python tests passed without skips** on Linux x86_64 / Python 3.13.5.
The 14 new regressions use ordinary local files and explicit fault injection,
not macFUSE. They demonstrate that:

- A changed initial snapshot can be distinguished from a stable completed file.
- The old exact-prefix comparison can fail solely because snapshots differ.
- Persistent stale bytes on the original descriptor still fail immediately.
- A corrupt mmap byte far beyond the old 4096-byte prefix still fails.
- Reopened-handle corruption, changes to the reference file, invalid DDS
  structure and missing-cache timeouts are not accepted or retried into success.

See `smokefix3-python-tests.txt` for the current suite transcript. No .NET or
macFUSE execution was possible in this sandbox; .NET source is unchanged.
**The revised integrated smoke test must still pass on the user's Mac.**

## Apply and reproduce

Use the hash-checked `fix-orthostreamxp-macos-smoke.py` installer on the existing
bridgefix2 source tree, or review/apply `OrthoStreamXP-macos-smokefix3.patch`.
The installer supports `--check`, creates a backup before applying, and supports
`--restore BACKUP_DIRECTORY`. It refuses unexpected modifications or symlinks in
managed target paths. No process is stopped or killed by the installer.

From the source root, with AutoOrtho and X-Plane closed and any previous port
session stopped normally:

```bash
.venv-macos/bin/python -m unittest discover -s platform/macos/tests -v
bash scripts/smoke-macos.sh --keep
```

Stop on the first failure. Preserve the combined output and the reported scratch
folder. This harness fix does not require a reboot. A before/after-reboot
comparison is a useful fallback diagnostic, but it is not proof that the source
is correct or a substitute for the six-cycle same-boot test.

## Review inventory

Changed executable/test files:

- `platform/macos/live_smoke.py`
- `platform/macos/tests/test_smoke_diagnostics.py`
- `platform/macos/tests/test_smoke_generations.py` (new)

Documentation: this note, the current validation/PR records and a sanitized
local unit-test transcript. No user-provided log or personal scenery paths are
included in the source distribution. No repository commit, upload or PR was made.
