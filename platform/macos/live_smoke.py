# SPDX-License-Identifier: GPL-3.0-only
"""Opt-in real macFUSE test: synthetic imagery, private scratch paths, no X-Plane.

Never kills the engine or its FUSE workers on a busy mount. Scratch files are
removed only after the cached mount table proves no scratch mount remains.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import mmap
import os
from pathlib import Path
import platform
import random
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import traceback

from donor.macos_mounts import read_mount_table
from mount_paths import within

STEM = "21904_35824_BI16"
PASSTHROUGH = b"passthrough-mask-test"
# Cross both 16 KiB Apple Silicon pages and the usual 64 KiB FUSE request size.
IMMUTABLE = bytes(range(256)) * 1024 + b"end-of-immutable-fixture"
CACHE_FOLDER = "level-3_512-512"  # Matches the isolated level-change=-3 settings.
CACHE_STEM = "5476_8956_PX14"  # 21904_35824_BI16 at level-change=-3.
DDS_SIZE = 512


def expected_dds_length() -> int:
    total, side = 128, DDS_SIZE
    while True:
        total += max(1, (side + 3) // 4) ** 2 * 8
        if side == 1:
            return total
        side //= 2


def validate_dds(content: bytes, label: str) -> None:
    expected = expected_dds_length()
    assert len(content) == expected, f"{label}: DDS length {len(content)} != {expected}"
    assert content[:4] == b"DDS ", f"{label}: missing DDS magic"
    assert struct.unpack_from("<II", content, 12) == (DDS_SIZE, DDS_SIZE), f"{label}: wrong dimensions"
    assert content[84:88] == b"DXT1", f"{label}: expected DXT1"
    assert struct.unpack_from("<I", content, 28)[0] == 10, f"{label}: expected 10 mip levels"


def require_bytes(actual: bytes, expected: bytes, label: str, offset: int = 0) -> None:
    if actual == expected:
        return
    different = next((i for i, (a, b) in enumerate(zip(actual, expected)) if a != b),
                     min(len(actual), len(expected)))
    raise AssertionError(
        f"{label}: data mismatch at file offset {offset + different}; "
        f"lengths actual={len(actual)} expected={len(expected)}; "
        f"sha256 actual={hashlib.sha256(actual).hexdigest()} "
        f"expected={hashlib.sha256(expected).hexdigest()}; "
        f"bytes actual={actual[different:different + 16].hex()} "
        f"expected={expected[different:different + 16].hex()}")


def reference_dds(cache_file: Path, timeout: float = 30.0) -> bytes:
    """Wait only for atomic cache publication, never retry mismatching data.

    This is a local scratch file outside FUSE. It is an independent reference:
    comparing two reads through the mount alone could accept stale data twice.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            content = cache_file.read_bytes()
        except FileNotFoundError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"No completed synthetic DDS in cache: {cache_file}") from None
            time.sleep(0.05)
            continue
        validate_dds(content, "completed cache reference")
        return content


def stable_reads(stream, reference: bytes, label: str) -> None:
    """Compare a fixed generation, including the whole mapping, not a prefix."""
    # Explicit metadata check exercises auto_cache rather than forcing a driver
    # flush, remount, direct_io, or deletion. Do not retry on a mismatch.
    info = os.fstat(stream.fileno())
    assert info.st_size == len(reference), f"{label}: wrong file size"
    print(f"PROBE: {label}: regular read against completed cache", flush=True)
    require_bytes(os.pread(stream.fileno(), len(reference), 0), reference, label + " regular read")
    assert os.pread(stream.fileno(), 100, len(reference)) == b"", f"{label}: read beyond EOF"
    print(f"PROBE: {label}: 100 concurrent random reads against completed cache", flush=True)
    rng = random.Random(1234)
    ranges = [(rng.randrange(len(reference)), rng.randrange(1, 8193)) for _ in range(100)]

    def check(item):
        offset, length = item
        data = os.pread(stream.fileno(), length, offset)
        require_bytes(data, reference[offset:offset + length], label + " random read", offset)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(check, ranges))
    print(f"PROBE: {label}: full-file memory-mapped read against completed cache", flush=True)
    with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
        require_bytes(mapped[:], reference, label + " mmap")
    # A mapping must not make subsequent conventional reads disagree either.
    require_bytes(os.pread(stream.fileno(), len(reference), 0), reference, label + " post-mmap read")


def streamed_dds_probe(texture: Path, cache_file: Path) -> None:
    print("PROBE: cold/warm streamed DDS open/read and header", flush=True)
    with texture.open("rb") as stream:
        before = os.fstat(stream.fileno()).st_mtime_ns
        first = stream.read()
        validate_dds(first, "initial streamed read")
        print("PROBE: wait for completed synthetic DDS outside the mount", flush=True)
        reference = reference_dds(cache_file)
        after = os.fstat(stream.fileno()).st_mtime_ns
        print(f"DDS generations: initial-read sha256={hashlib.sha256(first).hexdigest()} "
              f"completed-cache sha256={hashlib.sha256(reference).hexdigest()} "
              f"mtime_ns before={before} after={after}", flush=True)
        if first != reference:
            different = next(i for i, pair in enumerate(zip(first, reference)) if pair[0] != pair[1])
            print(f"NOTE: initial streaming snapshot differs from the completed generation at offset {different}. "
                  "Header-chunk stand-ins are allowed during generation; stable checks below must match the cache.", flush=True)
        # Keep the original descriptor alive: reopening alone could conceal a
        # failure to refresh the streamed handle after the generation completes.
        stable_reads(stream, reference, "original DDS descriptor after generation")
    with texture.open("rb") as reopened:
        stable_reads(reopened, reference, "reopened DDS descriptor")
    # This fixed synthetic test has no cache eviction or colour/season changes.
    # Fail if our supposedly immutable oracle changed during the comparisons.
    require_bytes(reference_dds(cache_file, timeout=0), reference, "completed cache remained unchanged")


def probe(mount: Path, cache_file: Path) -> None:
    print("PROBE: root metadata and directory listing", flush=True)
    assert stat.S_ISDIR(mount.stat().st_mode), "Mount root is not a directory"
    assert "textures" in os.listdir(mount)
    mask = mount / "textures" / "water_transition.png"
    print("PROBE: passthrough file metadata", flush=True)
    assert mask.stat().st_size == len(PASSTHROUGH), "Wrong passthrough file size"
    print("PROBE: passthrough file open/read", flush=True)
    assert mask.read_bytes() == PASSTHROUGH, "Passthrough bytes differ"
    print("PROBE: 600 repeated passthrough open/read/close cycles", flush=True)
    # Metadata lookups alone can be satisfied by the kernel attribute cache.
    # Reopen and close a real file to exercise native FUSE callbacks repeatedly.
    for _ in range(600):
        assert mask.stat().st_size == len(PASSTHROUGH)
        assert mask.read_bytes() == PASSTHROUGH, "Repeated passthrough read differs"
    print("PROBE: generated terrain definition", flush=True)
    terrain = (mount / "terrain" / (STEM + ".ter")).read_text()
    assert f"BASE_TEX_NOWRAP ../textures/{STEM}.dds" in terrain
    texture = mount / "textures" / (STEM + ".dds")
    print("PROBE: immutable passthrough regular and memory-mapped reads", flush=True)
    with (mount / "fixtures" / "reference.bin").open("rb") as immutable:
        stable_reads(immutable, IMMUTABLE, "immutable passthrough")
    streamed_dds_probe(texture, cache_file)
    print("PROBE: write protection", flush=True)
    try:
        fd = os.open(texture, os.O_WRONLY)
    except OSError as exc:
        assert exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM), exc
    else:
        os.close(fd)
        raise AssertionError("Read-only mount accepted a writable open")
    print("PASS: DDS, terrain, passthrough, 600 repeated opens, EOF, concurrent random reads, full-file mmap, original/reopened descriptors, read-only", flush=True)


def print_log_tail(path: Path) -> None:
    """Show bounded local diagnostics immediately, without another user command."""
    print(f"--- {path.name}: last 80 lines (at most 64 KiB) ---", file=sys.stderr)
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 65536))
            text = stream.read(65536).decode("utf-8", errors="replace")
        print("\n".join(text.splitlines()[-80:]), file=sys.stderr)
    except OSError as exc:
        print(f"Could not read diagnostic log: {exc}", file=sys.stderr)
    print("--- end of diagnostic log ---", file=sys.stderr)


def mounted_below(root: Path) -> bool:
    # Do not stat the mountpoint. Errors deliberately propagate: fail closed.
    return any(within(entry.mountpoint, str(root)) for entry in read_mount_table())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--probe", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--reference-cache", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--keep", action="store_true", help="Keep successful scratch logs and data")
    args = parser.parse_args()
    if args.probe is not None:
        if args.reference_cache is None:
            parser.error("--probe requires --reference-cache (an isolated completed DDS outside the mount)")
        probe(args.probe, args.reference_cache)
        return 0
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("The live mount test requires native Apple Silicon macOS")
    if args.app is None or not args.app.is_file():
        raise RuntimeError("Build first, then pass --app /path/to/OrthoStreamXP.Mac")
    root = Path(tempfile.mkdtemp(prefix="orthostreamxp-smoke-"))
    source, cache, mount = root / "source", root / "cache", root / "mount"
    (source / "Earth nav data").mkdir(parents=True)
    (source / "textures").mkdir()
    (source / "textures" / "water_transition.png").write_bytes(PASSTHROUGH)
    (source / "fixtures").mkdir()
    (source / "fixtures" / "reference.bin").write_bytes(IMMUTABLE)
    cache_file = cache / CACHE_FOLDER / "textures" / (CACHE_STEM + ".dds")
    settings = root / "smoke.ini"
    settings.write_text(f"""source = {source}
cache = {cache}
mountpoint = {mount}
texture-provider = synthetic
level-change = -3
mip-streaming = on
near-wait = 60
max-wait = 30
prefetch-loaded-scenery = off
match-colours = off
seasons = off
night-lights = off
exposure = 100
read-log = on
scenery-packs = off
cache-limit = 0
log-output = both
log-file = {root / 'engine.log'}
""")
    env = os.environ.copy()
    env["ORTHOSTREAMXP_PYTHON"] = sys.executable
    env["ORTHOSTREAMXP_DATA_HOME"] = str(root / "app-data")
    success = False
    process = None
    console = None
    print(f"Scratch/logs: {root}", flush=True)
    try:
        # Second pass proves placeholders can be remounted without deleting data.
        for iteration in range(2):
            ready = root / f"ready-{iteration}"
            console = root / f"console-{iteration}.log"
            with console.open("w") as output:
                process = subprocess.Popen([str(args.app.resolve()), "run", "--settings", str(settings),
                                            "--ready-file", str(ready)], env=env, stdin=subprocess.DEVNULL,
                                           stdout=output, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + 90
                while not ready.exists():
                    if process.poll() is not None:
                        raise RuntimeError(f"Engine exited {process.returncode}; see {output.name}")
                    if time.monotonic() > deadline:
                        raise TimeoutError("Mount initialization timed out")
                    time.sleep(0.2)
                # Filesystem calls run out of process so a blocked syscall cannot
                # prevent this supervisor from reporting a failure.
                child = subprocess.Popen([sys.executable, __file__, "--probe", str(mount),
                                          "--reference-cache", str(cache_file)], env=env,
                                         stdout=output, stderr=subprocess.STDOUT)
                try:
                    code = child.wait(timeout=180)
                except subprocess.TimeoutExpired:
                    child.kill()  # This is the TEST READER, never the FUSE worker.
                    try:
                        child.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass  # A blocked kernel read still needs the serving worker.
                    raise TimeoutError("Filesystem probe timed out; keep engine logs") from None
                if code:
                    raise RuntimeError(f"Probe failed ({code}); see {output.name}")
                process.send_signal(signal.SIGINT)
                try:
                    code = process.wait(timeout=45)
                except subprocess.TimeoutExpired:
                    raise RuntimeError(f"Unmount busy; engine PID {process.pid} retained. Close readers and send it SIGINT again") from None
                if code:
                    raise RuntimeError(f"Engine shutdown failed ({code}); see {output.name}")
                if mounted_below(root):
                    raise RuntimeError("Mount remains after worker shutdown; scratch preserved")
                print(f"PASS: mount/read/unmount cycle {iteration + 1}", flush=True)
        success = True
        print("PASS: native synthetic smoke test; X-Plane flight validation is still required", flush=True)
        return 0
    except (OSError, RuntimeError, AssertionError):
        if console is not None:
            print_log_tail(console)
        raise
    finally:
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT)
            print(f"Engine PID {process.pid} was asked to stop, NOT killed. Scratch retained: {root}", file=sys.stderr)
        if success and not args.keep and not mounted_below(root):
            shutil.rmtree(root)
        else:
            print(f"Preserved scratch/logs: {root}", file=sys.stderr)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, AssertionError) as exc:
        traceback.print_exc()
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
