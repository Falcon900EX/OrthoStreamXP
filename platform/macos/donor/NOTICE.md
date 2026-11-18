# AutoOrtho donor notices

The AutoOrtho-derived portions of this distribution originate in AutoOrtho
Continued, maintained at https://github.com/ProgrammingDinosaur/autoortho4xplane .
Original copyright interests remain with that project's contributors. Its
release 3.1.0 is distributed under Apache License 2.0; the complete license is
included as `LICENSE-Apache-2.0`. No endorsement by its maintainers is implied.

Pinned release: `3.1.0`
Pinned commit: `9cf46158913c994013250a1844d732249ef49f8e`

Copied/adapted portions, with changes clearly noted in their source headers:

- `macos_mounts.py`: `autoortho/utils/macos_mounts.py`. Original implementation
  retained; attribution header and whitespace adjusted.
- `mac_lifecycle.py`: bounded Popen/unmount-helper lifecycle from
  `autoortho/macsetup.py::unmount_macfuse`. Modified for explicit mount ownership,
  normal-versus-force stop, no AutoOrtho config, and retained serving processes.
- `../mount_worker.py`: control-pipe and parent-EOF shutdown design adapted from
  `autoortho/mount_worker.py::_start_control_thread`. OrthoStreamXP IPC,
  separate worker session, readiness handshake and busy retry are new.
- `../macfuse_runtime.py::mount_options`: Darwin options profile adapted from
  `autoortho/autoortho_fuse.py::fuse_option_profiles_by_os`. Modified to be
  read-only, avoid allow_other, identify owned mounts and refresh metadata/cache.

The .NET engine, Unix socket protocol, CLI, path validator and accompanying tests
are not copied from AutoOrtho's texture pipeline. The packaged AutoOrtho.app and
its binary libraries are not redistributed. `SOURCES.json` records original
URLs and hashes of local adapted files, not hashes misrepresented as upstream
unmodified source hashes.
