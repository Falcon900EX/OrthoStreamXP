# Draft PR: Apple Silicon macFUSE command-line host

## Status

**Draft / integrated native validation pending. Do not present as flight-tested.**
Base reviewed: supplied OrthoStreamXP 1.3.1 archive. Apply/rebase against the
maintainer's current branch and resolve changes there before submission.
No repository was uploaded, no commit was made, and no PR was opened when this
source package was prepared.

## Summary

Adds a native `osx-arm64` command-line host and read-only macFUSE bridge while
reusing the existing streaming/texture engine. The WPF/WinFsp application is
outside the Mac solution. The Windows adapter was retained at low cost, but
Windows verification is not asserted.

- Shared engine project and native mount interface; POSIX path joins and OS guards.
- Per-package mfusepy process backed by a private Unix-socket .NET server.
- Offset reads, pinned handles, in-memory terrain, passthrough masks, directory
  paging and existing colour/night/streaming read logic.
- Owned mountpoint validation, isolated defaults, bounded unmount helpers and
  retryable shutdown, including partial startup.
- CLI configuration, diagnostics, build/launch scripts, synthetic native smoke
  test, portable Python tests, reused engine tests, new .NET adapter tests,
  ARM64 CI, donor attribution and developer documentation.

Donor is AutoOrtho Continued 3.1.0 commit
`9cf46158913c994013250a1844d732249ef49f8e` under Apache-2.0. It contributes Darwin
mount-table/lifecycle code and the FUSE binding choice, not its texture generator.

## Evidence and merge gates

- [x] 84 current Python tests passed in Linux without skips, including native-thread callback and smoke-generation tests. Historical bridgefix2/pathfix1 evidence is retained.
- [x] Python syntax and shell syntax checks passed.
- [x] Uploaded bridgefix2 log confirms native .NET 10 ARM64 build/publish, zero warnings/errors and matching published bridge source.
- [x] All 268 selected engine/adapter .NET tests passed on ARM64 with zero skips.
- [x] Native probe passed metadata, 600 repeated passthrough opens, terrain, initial DDS/header, EOF and concurrent random reads before failing its mmap comparison.
- [ ] Validate smokefix3 independent-cache/full-file mmap checks on native macOS; current log is not an end-to-end smoke pass.
- [ ] Native synthetic smoke test passes both mount cycles, including mmap.
- [ ] Clean Ctrl+C, busy mount retry, parent exit, startup failure and remount.
- [ ] Cold-cache and warm-cache flights, scenery reload and stand-in refresh.
- [ ] Multiple-region and long-flight memory/throughput validation.
- [ ] Review donor/license inventory and binary redistribution requirements.
- [ ] Review/rebase against current upstream; Windows CI if upstream requires it.

## Bridgefix2 stability correction

Replaces callback-thread-local sockets with a bounded per-worker pool, preserves
no-replay semantics, and adds failure logging plus repeated passthrough opens to
the live smoke test. The C-thread regression reproduces an old connection leak
and EIO at callback 257 under a simulated server limit. The fixed client passes
600 callbacks using one connection. The user's bridgefix2 run then passed the
previously failing passthrough phase and 600 repeated opens, but stopped later
at mmap data comparison. This is not presented as an AutoOrtho multiprocessing
fix or a complete native stability pass.

## Smokefix3 probe correction

The initial streamed header chunk can contain temporary pixels; it is not an
immutable reference for a later mmap. The revised probe waits for completed DDS
cache publication, then compares full conventional/concurrent reads and full-file
mmap on the original and reopened descriptors against that independent file. An
immutable passthrough mmap check and 14 regressions ensure stale or corrupt data
still fails. See `SMOKEFIX3.md`. Production code and mount options are unchanged;
this is not proof that the user's mmap failure was benign.

## Deliberate limits / review points

No GUI, macFUSE installer, `.app` signing/notarization, auto-updater or mesh
installer. No direct FUSE-T implementation. No Windows hardware telemetry
pretending to represent Apple unified memory. Python/.NET IPC is not benchmarked.
macFUSE automatic cache refresh is enabled, but active mapped-page invalidation
is not implemented and requires explicit flight evidence. Donor lifecycle changes
must be validated on the tester's installed macFUSE version.

## How to reproduce

See `README-macOS.md`. From a native Apple Silicon terminal:

```bash
bash scripts/build-macos.sh
bash scripts/run-macos.sh doctor
bash scripts/smoke-macos.sh --keep
```

Record hardware, OS, macFUSE, Python and .NET versions with the logs. Do not attach
personal configuration paths, access credentials, private log details or imagery
cache contents to a public PR without reviewing them first.
