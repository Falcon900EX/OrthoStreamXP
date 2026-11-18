# OrthoStreamXP: Apple Silicon development port

**Source implementation for local validation, not a tested or signed macOS release.**
The Python tests were executed in a Linux sandbox; .NET compilation, the macFUSE
mount test and an X-Plane flight were not executable there. See
[validation status](docs/macos/VALIDATION.md) before treating this as merge-ready.

Base: the supplied OrthoStreamXP **1.3.1** source archive. Target: native
**osx-arm64**. Donor: AutoOrtho Continued **3.1.0**, release commit
`9cf46158913c994013250a1844d732249ef49f8e`. This pins the latest published release
checked on November 14, 2026, not the moving `develop` branch.

## Current correction: bridgefix2

The tester has reported successful native ARM64 build/publish and an independent
macFUSE read/unmount test. Integrated OrthoStreamXP reads still need validation.
[Bridgefix2](docs/macos/BRIDGEFIX2.md) corrects a reproduced native-callback socket
leak and adds a same-boot repeated-mount test procedure. It does not modify
AutoOrtho or reinstall/reset macFUSE. The correction's native result is pending.

## What this is

X-Plane reads a read-only macFUSE scenery mount. A small Python `mfusepy` worker
forwards filesystem operations over a private Unix socket to the existing .NET
OrthoStreamXP engine. The engine still generates DDS mip levels, synthesizes
terrain definitions, reads PNG masks, applies colour/season transforms, manages
its cache and tracks aircraft UDP data. AutoOrtho does not generate the imagery
in this design; its macOS mount-table and shutdown code are the donor parts.

This first port is a **terminal application**, not a replacement WPF GUI. There
is no `.app` bundle, installer, notarization, GUI package downloader or automatic
update mechanism. Existing source/mesh packages must already be present. The
original Windows project remains separate because keeping it was inexpensive;
macOS does not compile or load WPF, WinFsp, Windows Forms or Velopack.

## Prerequisites

Use a native Apple Silicon Terminal. You need your existing, approved macFUSE
installation, an ARM64 .NET **10 SDK**, and ARM64 Python **3.10 or newer** with
`venv` and `pip`. Use a macOS version supported by those installations.
The build installs only the hash-pinned Python binding into a repository-local
virtual environment. It does **not** install/reconfigure macFUSE or change system
security settings. It restores the project's NuGet packages from the network.

```bash
uname -m            # arm64, not x86_64
dotnet --version    # 10.x
python3 -c 'import platform; print(platform.machine())'  # arm64
```

Do not use `sudo` to build or run. Do not run the packaged AutoOrtho app alongside
this port against the same scenery while validating.

## Build and test first

From the extracted source directory:

```bash
bash scripts/build-macos.sh
bash scripts/run-macos.sh doctor
bash scripts/smoke-macos.sh --keep
```

The build performs Python unit tests, .NET restore/build/unit tests and a
self-contained `osx-arm64` publish to `artifacts/macos-arm64`. It stops on any
failed step and retains the upstream warnings-as-errors policy. The published
engine includes its .NET runtime, but still needs the Python virtual environment
and installed macFUSE. Keep the source directory and `.venv-macos` together when
using the launcher. Moving a Python venv is not a supported distribution method;
recreate it at its new location. No separate .NET runtime is required for a
successful self-contained publish.

`doctor` checks library loading, architecture and the cached mount table. It is
**not** evidence that the kernel extension is approved or that mounts work.

The opt-in smoke test uses synthetic imagery and fresh scratch folders; it does
not open X-Plane or use your scenery/config/cache. It tests DDS headers and sizes,
terrain references, passthrough files, EOF, concurrent random reads, memory-mapped
reads, read-only protection, clean unmount and a second mount/unmount cycle. Its
app-data/log paths are isolated too. `--keep` retains logs. On a busy or uncertain
mount it leaves the serving process and scratch files alone instead of killing
the worker or recursively deleting a mountpoint. Inspect the reported log path.

For a binding not found in the normal locations, identify your actual installed
macFUSE FUSE-2 compatibility library, then set, for example:

```bash
export FUSE_LIBRARY_PATH="/usr/local/lib/libfuse.2.dylib"
bash scripts/run-macos.sh doctor
```

The override must point to **macFUSE**, not FUSE-T. A wrong-architecture library
fails loading. The port intentionally does not silently switch filesystem stacks.

## Configure an existing mesh safely

**Stop X-Plane and AutoOrtho first.** Use the real backing scenery package that
contains `Earth nav data`, not an active AutoOrtho mount. Consult your AutoOrtho
configuration for its backing mesh directory; layouts differ. Nothing here needs
to alter AutoOrtho's `.app`, configuration, source mesh or cache.

For one backing package:

```bash
bash scripts/run-macos.sh init \
  --source "/absolute/path/to/backing-mesh-package" \
  --xplane "/absolute/path/to/X-Plane 12"

bash scripts/run-macos.sh check
bash scripts/run-macos.sh run
```

For a parent directory containing several real mesh packages, replace `--source`
with `--packages "/absolute/path/to/mesh-parent"`. Avoid choosing all of `Custom
Scenery`, which may also contain airports/overlays that should not be streamed.
The check rejects an active FUSE/NFS subtree before enumerating it, and rejects
nested source/cache/mount paths, symlink mount targets, and nonempty mountpoints.
Backings inside `Custom Scenery` are allowed only when the new mount is separate.

Defaults:

| Item | Location / behaviour |
|---|---|
| INI | `~/Library/Application Support/OrthoStreamXP/orthostreamxp-mac.ini` |
| Logs | `~/Library/Application Support/OrthoStreamXP/logs` |
| Flight/colour history | `~/Library/Application Support/OrthoStreamXP/history` |
| New cache | `~/Library/Caches/OrthoStreamXP` |
| Virtual package | `X-Plane 12/Custom Scenery/z_OrthoStreamXP_<source-name>` |
| Initial imagery | `bing,esri`; `level-change = -1` |
| Scenery ordering edits | Off in generated INI |

`init` never overwrites an existing INI. For experiments, use `--settings FILE`
on `init`, `check` and `run`, and `--cache` on `init`. All INI paths must be
absolute and unquoted; spaces are accepted. CLI paths with spaces need shell
quotes as shown. The CLI expands `~` only for its command-line path options.
Engine options are documented in `src/OrthoStreamXP/Configuration/SettingsOptions.cs`.
Windows-only `filesystem-threads` does not control macFUSE's worker count.

Once the app prints **Mounts are ready**, start X-Plane. Back up
`Custom Scenery/scenery_packs.ini` before editing it. Keep airports and overlays
above the new mesh entry, and avoid enabling both the old AutoOrtho mesh mount
and the new streamed mesh for the same region. A new entry looks like:

```text
SCENERY_PACK Custom Scenery/z_OrthoStreamXP_<actual-source-name>/
```

Use the **actual name printed by `check`**, not the placeholder above. Keep the
existing overlay packages. The default does not rewrite `scenery_packs.ini`;
explicitly setting `scenery-packs = on` opts into upstream ordering edits.
Unmounted placeholders contain only an ownership marker and empty scenery
subdirectories. They do not provide scenery: mount before starting X-Plane.

## Stop and recover

Quit X-Plane, then press Ctrl+C in the terminal running OrthoStreamXP. A busy
mount is reported and its engine/worker stay alive. Close other programs reading
the mount and press Ctrl+C again. Do not terminate the worker first.

```bash
bash scripts/run-macos.sh mounts
bash scripts/run-macos.sh unmount --mountpoint "/absolute/path/to/own-mount"
```

Only after closing X-Plane and accepting that open reads may fail, explicit
recovery can request:

```bash
bash scripts/run-macos.sh unmount --mountpoint "/absolute/path/to/own-mount" --force
```

Recovery refuses non-OrthoStreamXP filesystem identities; it is not an AutoOrtho
unmount tool. Do not delete a mounted directory to fix it. An engine crash closes
the private control pipe; its worker attempts a bounded force-detach while still
answering callbacks. Normal Ctrl+C never automatically force-unmounts.

## Important validation gaps

The default preserves buffered/memory-mapped reads. `auto_cache` and dynamic
streamed-file modification times request cache refresh when the driver checks
metadata. This is **not** a proof that every already-mapped page or a texture
already uploaded by X-Plane updates in place. Validate cold-cache loading,
stand-in replacement and scenery reloads in a real flight. `--direct-io` is a
**diagnostic** run option; it can disable `mmap`, so it is not the default fix.

The Python/.NET copy and IPC overhead, memory use, large-region directory
behaviour and long-flight stability have not been benchmarked. System-wide
Windows CPU/free-memory/dedicated-VRAM probes return unavailable on macOS rather
than inventing Apple unified-memory measurements. Process CPU and normal engine
statistics remain available. Nothing here asserts Windows regression tests pass.

See [architecture](docs/macos/ARCHITECTURE.md), [validation](docs/macos/VALIDATION.md),
[PR draft](docs/macos/PR_DESCRIPTION.md) and [donor attribution](THIRD_PARTY_NOTICES.md).
