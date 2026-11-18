# macOS port validation record

## Delivery status

This is an implemented **development source port**. A user-provided bridgefix2
log now confirms successful native ARM64 build/publish and 268 passing .NET
tests. It is not a signed or flight-tested macOS release. The integrated test
reached mmap comparison and failed; the revised probe is still awaiting native
validation. A Python suite pass alone does not establish macFUSE or X-Plane
correctness.

Base: user-supplied OrthoStreamXP 1.3.1 source archive. Donor: AutoOrtho Continued
3.1.0, commit `9cf46158913c994013250a1844d732249ef49f8e`, checked November 14,
2026. See `platform/macos/donor/SOURCES.json` for source URLs and adapted-file
hashes. No external fork, commit, upload or PR was created.

## Current smokefix3 results and user-side native progress

See [SMOKEFIX3.md](SMOKEFIX3.md) for the version-aware, independent-reference
smoke probe and its remaining limitations. All 84 current Python tests passed
on Linux without skips; 14 new tests cover generation transitions, full-file
mmap corruption, stale original handles, reopened handles and reference errors.
This changes tests, not runtime behavior, and does not require a .NET rebuild.

The uploaded bridgefix2 native log confirms 70 Python tests and 268 .NET tests
passed, no build warnings/errors, successful ARM64 publication, and matching
published bridge source. Its first smoke run passed 600 passthrough reopens,
terrain, initial streamed DDS/header and concurrent random reads. It failed at
`mmap data mismatch`. The prior passthrough EIO did not recur in this run.
The next two requested smoke runs did not execute, so six same-boot
mount/read/unmount cycles are NOT yet confirmed.

The old probe compared a later mmap with the first possibly temporary streaming
snapshot. The new probe checks both against an independently completed cache
file after generation. It still fails stale/corrupt mounted bytes; the earlier
log alone cannot distinguish a valid generation transition from an actual
cache-coherency defect. Revised native smoke and X-Plane flights remain pending.

[BRIDGEFIX2.md](BRIDGEFIX2.md) records the earlier callback connection leak and
Linux reproduction. Historical records below describe earlier source states.

## Path normalization correction (pathfix1)

The first supplied native build log reached the Python suite but stopped with
49 tests passing and `test_valid_separate_paths` failing: validation returned
`/private/var/...`, while the assertion expected `/var/...`. No .NET compilation
or real mount was reached in that log.

`validate_layout` already intentionally returns canonical paths. The correction
changes only the expected paths in the test and adds an explicit symlink-parent
regression. Runtime path validation and mount safety checks are unchanged.

On Linux x86_64 / Python 3.13.5, the original 50-test suite passed with an ordinary
temporary directory and reproduced exactly one failure with a symlinked
`TMPDIR`. After the correction, all 51 tests passed in both configurations.
See `python-tests.txt` and `python-tests-symlink-parent.txt`. This is portable
regression testing, not a native macOS build or live macFUSE test. Native
compilation and mount/flight validation remain pending.

## Historical pathfix1 sandbox checks

Environment: Linux x86_64, Python 3.13.5. No .NET SDK, macOS kernel, macFUSE driver
or X-Plane executable was available in the implementation environment.

| Check | Result | Scope |
|---|---|---|
| Python bridge unit tests | **51 passed** | Real local Unix sockets plus mocked FUSE operations, mount tables and unmount subprocesses |
| Python syntax | **Passed**, 10 files | AST parsing; no native library execution |
| Shell syntax | **Passed**, 3 scripts | `bash -n`, not the native build script execution |
| New .NET project/solution XML | **Passed**, 4 files | XML and referenced source/content paths, not C# type checking |
| Shared engine source selection | **Passed**, 104 linked files | No duplicate links or direct WPF/WinFsp imports; not a proof of complete portability |
| Donor adapted-file hashes | **Passed**, 4 files | Local bytes match the provenance manifest |
| macOS CI workflow structure | **Passed** | Parsed YAML and ARM64 runner configuration; CI has not run |
| .NET restore/build/tests/publish | **Not run** | Requires native .NET 10 validation |
| Real macFUSE synthetic mount | **Not run** | Requires the user's Apple Silicon Mac and approved macFUSE installation |
| X-Plane scenery/flight tests | **Not run** | Includes texture refresh, performance and stability |
| Windows regression suite | **Not run** | Retaining the adapter does not establish compatibility |

Exact transcripts are in `python-tests.txt` and `structural-checks.txt` beside
this document. Reproduce the portable suite with:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s platform/macos/tests -v
bash -n scripts/build-macos.sh
bash -n scripts/run-macos.sh
bash -n scripts/smoke-macos.sh
```

The tests cover socket fragmentation, raw binary payloads, protocol limits,
concurrent connections, disconnect handling without replay, read-only callbacks,
short/large reads, directory pagination, path overlap and ownership guards,
bounded unmount helpers, and preservation of busy serving workers. Native FUSE
callback dispatch and .NET/Python interoperability still need the native tests.

## Gate 1: native build and engine tests

Use a native ARM64 terminal, .NET 10 SDK, ARM64 Python 3.10+ and your existing
macFUSE installation. Do not run under Rosetta or with `sudo`.

```bash
bash scripts/build-macos.sh
bash scripts/run-macos.sh doctor
```

The build script runs all portable Python tests, restores/builds the Mac solution,
runs selected upstream engine tests plus new adapter and shutdown tests, and
publishes a self-contained `osx-arm64` executable. Warnings remain errors. Resolve
any compile, analyzer, package or native-library failures before continuing;
do not bypass failing tests to make the build green.

For a shareable build log, use `bash` with `set -o pipefail` before piping to
`tee`, or redirect output to a file and preserve the command's exit status.
The `doctor` command checks binding loading and cached mount-table access, not
successful driver approval or a working mount.

## Gate 2: isolated synthetic mount

Close X-Plane and AutoOrtho. This test uses synthetic imagery, scratch backing
files, scratch cache and separate application data; it does not need your real
mesh or online imagery.

```bash
bash scripts/smoke-macos.sh --keep
```

Require both mount/unmount cycles to pass: valid DDS size/header, terrain
references, passthrough bytes, EOF, 100 concurrent offset reads, memory-mapped
reads and rejected writable opens. Keep the reported scratch log directory.
A timeout or busy mount is a failure to investigate, not permission to kill the
serving worker or delete its mountpoint. Close readers and use the documented
owned-mount recovery commands. Confirm detachment in `mounts` before cleanup.

## Gate 3: real scenery and lifecycle

Use a small existing **backing** mesh package, not an active AutoOrtho mount.
Configure a separate cache and this port's new mount name. Back up
`scenery_packs.ini` and avoid duplicate active mesh entries for the same region.
Keep required overlays above the mesh. See the root `README-macOS.md`.

Validate an ordinary shutdown after X-Plane exits, a deliberate busy-unmount
retry using only disposable test data, repeated remounts, failed startup and
parent-exit recovery. Do not begin crash/recovery tests against valuable live
scenery sessions. Confirm that the source package and AutoOrtho's configuration,
cache and mounts have not been changed.

## Gate 4: flight behaviour and performance

Record a cold-cache flight followed by a warm-cache flight, scenery reloads,
low-detail-to-real-texture replacement, and the same area at different altitudes.
Check X-Plane's log for missing terrain, masks, DDS failures and I/O errors. Test
multiple packages, long flights, memory pressure and interrupted imagery access
before proposing a non-draft release.

The default enables macFUSE `auto_cache` with dynamic streamed-file modification
times. It does not implement proactive invalidation notifications. Automatic
metadata-based refresh is not proof that already-mapped pages or GPU textures
refresh correctly. `--direct-io` is only a diagnostic because it can disable
memory mapping; do not declare it a general fix without X-Plane evidence.

Measure simulator stalls, read latency, memory and throughput. The Python/.NET
IPC design has not been benchmarked against native AutoOrtho or Windows WinFsp.
The port deliberately reports Windows-only system/VRAM metrics as unavailable.

## Evidence for a draft PR

Attach reviewed build/test results, exact hardware/macOS/macFUSE/.NET/Python and
X-Plane versions, the synthetic test result, flight conditions and known issues.
Review paths and logs for private information before sharing. Do not upload
imagery cache contents. Use `PR_DESCRIPTION.md` as a draft, not proof of merge
readiness. Rebase onto the maintainer's current branch and resolve any drift from
the supplied 1.3.1 archive before submitting.
