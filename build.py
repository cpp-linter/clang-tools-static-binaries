#!/usr/bin/env python3
"""
build.py - local build script for clang-tools static binaries.

This script mirrors the steps in .github/workflows/build.yml so that a build
can be reproduced on any supported platform without needing GitHub Actions.

Supported platforms
-------------------
  linux        - Linux x86-64  (requires gcc-10, cmake, ninja/make)
  macosx       - macOS ARM64   (requires brew, gcc@14, cmake)
  macos-intel  - macOS x86-64  (requires brew, gcc@14, cmake)
  windows      - Windows x86-64 (requires Visual Studio with C++ tools, cmake)

Usage
-----
  python build.py --version 18
  python build.py --version 17 --os macosx
  python build.py --version 20 --os linux --build-dir /tmp/llvm-build
  python build.py --help
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Version -> source release mapping (must stay in sync with build.yml)
# ---------------------------------------------------------------------------
RELEASES: dict[str, str] = {
    "22": "llvm-project-22.1.0.src",
    "21": "llvm-project-21.1.0.src",
    "20": "llvm-project-20.1.0.src",
    "19": "llvm-project-19.1.0.src",
    "18": "llvm-project-18.1.5.src",
    "17": "llvm-project-17.0.6.src",
    "16": "llvm-project-16.0.3.src",
    "15": "llvm-project-15.0.2.src",
    "14": "llvm-project-14.0.0.src",
    "13": "llvm-project-13.0.0.src",
    "12.0.1": "llvm-project-12.0.1.src",
    "12": "llvm-project-12.0.0.src",
    "11": "llvm-project-11.1.0.src",
}

TOOLS = ["clang-format", "clang-query", "clang-tidy", "clang-apply-replacements"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(cmd: list[str], **kwargs) -> None:
    """Run a command, raising CalledProcessError on failure."""
    print(f"\n>>> {' '.join(str(c) for c in cmd)}", flush=True)
    subprocess.run(cmd, check=True, **kwargs)


def detect_os() -> str:
    """Return the platform key matching build.yml matrix names."""
    system = platform.system().lower()
    if system == "linux":
        return "linux"
    if system == "darwin":
        machine = platform.machine().lower()
        return "macosx" if machine == "arm64" else "macos-intel"
    if system == "windows":
        return "windows"
    raise RuntimeError(f"Unsupported operating system: {system!r}")


def os_arch(os_name: str) -> str:
    """Return the architecture string used in binary suffixes."""
    return "arm64" if os_name == "macosx" else "amd64"


def sha512_file(path: Path) -> str:
    """Return the hex sha512 digest of *path*."""
    h = hashlib.sha512()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_file(url: str, dest: Path) -> None:
    """Download *url* to *dest* with a simple progress indicator."""
    if dest.exists():
        print(f"[skip] {dest.name} already downloaded.")
        return
    print(f"Downloading {url} ...", flush=True)
    tmp = dest.with_suffix(".tmp")
    try:
        with urllib.request.urlopen(url) as resp, open(tmp, "wb") as fh:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            block = 1 << 16
            while True:
                data = resp.read(block)
                if not data:
                    break
                fh.write(data)
                downloaded += len(data)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\r  {pct:3d}%", end="", flush=True)
        print()
        tmp.rename(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def unpack_tarball(tarball: Path, release: str, extra_excludes: list[str]) -> None:
    """Extract *tarball*, skipping paths listed in *extra_excludes*."""
    release_dir = Path(release)
    if release_dir.exists():
        print(f"[skip] {release} already unpacked.")
        return
    print(f"Unpacking {tarball.name} ...", flush=True)
    # Build a set of path prefixes to skip
    excludes = set(extra_excludes)

    with tarfile.open(tarball, "r:xz") as tf:
        members = []
        for member in tf.getmembers():
            skip = any(
                member.name.startswith(excl.rstrip("*")) for excl in excludes
            )
            if not skip:
                members.append(member)
        tf.extractall(path=".", members=members)  # noqa: S202 - we own the source


def patch_cmake_implicit_link_macos() -> None:
    """Patch brew's CMakeParseImplicitLinkInfo.cmake to recognise gcc_ext."""
    try:
        brew_prefix = subprocess.check_output(
            ["brew", "--prefix"], text=True
        ).strip()
    except FileNotFoundError:
        print("[warn] brew not found; skipping cmake implicit-link-library patch.")
        return

    cmake_files = list(Path(brew_prefix).rglob("CMakeParseImplicitLinkInfo.cmake"))
    if not cmake_files:
        print("[warn] CMakeParseImplicitLinkInfo.cmake not found under brew prefix.")
        return

    for cmake_file in cmake_files:
        content = cmake_file.read_text()
        patched = content.replace("gcc_eh.*|", "gcc_eh.*|gcc_ext.*|")
        if patched != content:
            print(f"Patching {cmake_file}")
            cmake_file.write_text(patched)
        else:
            print(f"[skip] {cmake_file} already patched or pattern not found.")


def apply_patch(patch_file: Path, target_dir: Path) -> None:
    """Apply a unified diff patch inside *target_dir*."""
    run(["patch", "-p1", "-d", str(target_dir), "-i", str(patch_file.resolve())])


def print_dependencies(release: str) -> None:
    """Print dynamic library dependencies of clang-format (macOS only)."""
    clang_format = Path(release) / "build" / "bin" / "clang-format"
    if clang_format.exists():
        run(["otool", "-L", str(clang_format)])
    else:
        print(f"[warn] {clang_format} not found; skipping dependency listing.")


# ---------------------------------------------------------------------------
# Platform-specific cmake argument builders
# ---------------------------------------------------------------------------


def cmake_args_linux() -> list[str]:
    return [
        "-DBUILD_SHARED_LIBS=OFF",
        '-DLLVM_ENABLE_PROJECTS=clang;clang-tools-extra',
        "-DLLVM_BUILD_STATIC=ON",
        "-DCMAKE_BUILD_TYPE=MinSizeRel",
        '-DCMAKE_CXX_FLAGS=-s -flto',
        "-DCMAKE_CXX_COMPILER=g++-10",
        "-DCMAKE_C_COMPILER=gcc-10",
    ]


def cmake_args_macosx() -> list[str]:
    return [
        "-DBUILD_SHARED_LIBS=OFF",
        '-DLLVM_ENABLE_PROJECTS=clang;clang-tools-extra',
        "-DCMAKE_BUILD_TYPE=MinSizeRel",
        "-DCMAKE_CXX_FLAGS=-static-libgcc -static-libstdc++ -flto -ffunction-sections -fdata-sections",
        "-DCMAKE_EXE_LINKER_FLAGS=-Wl,-dead_strip",
        "-DCMAKE_OSX_DEPLOYMENT_TARGET=11.0",
        "-DCMAKE_CXX_COMPILER=g++-14",
        "-DCMAKE_C_COMPILER=gcc-14",
        "-DLLVM_TARGETS_TO_BUILD=AArch64",
        "-DLLVM_ENABLE_ZSTD=OFF",
        "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
    ]


def cmake_args_macos_intel() -> list[str]:
    return [
        "-DBUILD_SHARED_LIBS=OFF",
        '-DLLVM_ENABLE_PROJECTS=clang;clang-tools-extra',
        "-DCMAKE_BUILD_TYPE=MinSizeRel",
        "-DCMAKE_CXX_FLAGS=-static-libgcc -static-libstdc++ -flto -ffunction-sections -fdata-sections",
        "-DCMAKE_EXE_LINKER_FLAGS=-Wl,-dead_strip",
        "-DCMAKE_OSX_DEPLOYMENT_TARGET=11.0",
        "-DCMAKE_CXX_COMPILER=g++-14",
        "-DCMAKE_C_COMPILER=gcc-14",
        "-DLLVM_TARGETS_TO_BUILD=X86",
        "-DLLVM_ENABLE_ZSTD=OFF",
        "-DLLVM_ENABLE_ZLIB=OFF",
        "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
    ]


def cmake_args_windows() -> list[str]:
    return [
        "-DBUILD_SHARED_LIBS=OFF",
        '-DLLVM_ENABLE_PROJECTS=clang;clang-tools-extra',
        "-Thost=x64",
        "-DCMAKE_CXX_FLAGS=/MP /std:c++14",
        "-DLLVM_USE_CRT_MINSIZEREL=MT",
    ]


CMAKE_ARGS_BY_OS = {
    "linux": cmake_args_linux,
    "macosx": cmake_args_macosx,
    "macos-intel": cmake_args_macos_intel,
    "windows": cmake_args_windows,
}


def build_args_by_os(os_name: str) -> list[str]:
    if os_name == "windows":
        return ["--config", "MinSizeRel"]
    cpu_count = os.cpu_count() or 1
    return [f"-j{cpu_count}"]


def bin_dir(release: str, os_name: str) -> Path:
    sub = "MinSizeRel/bin" if os_name == "windows" else "bin"
    return Path(release) / "build" / sub


# ---------------------------------------------------------------------------
# Main build logic
# ---------------------------------------------------------------------------


def build(version: str, os_name: str, script_dir: Path) -> None:
    release = RELEASES[version]
    suffix = f"{version}_{os_name}-{os_arch(os_name)}"
    dot_exe = ".exe" if os_name == "windows" else ""

    print(f"\n{'='*60}")
    print(f"Building clang-tools {version} for {os_name}")
    print(f"  release : {release}")
    print(f"  suffix  : {suffix}")
    print(f"{'='*60}\n")

    # ------------------------------------------------------------------
    # 1. Download source tarball
    # ------------------------------------------------------------------
    tarball = Path(f"{release}.tar.xz")
    ver_tag = release[len("llvm-project-"):-len(".src")]
    url = (
        f"https://github.com/llvm/llvm-project/releases/download/"
        f"llvmorg-{ver_tag}/{release}.tar.xz"
    )
    download_file(url, tarball)

    # ------------------------------------------------------------------
    # 2. Unpack
    # ------------------------------------------------------------------
    extra_excludes: list[str] = []
    if os_name == "windows":
        extra_excludes = [
            f"{release}/clang/test/Driver/Inputs/",
            f"{release}/libcxx/test/std/input.output/filesystems/Inputs/static_test_env/",
            f"{release}/libclc/amdgcn-mesa3d",
        ]
    unpack_tarball(tarball, release, extra_excludes)

    # ------------------------------------------------------------------
    # 3. Platform-specific patches
    # ------------------------------------------------------------------
    if os_name in ("macosx", "macos-intel"):
        patch_cmake_implicit_link_macos()

    if os_name == "macosx" and version == "17":
        patch_path = script_dir / "arm_streaming_fix.patch"
        if patch_path.exists():
            apply_patch(patch_path, Path(release))
        else:
            print(f"[warn] Patch not found at {patch_path}; skipping ARM streaming fix.")

    # ------------------------------------------------------------------
    # 4. CMake configure
    # ------------------------------------------------------------------
    source_dir = Path(release) / "llvm"
    build_dir = Path(release) / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    cmake_cmd = [
        "cmake",
        "-S", str(source_dir),
        "-B", str(build_dir),
    ] + CMAKE_ARGS_BY_OS[os_name]()

    run(cmake_cmd)

    # ------------------------------------------------------------------
    # 5. Build
    # ------------------------------------------------------------------
    build_cmd = [
        "cmake", "--build", str(build_dir),
    ] + build_args_by_os(os_name) + [
        "--target",
        "clang-format", "clang-query", "clang-tidy", "clang-apply-replacements",
    ]
    run(build_cmd)

    # ------------------------------------------------------------------
    # 5b. Print dynamic library dependencies (macOS only)
    # ------------------------------------------------------------------
    if os_name in ("macosx", "macos-intel"):
        print_dependencies(release)

    # ------------------------------------------------------------------
    # 6. Smoke test
    # ------------------------------------------------------------------
    bins = bin_dir(release, os_name)
    for tool in TOOLS:
        exe = bins / f"{tool}{dot_exe}"
        print(f"\nSmoke-testing {exe} ...")
        run([str(exe), "--version"])

    # ------------------------------------------------------------------
    # 7. Rename binaries
    # ------------------------------------------------------------------
    for tool in TOOLS:
        src = bins / f"{tool}{dot_exe}"
        dst = bins / f"{tool}-{suffix}{dot_exe}"
        print(f"Renaming {src.name} -> {dst.name}")
        src.rename(dst)

    # ------------------------------------------------------------------
    # 8. Generate sha512sums
    # ------------------------------------------------------------------
    for tool in TOOLS:
        binary = bins / f"{tool}-{suffix}{dot_exe}"
        digest = sha512_file(binary)
        sha_file = bins / f"{tool}-{suffix}{dot_exe}.sha512sum"
        sha_file.write_text(f"{digest}  {binary.name}\n")
        print(f"{digest}  {binary.name}")

    print(f"\nBuild complete. Artifacts are in: {bins.resolve()}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build clang-tools static binaries locally.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--version",
        "-v",
        required=True,
        choices=sorted(RELEASES),
        metavar="VERSION",
        help=(
            "Clang version to build. "
            f"Supported: {', '.join(sorted(RELEASES, key=lambda x: float(x) if '.' not in x or x.count('.') == 1 else float(x.rsplit('.', 1)[0])))}"
        ),
    )
    parser.add_argument(
        "--os",
        choices=["linux", "macosx", "macos-intel", "windows"],
        default=None,
        help=(
            "Target OS/platform. Defaults to auto-detected host OS. "
            "linux=Linux x86-64, macosx=macOS ARM64, "
            "macos-intel=macOS x86-64, windows=Windows x86-64."
        ),
    )
    parser.add_argument(
        "--build-dir",
        default=None,
        metavar="DIR",
        help="Working directory for downloads and build artifacts (default: current directory).",
    )

    args = parser.parse_args()

    os_name = args.os or detect_os()
    script_dir = Path(__file__).parent.resolve()

    if args.build_dir:
        build_path = Path(args.build_dir)
        build_path.mkdir(parents=True, exist_ok=True)
        os.chdir(build_path)

    try:
        build(args.version, os_name, script_dir)
    except subprocess.CalledProcessError as exc:
        print(f"\nBuild failed (exit code {exc.returncode}).", file=sys.stderr)
        sys.exit(exc.returncode)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
