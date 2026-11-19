# OrthoStreamXP 1.4 — macOS Apple Silicon Guide

> **DEVELOPMENT / TECHNICAL PREVIEW — NOT A PUBLIC macOS RELEASE**
> **The numbered ASCII Terminal UI is temporary.** The planned permanent experience is a native **SwiftUI installer and controller**. This document describes the current macOS 1.4 implementation and its limitations; it is not an announcement that a SwiftUI application or FUSE-T backend is already available.
>
> **Last reviewed:** November 18, 2026
> **Project fork:** https://github.com/Falcon900EX/OrthoStreamXP
> **Development branch:** `macos/1.4-integration` (last reported integrated commit `10cf4f6`; later changes may be local or unpushed).

## 1. What OrthoStreamXP does

OrthoStreamXP streams aerial-photo imagery for **X-Plane 12** as the simulator needs it, builds X-Plane-compatible textures, and reuses cached imagery on later flights. It needs compatible **scenery/terrain mesh packages**: it does **not** generate terrain mesh from scratch. Unlike downloading vast prebuilt orthophoto regions, the goal is to fetch and cache the imagery required for areas you fly.

This is a **community macOS Apple Silicon port of upstream OrthoStreamXP 1.4**. It retains the upstream .NET streaming engine, with a macOS Python/filesystem bridge and an interim text-mode control panel.

### Current scope versus future scope

| Area | Status | What that means |
|---|---|---|
| .NET 10 ARM64 engine integration | Implemented and build/test validated | Flight-quality and long-run regressions remain under evaluation. |
| macFUSE bridge | Implemented and exercised | macFUSE installation/permissions remain a substantial first-run obstacle. |
| ASCII numbered Terminal UI | **Temporary development interface** | Settings, performance views, logs and engine controls; not the intended user-facing release UI. |
| Separate engine Terminal session | Development UI feature; real-Mac lifecycle validation incomplete | Controller remains usable while the engine runs; clean window closure is backlog work. |
| Guarded emergency unmount | Added to latest standalone ASCII UI v1.4 | Must be treated as emergency-only; local checkout/branch inclusion not independently verified. |
| SwiftUI setup assistant | **Planned, not implemented** | Native macFUSE guidance, readiness tests, restart/recovery flow. |
| SwiftUI operational controller | **Planned, not implemented** | Native Home, Start/Stop, Settings, Performance, Cache, Logs, Diagnostics. |
| FUSE-T backend | **Experimental proposal, not integrated** | Installing FUSE-T alone does not make this port use it. macFUSE remains the known baseline. |
| Packaged/signed/notarized `.app` | **Not yet released** | No public one-click Mac installer should be assumed. |

## 2. Before you start

For the **current technical preview**, you need:

- A supported **Apple Silicon Mac** with **X-Plane 12** installed.
- A working **macFUSE** installation and completed macOS approvals (the exact approval flow varies by macOS version and macFUSE backend).
- An OrthoStreamXP macOS 1.4 source/build checkout, the .NET SDK/runtime required by that checkout, and the project-specific Python bridge dependencies. These are currently development prerequisites; the future SwiftUI app should bundle or guide them appropriately.
- A compatible region's terrain mesh/scenery package, separately from the virtual scenery mount created under `Custom Scenery`.
- A reliable connection for uncached imagery and adequate space on a fast local SSD for the cache.

**Do not follow upstream Windows-only WinFsp, `.msi`, Start-menu or Windows registry instructions on macOS.** The upstream Windows user guide documents the core streaming product, while this guide describes the Mac integration.

### File locations in the current development setup

These are **the paths used by the current test installation**, not guaranteed defaults for all future users:

| Purpose | Current example |
|---|---|
| Development checkout | `~/Downloads/OrthoStreamXP-macos-upgrade-1.4` |
| Shared macOS configuration | `~/Library/Application Support/OrthoStreamXP/orthostreamxp-mac.ini` |
| Imagery/texture cache root | `~/Library/Caches/OrthoStreamXP` |
| X-Plane installation | `~/X-Plane 12` |
| Virtual scenery mount example | `~/X-Plane 12/Custom Scenery/z_OrthoStreamXP_north_america_base` |

Keep caches, source scenery packages and virtual mount locations distinct. **Never manually delete a live mounted scenery folder.** A mountpoint is not the same as the source terrain package.

## 3. Current installation and setup — technical preview

> **These steps are for developers/test pilots. The permanent SwiftUI installer will replace the Terminal-based workflow.** This guide intentionally avoids implying that a self-contained macOS installer exists today.

1. **Verify X-Plane 12 and your scenery package.** The region package must contain the required mesh/`Earth nav data` content. Select the **parent directory** containing packages when that is what the engine expects; do not point at an unrelated cache or the virtual mounted folder.
2. **Verify macFUSE is installed and operational.** Seeing an installed library is not enough: macOS approvals and a genuine mount/read/unmount cycle must work. Follow the **official macFUSE documentation** for your specific macOS release rather than making broad system-security changes from copied commands.
3. **Build and validate the Mac integration** using the checkout's maintained build/doctor/smoke tools. The current development runner is `scripts/run-macos.sh`; script naming and packaging may change.
4. **Confirm your configuration** in `orthostreamxp-mac.ini`, including scenery path, cache, quality and worker counts. The current Mac 1.3.1/1.4 development builds share this configuration file, so changing it can affect both baselines.
5. **Start OrthoStreamXP before starting X-Plane.** Wait for the scenery mount/ready state before launching the simulator; X-Plane normally discovers installed scenery during startup.
6. **Fly a known route and inspect the results.** New areas may initially show lower-detail substitutes while imagery downloads; revisiting cached regions should avoid much of this delay.
7. **Shut down in reverse order.** Quit X-Plane, then stop the OrthoStreamXP engine and verify its mount disappears before switching builds/backends or making mount changes.

**Known open issue:** A recent 1.4 flight looked blurry even though its report showed 51,098 real texture reads, zero stand-ins, zero new downloads and zero builds. That log rules out missing reads in that session, but **does not prove the imagery was visually sharp**. Cache resolution, X-Plane mipmap/VRAM behavior and actual quality selection still need validation against the 1.3.1 Sharp baseline.

## 4. How the temporary Terminal UI works

The current work-in-progress UI is an **ASCII/ANSI numbered control panel**. It provides a Home menu, engine start, configuration/settings pages, performance and flight-tuning information, logs, cache and diagnostic views. Exact numbering may change as the prototype evolves.

- **Start** launches the engine in a separate Terminal window in the later experimental UI, allowing the main controller to remain interactive.
- **Engine status** is displayed on the controller, including stopped/running states and possible orphaned mount conditions.
- **Settings** should be treated as **next-launch** changes; don't assume a running stream reconfigures itself.
- **Emergency unmount** is a protected troubleshooting action for an unresponsive engine and an orphaned **OrthoStreamXP-owned** mount—not a normal Stop button. The UI uses separate `LIFT` and `UNMOUNT` confirmations, with a further `FORCE` confirmation if a force-unmount is presented. Do not use this while X-Plane or the engine is active.
- **Automatic closure of the engine Terminal** after a successful stop remains a **UX backlog item**, not a proven current behavior. Failed shutdown diagnostics should remain visible.

> **The Terminal UI is temporary, not OrthoStreamXP macOS's final product design.** We are preserving it as an engineering/debugging fallback while development moves to a native SwiftUI installer and permanent controller. New public-user workflows should be designed for SwiftUI, not additional Terminal pages.

## 5. Quality presets and current tuning

The upstream **1.4** guide defines these presets:

| Preset | Texture size | Intended use |
|---|---:|---|
| Balanced | 1024 px | Higher altitude, lower bandwidth/VRAM |
| Sharp | 2048 px | Everyday flying and approaches |
| Ultra | 4096 px | Lower/slower flight with more resources |
| Extra | 4096 px | Finest near-airport imagery where available |
| Dynamic | Varies by altitude | Extra near ground, Sharp above 5,000 ft AGL, Balanced above 25,000 ft AGL |

On descent, upstream Dynamic anticipates the approach and restores higher-detail requests early. **Dynamic is an opt-in preset; upgrading to 1.4 does not silently enable it.** Cached imagery already downloaded may be reused at its existing quality.

The most recently reported **development test configuration** was:

```ini
level-change=-1
download-workers=48
encoder-workers=8
dds-build-workers=8
build-memory=768
build-tiles=8192
cache-limit=25
night-lights=on
```

These are **reported settings, not guaranteed running-process values**. `level-change=-1` has been used for the Sharp baseline. Worker counts should be checked against engine startup logs before assuming they took effect. Do not raise worker counts or change presets as a reflexive fix for blur: investigate texture dimensions, source resolution, simulator texture settings and cache behavior first.

## 6. Cache, image sources and scenery packages

- A cache reduces repeat downloads; a session with **zero bytes downloaded** may simply be serving already-cached content.
- Cache efficiency is **not equivalent to image quality**. Real texture reads may still appear soft because of the selected preset, existing source imagery or X-Plane's mip/VRAM choices.
- Keep the cache on a fast local SSD where possible. Cache clearing is a disruptive diagnostic step; it should be selective and explicit, not the default response to every visual issue.
- OrthoStreamXP 1.4 supports source selection as described in the upstream guide (Bing/Esri and supported regional alternatives). Image coverage, age, colors and provider availability can differ by region.
- It streams photo **textures**, not the underlying **terrain mesh**. A compatible mesh package must exist for each region.
- Avoid overlapping enabled virtual scenery packages from competing streaming tools. Check scenery order and active mounts instead of deleting scenery indiscriminately.
- Night-light functionality exists in the port, but actual visual results are still subject to flight validation.

## 7. macFUSE today; FUSE-T experiment tomorrow

### Current supported development path: macFUSE

The macOS bridge uses macFUSE and incorporates safeguards that inspect mount identity, use cached Darwin mount-table information, and bound unmount attempts. Keep the working macFUSE installation intact during future backend experiments.

### Proposed comparison: FUSE-T (not yet active)

The **X-Plane Map Enhancement** macOS documentation identifies **FUSE-T** as a setup dependency, providing useful evidence that this backend can be used for X-Plane scenery applications. This is **not proof** that OrthoStreamXP's current Python/macFUSE bridge can switch by installing FUSE-T alone.

A safe OrthoStreamXP FUSE-T experiment should:

1. Preserve macFUSE, the current Sharp cache, configuration and known-good port.
2. Explicitly select FUSE-T in an **isolated experimental bridge/build**, without replacing the system macFUSE library or the default backend.
3. Implement **backend-aware mount detection and identity checks**; current orphaned-mount protections expect macFUSE-specific mount information.
4. Pass an isolated mount/list/read/unmount test before connecting to X-Plane.
5. Compare real flights with matching route, aircraft, preset, warm/cold cache conditions, load time, read latency, CPU, memory, failure rates and shutdown cleanliness.
6. Roll back by stopping the FUSE-T test build, confirming unmount and relaunching the existing macFUSE implementation—**no macFUSE removal**.

Check FUSE-T's current licensing and redistribution permissions before bundling it with a public installer. Coexistence and application compatibility must be verified on the target macOS version.

## 8. Troubleshooting guide

| Symptom | First checks | Avoid |
|---|---|---|
| macFUSE detected but no mount | Verify OS security approval, compatible backend, real mount test and diagnostic output | Assuming library presence equals readiness |
| Engine says running, but there is no engine process | Look for an **orphaned OrthoStreamXP-owned** mount; inspect engine state and mount table | Force-unmounting an unrelated mount |
| Blurry ground despite “successful” flight | Confirm active preset, DDS dimensions, old cache content, X-Plane texture/VRAM behavior and scenery source | Adding workers before diagnosing quality |
| Scenery not visible | Start engine **before** X-Plane; verify virtual package, terrain mesh and `scenery_packs.ini` ordering | Deleting source scenery packages |
| No download activity | Check whether textures were already cached and real reads succeeded | Assuming zero downloads means failure |
| Slow streaming or stand-ins | Review connection/provider reachability, source latency, preset and engine flight logs | Treating raw broadband speed as proof that the imagery provider is reachable |
| Can't stop engine | Quit X-Plane first; use normal Stop; inspect logs and actual mounts | Routine force-unmount or killing services blindly |
| Crash or abnormal shutdown | Preserve logs and reproduce with unchanged settings | Closing diagnostic windows before saving error details |

### For actionable bug reports

Include: macOS version and chip; OrthoStreamXP build/commit and filesystem backend; X-Plane version; selected scenery package and source; quality preset; cache state; route/airport and aircraft; whether X-Plane started after the engine; approximate time of issue; engine log; relevant X-Plane `Log.txt`; and screenshots of visible scenery or errors. Remove tokens, credentials or other sensitive data before posting logs publicly.

## 9. Release plan — SwiftUI replaces the Terminal experience

**Release gate A — Streaming correctness:** Resolve the blurry 1.4 flight regression; validate Sharp against 1.3.1 and assess Dynamic with real flights. Confirm reliable start, stop, mount recovery and long-duration behavior.

**Release gate B — Native setup:** Build a SwiftUI first-run wizard with understandable detection of dependencies, macFUSE approval guidance, restart/resume handling, X-Plane/scenery/cache selection and a **real temporary mount/read/unmount readiness test**. Pilot control over security-sensitive OS steps is mandatory.

**Release gate C — Native controller:** Replace the numbered Terminal menu with a native Mac Home page, engine Start/Stop, settings, performance, cache, logs, troubleshooting and guarded recovery. Retain the CLI only as an advanced fallback. Engine supervision must be independent of visible Terminal sessions.

**Release gate D — Distribution:** Bundle appropriate dependencies, build a standard `.app`/DMG, complete developer signing and notarization, verify licenses and perform clean-Mac installation tests. Do not present the current technical preview as a finished consumer installer.

**Release gate E — Pilot beta:** Test on multiple Apple Silicon Macs and macOS versions, including machines without development tools or a preconfigured macFUSE installation. Track installation completion, scenery sharpness, stability and recovery.

## 10. Upstream versus macOS-port differences

| Upstream OrthoStreamXP 1.4 documentation | This macOS development port |
|---|---|
| Windows installer, WinFsp, Windows GUI | Mac-specific bridge with macFUSE and **temporary ASCII Terminal UI** |
| First-run GUI walkthrough | Manual/development setup currently; **SwiftUI wizard planned** |
| Home page and quality UI | Temporary numbered controls and log/performance pages |
| Automatic Windows application workflow | Current Mac process supervision is experimental; native controller planned |
| Windows-specific paths and logs | macOS `~/Library/...` configuration and cache paths |
| Upstream Dynamic preset | Engine feature integrated; Mac flight behavior needs validation |

Do not treat Windows UI screenshots or paths as screenshots of the current Mac port. Likewise, do not copy Map Enhancement's map-provider, packaging, licensing or FUSE-T setup instructions verbatim as if they were OrthoStreamXP features.

## 11. References and attribution

These links informed this **original, project-specific guide**; wording and workflows have been adapted rather than copied wholesale.

- **OrthoStreamXP official user guide:** https://flysimdev.github.io/OrthoStreamXP/
- **OrthoStreamXP first setup:** https://flysimdev.github.io/OrthoStreamXP/first-setup/
- **OrthoStreamXP flight workflow:** https://flysimdev.github.io/OrthoStreamXP/flying/
- **OrthoStreamXP 1.4 quality/settings:** https://flysimdev.github.io/OrthoStreamXP/settings/
- **OrthoStreamXP upstream source/license:** https://github.com/flysimdev/OrthoStreamXP
- **OrthoStreamXP macOS development fork:** https://github.com/Falcon900EX/OrthoStreamXP
- **X-Plane Map Enhancement overview:** https://www.aiflygo.com/docs/xplane-map-enhancement/
- **Map Enhancement macOS installation guidance:** https://www.aiflygo.com/docs/xplane-map-enhancement/download/
- **Map Enhancement usage and diagnostics:** https://www.aiflygo.com/docs/xplane-map-enhancement/usage/
- **Map Enhancement features:** https://www.aiflygo.com/docs/xplane-map-enhancement/features/
- **FUSE-T releases:** https://github.com/macos-fuse-t/fuse-t/releases

**Software and content rights:** Upstream OrthoStreamXP identifies itself as GPL-3.0. Imagery is owned by its respective providers and remains subject to their terms; the software and documentation do not grant a right to redistribute imagery. This original guide is not affiliated with X-Plane Map Enhancement or its authors. Reusing third-party code, screenshots, graphics, package contents or binaries requires separate license and attribution review.

---

**Maintainer note:** This is the initial macOS technical-preview documentation baseline. Before publication, reconcile each feature/status with the actual Git branch, built binaries and real-device test records; then update paths, screenshots and setup directions for the SwiftUI release.
