# macOS implementation notes

## Boundaries

```text
X-Plane ARM64
  -> macFUSE FUSE-2 library/kernel interface (already installed by the tester)
  -> Python mfusepy worker, one per package
  -> private AF_UNIX socket, bounded connection pool, one exclusive lease per request
  -> .NET OverlayFiles adapter
  -> original SceneryOverlay / TextureStreamer / colour pipeline / cache
```

`OrthoStreamXP.Mac.slnx` excludes the Windows project. Core source is linked into
`OrthoStreamXP.Core` rather than duplicated. Its `InternalsVisibleTo` declarations
keep the existing internal engine API available to the Mac host/tests without
turning it into a public SDK. `IMountHostFactory` is the small native lifecycle
boundary. The Mac CLI owns a created session before starting mounts, retaining
it through partial startup failures so cleanup can be retried. A retained Windows
adapter is not a requirement for using or building macOS.

The Mac adapter mirrors WinFsp's per-open read cursor, pinned backing-file length,
DDS generation, in-memory terrain, night texture, colour/exposure/season
transforms and read logging. File descriptors permit atomic cache replacements
without changing an already-open handle. Per-handle locks serialize access to
that cursor and release; unrelated handles can execute concurrently. Directory
handles snapshot names and return 512-entry IPC pages. Default lazy terrain
listing avoids enumerating all possible generated definitions.

## Protocol version 1

All integers in frame headers are little-endian. Request: unsigned 32-bit byte
length, then UTF-8 JSON. Response: signed 32-bit POSIX errno (zero success),
unsigned 32-bit byte length, then payload. A successful `read` payload is raw DDS
or file bytes, never base64. Other successes are JSON; errors are short UTF-8
messages without a serialized source-path stack trace.

| Operation | Request fields beyond `v:1, op` | Result |
|---|---|---|
| ping | none | `{version:1}` |
| stat | path | directory, size, modified (Unix seconds), inode |
| fstat | fh | same, retaining open length and current streamed version |
| open / opendir | path | `{fh:<64-bit handle>}` |
| read | fh, offset, size | binary bytes; short at EOF |
| readdir | fh, offset | `{names:[...], end:bool}` |
| release | fh | `{ok:true}` |

Requests are bounded to 64 KiB, each read/response to 4 MiB, concurrent sockets to
64 per worker by default (256 hard cap in the server), open handles to approximately 65,536 per mount. Large FUSE reads
are chunked. The client handles partial socket reads, never replays a failed
non-idempotent operation, and reconnects only for a later callback. Broken
connections become EIO; missing files ENOENT; invalid handles EBADF; mutation
callbacks EROFS. The worker uses the current user's uid/gid and no `allow_other`.

The pool is independent of Python thread-local storage: ctypes callbacks from
native FUSE threads can recreate Python thread state on every invocation.
Leases are returned after a complete response, while damaged connections are
closed. This prevents the original callback-by-callback connection leak. See
[bridgefix2](BRIDGEFIX2.md) for the reproduction and native regression tests.

Sockets live in a random short `/tmp/osxp-<guid>` directory (0700); socket mode
is 0600. These permissions isolate other users, not hostile processes owned by
the same user. The engine exposes no TCP port. The aircraft UDP listener is the
existing engine's configurable X-Plane integration. This is not a sandbox for
untrusted mesh packages; trusted backing symlinks retain upstream semantics.

## Driver lifetime and path safety

The AutoOrtho donor mount-table reader uses Darwin `getfsstat64(MNT_NOWAIT)` so
checking a stale mount does not itself traverse that volume. Unknown table state
fails closed. Source/cache/mount checks run before the .NET settings planner can
enumerate packages. Source, cache and final mount cannot overlap. Existing
nonempty directories are never accepted except exact owned, empty placeholders.
No runtime mountpoint cleanup uses recursive deletion.

A private stdin control pipe tells the FUSE worker to detach while it is still
servicing operations. The unmount subprocess has a deadline; a kernel-blocked
helper is not waited for indefinitely, and further helpers are not stacked.
Busy normal stop retains worker and engine. Parent EOF is a distinct crash
recovery path and permits force detach. The worker uses a separate process
session, keeping Terminal Ctrl+C directed to the orchestrator first. Native
validation of this behaviour remains mandatory, including partial startup.

## Cache freshness and performance

`auto_cache`, zero attribute/name timeouts and streamed modification timestamps
expose new engine versions when macFUSE rechecks a file. Handles keep their
original size. The default avoids `direct_io` so mmap is not deliberately
disabled. There is no proactive macFUSE invalidate-notification implementation in
this revision; refresh of existing mappings and scenery reloads is a flight-test
gate. `--direct-io` is for controlled diagnosis, not the default.

The interprocess split avoids embedding a Python runtime in .NET or binding
Darwin FUSE structures manually. Costs include socket calls and copies on the
read path, extra Python worker memory, and two process lifecycles. This was chosen
to reuse AutoOrtho's binding and macOS lifecycle knowledge first. Compare real
frame-time/throughput traces before optimizing the bridge or replacing it with
a direct native adapter.

## Reproducibility references

- Donor source/tag and local adapted-file hashes: `platform/macos/donor/SOURCES.json`.
- Binding distribution hashes: `platform/macos/requirements.txt`.
- Native Skia package: https://www.nuget.org/packages/SkiaSharp.NativeAssets.macOS/4.153.1
- macFUSE cache/mmap semantics: https://github.com/macfuse/macfuse/wiki/Mount-Options
- Test runner CLI: https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-test-mtp
- CI runner architecture: https://docs.github.com/en/actions/reference/runners/github-hosted-runners

Dependency versions inherited from the supplied source have not been upgraded.
NuGet transitive lock files are not fabricated; produce and review them after a
successful SDK restore if maintainers require locked restores for a release.
