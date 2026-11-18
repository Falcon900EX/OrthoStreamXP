# Third-party notices for the macOS source port

The original OrthoStreamXP source and its GPL version 3 license remain in this
repository (`LICENSE`). New port code uses the same GPL-3.0 terms. AutoOrtho
portions retain their Apache-2.0 source notices; see
`platform/macos/donor/NOTICE.md` and `LICENSE-Apache-2.0` in that directory.

AutoOrtho Continued donor release 3.1.0:
https://github.com/ProgrammingDinosaur/autoortho4xplane/tree/3.1.0
Upstream license:
https://github.com/ProgrammingDinosaur/autoortho4xplane/blob/3.1.0/LICENSE.md

The runtime Python binding `mfusepy==3.1.1` is installed separately by pip, not
vendored in this source archive. Its ISC copyright/license notice belongs to the
binding authors and remains in the installed distribution:
https://github.com/mxmlnkn/mfusepy/blob/v3.1.1/mfusepy.py
Its exact distribution hashes are in `platform/macos/requirements.txt`.

The engine adds the macOS-native assets package matching the existing SkiaSharp
version, `SkiaSharp.NativeAssets.macOS 4.153.1`. SkiaSharp's own license and
third-party native component notices must accompany a later binary release:
https://github.com/mono/SkiaSharp

The .NET runtime, Skia native components, test framework, and macFUSE have their
own notices/terms. macFUSE itself is not included or installed by this source
port. Before redistributing a compiled app, retain the SDK/package-generated
notices and review the complete runtime dependency/license inventory. This file
is source provenance, not a claim that binary-release compliance has been audited.
