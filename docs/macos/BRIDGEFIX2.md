# Bridgefix2: non-reboot connection-lifetime correction

## Status and scope

This patch fixes a reproduced connection leak in the OrthoStreamXP Python/.NET
bridge. It is not an AutoOrtho patch, macFUSE reinstall, driver reset, or proof
that every reported Errno 5 has the same cause. No dependency version changes.
Native validation of this correction is pending on the user's Apple Silicon Mac.
No Git commits, repository uploads, or PR submissions were performed.

The user's earlier terminal transcript demonstrated native ARM64 build/publish,
a loaded macFUSE driver, and an independent successful mount/read/unmount test.
The OrthoStreamXP synthetic test still returned EIO. Those user-reported results
are distinct from the Linux checks performed while preparing this patch.

## Reproduced fault

The original EngineClient stored persistent sockets in threading.local and also
retained them in a shared set until shutdown. ctypes callbacks from native
threads do not preserve that Python thread-local state between calls. The old
code therefore opened another socket on each callback and retained the old one.
EngineServer.cs has a 256-connection cap; once reached, it closes new connections.
The client translates that broken connection to EIO (Errno 5).

The callback behavior is documented by Python:
https://docs.python.org/3/library/ctypes.html#callback-functions

An actual C pthread invoking Python through ctypes reproduced the following in
Linux, with real AF_UNIX sockets and a simulated server limit (not the .NET
server or macFUSE driver):

| Client | Test | Result |
|---|---|---|
| Original | 300 callbacks, no server limit | 300 retained sockets from one native thread |
| Original | 600 attempted callbacks, 256-connection limit | EIO at callback 257 |
| Bridgefix2 | 600 callbacks, same simulated limit | All passed, one retained socket |

This demonstrates a real defect and a fix for that defect. The user's original
logs did not contain connection counts, so this does not prove it was the sole
cause of the first failing root or water_transition.png operation.

## Changes

- Replace thread-local connections with a bounded pool: 64 sockets by default,
  leased exclusively per in-flight request. Idle sockets can serve other threads.
- Keep the existing C# 256-connection limit and protocol. Handles remain engine
  owned, not tied to a particular connection. Concurrent readers stay concurrent.
- Discard a failed socket; never replay a failed open/read/release request. Only
  a later call may connect again. Shutdown wakes waiters and closes connections.
- Log RPC failures with operation, virtual path/handle, range and traceback,
  without logging image data or request bodies.
- Print the failing smoke-test phase and a bounded console-log tail automatically.
- Exercise 600 passthrough open/read/close cycles per smoke mount, rather than
  relying only on metadata lookups that a kernel cache could satisfy.
- Add pool, shutdown, native callback, no-replay and diagnostic regression tests.

Only two runtime Python files change: platform/macos/rpc.py and live_smoke.py.
The other changes are tests and documentation. C# code, donor source, package
versions, installed drivers, AutoOrtho files, real scenery and real caches are
unchanged. Rebuilding is required to copy the new bridge into the publish folder.

## Checks actually performed

Environment: Linux x86_64, CPython 3.13.5, a local C compiler. No macOS kernel,
macFUSE driver, .NET SDK or X-Plane instance is available here.

- 70 Python tests passed with no skips; all three native callback tests ran.
- Five consecutive complete Python-suite runs passed (350 test executions).
- The old and new clients were compared against the same native callback driver.
- The C helper is test-only and compiled in a temporary directory; it is not
  a shipped runtime dependency. With no existing compiler, those three optional
  native tests are explicitly skipped, not presented as passing.
- Python syntax and shell syntax are checked during package validation.
- Native macOS rebuild, integrated live smoke, X-Plane and long-flight behavior
  are NOT established by these sandbox tests.

Exact evidence: bridgefix2-python-tests.txt and bridgefix2-reproduction.txt.
Historical validation records are retained separately rather than overwritten.

## Apply to the existing source without rebooting

Close X-Plane and AutoOrtho. Stop any running OrthoStreamXP session normally
(Ctrl+C, after closing readers). Do not kill serving workers or unload macFUSE.
Download fix-orthostreamxp-macos-bridge.py to Downloads, then use:

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/Downloads/OrthoStreamXP-macos-arm64"
.venv-macos/bin/python "$HOME/Downloads/fix-orthostreamxp-macos-bridge.py" --root "$PWD"
if [[ -x "$HOME/.dotnet-orthostreamxp/dotnet" ]]; then
  export DOTNET_ROOT="$HOME/.dotnet-orthostreamxp"
  export PATH="$DOTNET_ROOT:$PATH"
fi
LOG="$PWD/macos-bridgefix2-$(date +%Y%m%d-%H%M%S).log"
echo "Combined log: $LOG"
bash scripts/build-macos.sh 2>&1 | tee "$LOG"
.venv-macos/bin/python -c 'from pathlib import Path; assert Path("platform/macos/rpc.py").read_bytes() == Path("artifacts/macos-arm64/bridge/rpc.py").read_bytes(), "Published bridge is stale"; print("Published bridge matches patched source")' 2>&1 | tee -a "$LOG"
bash scripts/run-macos.sh doctor 2>&1 | tee -a "$LOG"
for run in 1 2 3; do
  echo "Non-reboot smoke run $run of 3" | tee -a "$LOG"
  bash scripts/smoke-macos.sh --keep 2>&1 | tee -a "$LOG"
done
echo "All three smoke runs completed. Log: $LOG"
BASH
```

The block stops at the first failure. Each smoke invocation does two complete
mount/read/unmount cycles; three invocations test six cycles in the same boot.
There is no reboot, sudo, driver reload, broad process-kill or cache deletion.
The scratch files are retained deliberately for diagnosis.

Require both PASS mount/read/unmount cycle messages and the final native smoke
PASS from every invocation. Compilation alone does not meet this gate. On
failure, send the combined log; the new output includes the failed phase and
local console tail. Do not repeat imports, delete scenery or reset the driver
just because the integrated test still fails. A busy mount is preserved for
safe recovery, not force-killed by the test supervisor.

## Installer and rollback

The installer uses only Python's standard library. It verifies known original
and pathfix1 source hashes before any edits, refuses unexpected changes and
symlinked targets, and makes a timestamped backup under
.orthostreamxp-backups/ before replacing files. A repeat invocation is a no-op.
Use --check for a preflight without writes. It prints the exact backup path.

For rollback, use the same installer with --root and --restore followed by that
printed backup path. Rollback refuses files edited after this patch. Rebuild
before running again, because rollback restores source, not a live process or
published executable. It does not remove an earlier pathfix1 correction.

## Next milestone

Six successful cycles in the same boot are evidence that this workload can be
mounted, read and shut down repeatedly without a reboot. They are not proof of
long-flight stability or a fix for AutoOrtho's separate multiprocessing failure.
Only after this gate passes should a disposable real-scenery test begin.
