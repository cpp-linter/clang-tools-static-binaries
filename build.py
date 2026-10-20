#!/usr/bin/env python3
"""
build.py - local build script for clang-tools static binaries.

This script mirrors the steps in .github/workflows/build.yml so that a build
can be reproduced on any supported platform without needing GitHub Actions.

Supported platforms
-------------------
  linux-amd64    - Linux x86-64   (requires gcc-10, cmake, ninja/make)
  linux-arm64    - Linux ARM64    (requires gcc-10, cmake, ninja/make)
  macos-amd64    - macOS x86-64   (requires brew, gcc@14, cmake)
  macos-arm64    - macOS ARM64    (requires brew, gcc@14, cmake)
  windows-amd64  - Windows x86-64 (requires Visual Studio with C++ tools, cmake)
  windows-arm64  - Windows ARM64  (requires Visual Studio with C++ tools, cmake)

Usage
-----
  python build.py --version 18
  python build.py --version 17 --platform macos-arm64
  python build.py --version 20 --platform linux-amd64 --build-dir /tmp/llvm-build
  python build.py --help
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import struct
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Version -> source release mapping (loaded from releases.json)
# ---------------------------------------------------------------------------
def _load_releases() -> dict[str, str]:
    """Load the version-to-tarball mapping from releases.json."""
    releases_path = Path(__file__).parent / "releases.json"
    with open(releases_path) as f:
        return json.load(f)


RELEASES: dict[str, str] = _load_releases()

TOOLS = [
    "clang-format",
    "clang-query",
    "clang-tidy",
    "clang-apply-replacements",
    "clang-include-cleaner",  # available starting LLVM 18
    "llvm-cov",
    "llvm-profdata",
    "llvm-symbolizer",
    "clang-scan-deps",
]

# Minimum LLVM major version for tools that were introduced after LLVM 11.
INCLUDE_CLEANER_MIN_VERSION = 18
CLANG_SCAN_DEPS_MIN_VERSION = 12


def active_tools(version: str) -> list[str]:
    """Return the list of tools that are buildable for *version*.

    clang-include-cleaner was introduced as a standalone tool in LLVM 18.
    Earlier versions only had it as a library, not a build target.

    clang-scan-deps became available as a standalone tool in LLVM 12.
    Earlier versions (11) only had it as an experimental library.
    """
    tools = list(TOOLS)
    major = int(version.split(".")[0])
    if major < INCLUDE_CLEANER_MIN_VERSION:
        tools.remove("clang-include-cleaner")
    if major < CLANG_SCAN_DEPS_MIN_VERSION:
        tools.remove("clang-scan-deps")
    return tools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(cmd: list[str], **kwargs: Any) -> None:
    """Run a command, raising CalledProcessError on failure."""
    print(f"\n>>> {' '.join(str(c) for c in cmd)}", flush=True)
    subprocess.run(cmd, check=True, **kwargs)


def detect_os() -> str:
    """Return the platform key matching build.yml matrix names."""
    system_name = platform.system().lower()

    match system_name:
        case "linux" | "windows":
            return system_name

        case "darwin":
            return "macos"

        case _:
            raise RuntimeError(f"Unsupported operating system: {system_name!r}")


def detect_arch() -> Literal["amd64", "arm64"]:
    """Return the architecture string used in binary suffixes."""
    machine_type = platform.machine().lower()
    return "arm64" if machine_type in ("arm64", "aarch64") else "amd64"


def sha512_file(path: Path) -> str:
    """Return the hex sha512 digest of *path*."""
    h = hashlib.sha512()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_file(url: str, dest: Path) -> None:
    """Download *url* to *dest*."""
    if dest.exists():
        print(f"[skip] {dest.name} already downloaded.")
        return
    print(f"Downloading {url} ...", flush=True)
    tmp = dest.with_suffix(".tmp")
    try:
        urllib.request.urlretrieve(url, tmp)
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
            skip = any(member.name.startswith(excl.rstrip("*")) for excl in excludes)
            if not skip:
                members.append(member)
        tf.extractall(path=".", members=members)


def patch_cmake_implicit_link_macos() -> None:
    """Patch brew's CMakeParseImplicitLinkInfo.cmake to recognise gcc_ext."""
    try:
        brew_prefix = subprocess.check_output(["brew", "--prefix"], text=True).strip()
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


# The binaries must run on a clean machine, so they may only load libraries
# that come with the operating system.
MACOS_SYSTEM_LIBRARY_DIRS = ("/usr/lib/", "/System/Library/")
# A clean Windows install may lack the Visual C++ runtime DLLs, and installing
# the Visual C++ Redistributable needs admin rights, so the runtime is linked
# statically (/MT) and none of its DLLs may show up in the import table.
WINDOWS_RUNTIME_DLL_PREFIXES = ("msvcp", "vcruntime", "ucrtbase", "api-ms-win-crt-")


def pe_imports(path: Path) -> list[str]:
    """Return the DLLs in the import table of the Windows executable *path*."""
    data = path.read_bytes()
    (pe_offset,) = struct.unpack_from("<I", data, 0x3C)
    if data[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise RuntimeError(f"{path} is not a PE file.")
    num_sections, optional_size = struct.unpack_from("<H12xH", data, pe_offset + 6)
    optional = pe_offset + 24
    (magic,) = struct.unpack_from("<H", data, optional)
    # Data directory 1 is the import table; its offset depends on PE32/PE32+.
    data_dirs = optional + (112 if magic == 0x20B else 96)
    (import_rva,) = struct.unpack_from("<I", data, data_dirs + 8)
    sections = [
        struct.unpack_from("<8xIIII", data, optional + optional_size + 40 * i)
        for i in range(num_sections)
    ]

    def file_offset(rva: int) -> int:
        for virtual_size, virtual_address, raw_size, raw_pointer in sections:
            if virtual_address <= rva < virtual_address + max(virtual_size, raw_size):
                return rva - virtual_address + raw_pointer
        raise RuntimeError(f"RVA {rva:#x} is outside every section of {path}.")

    dlls: list[str] = []
    if import_rva == 0:
        return dlls
    descriptor = file_offset(import_rva)
    while data[descriptor : descriptor + 20] != bytes(20):
        (name_rva,) = struct.unpack_from("<I", data, descriptor + 12)
        name = file_offset(name_rva)
        dlls.append(data[name : data.index(b"\0", name)].decode("ascii"))
        descriptor += 20
    return dlls


def elf_interpreter(path: Path) -> str | None:
    """Return the dynamic loader of the ELF executable *path*, or None if static."""
    with open(path, "rb") as fh:
        header = fh.read(64)
        if header[:4] != b"\x7fELF" or header[4] != 2 or header[5] != 1:
            raise RuntimeError(f"{path} is not a 64-bit little-endian ELF file.")
        (ph_offset,) = struct.unpack_from("<Q", header, 0x20)
        ph_size, ph_count = struct.unpack_from("<HH", header, 0x36)
        for i in range(ph_count):
            fh.seek(ph_offset + i * ph_size)
            p_type, _, p_offset, _, _, p_filesz = struct.unpack("<IIQQQQ", fh.read(40))
            if p_type == 3:  # PT_INTERP
                fh.seek(p_offset)
                return fh.read(p_filesz).rstrip(b"\0").decode()
    return None


def macos_dylibs(path: Path) -> list[str]:
    """Return the shared libraries the macOS executable *path* links to."""
    output = subprocess.check_output(["otool", "-L", str(path)], text=True)
    return [line.split()[0] for line in output.splitlines() if line.startswith("\t")]


def check_dependencies(bins: Path, tools: list[str], target_platform: str) -> None:
    """Fail if a built tool needs a shared library that a clean machine may lack."""
    problems = []
    for tool in tools:
        if target_platform.startswith("windows"):
            exe = bins / f"{tool}.exe"
            deps = pe_imports(exe)
            bad = [
                d for d in deps if d.lower().startswith(WINDOWS_RUNTIME_DLL_PREFIXES)
            ]
        elif target_platform.startswith("macos"):
            exe = bins / tool
            deps = macos_dylibs(exe)
            bad = [d for d in deps if not d.startswith(MACOS_SYSTEM_LIBRARY_DIRS)]
        else:
            exe = bins / tool
            interpreter = elf_interpreter(exe)
            deps = [interpreter] if interpreter else []
            bad = deps
        print(f"{exe.name}: {', '.join(deps) or 'no shared libraries'}")
        if bad:
            problems.append(f"{exe.name}: {', '.join(bad)}")
    if problems:
        raise RuntimeError(
            "These binaries need shared libraries that a clean machine may not have:\n  "
            + "\n  ".join(problems)
        )


def windows_runtime_args(llvm_major: int) -> list[str]:
    """CMake arguments that link the C/C++ runtime statically (/MT) on Windows.

    LLVM 18 removed the LLVM_USE_CRT_<CONFIG> options in favour of CMake's
    CMAKE_MSVC_RUNTIME_LIBRARY and ignores them silently: with only the old
    option, LLVM 18 and later link the DLL runtime (MSVCP140.dll and others).
    """
    if llvm_major >= 18:
        return ["-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded"]
    return ["-DLLVM_USE_CRT_MINSIZEREL=MT"]


# ---------------------------------------------------------------------------
# Platform-specific cmake argument builders
# ---------------------------------------------------------------------------


def cmake_args_linux_amd64() -> list[str]:
    return [
        "-DBUILD_SHARED_LIBS=OFF",
        "-DLLVM_ENABLE_PROJECTS=clang;clang-tools-extra",
        "-DLLVM_BUILD_STATIC=ON",
        "-DCMAKE_BUILD_TYPE=MinSizeRel",
        "-DCMAKE_CXX_FLAGS=-s -flto",
        "-DCMAKE_CXX_COMPILER=g++-10",
        "-DCMAKE_C_COMPILER=gcc-10",
    ]


def cmake_args_linux_arm64() -> list[str]:
    return [
        "-DBUILD_SHARED_LIBS=OFF",
        "-DLLVM_ENABLE_PROJECTS=clang;clang-tools-extra",
        "-DLLVM_BUILD_STATIC=ON",
        "-DCMAKE_BUILD_TYPE=MinSizeRel",
        "-DCMAKE_CXX_FLAGS=-s -flto",
        "-DCMAKE_CXX_COMPILER=g++-10",
        "-DCMAKE_C_COMPILER=gcc-10",
    ]


def cmake_args_macos_amd64() -> list[str]:
    return [
        "-DBUILD_SHARED_LIBS=OFF",
        "-DLLVM_ENABLE_PROJECTS=clang;clang-tools-extra",
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


def cmake_args_macos_arm64() -> list[str]:
    return [
        "-DBUILD_SHARED_LIBS=OFF",
        "-DLLVM_ENABLE_PROJECTS=clang;clang-tools-extra",
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


def cmake_args_windows_amd64() -> list[str]:
    return [
        "-DBUILD_SHARED_LIBS=OFF",
        "-DLLVM_ENABLE_PROJECTS=clang;clang-tools-extra",
        "-Thost=x64",
        "-DCMAKE_CXX_FLAGS=/MP /std:c++14",
    ]


def cmake_args_windows_arm64() -> list[str]:
    return [
        "-DBUILD_SHARED_LIBS=OFF",
        "-DLLVM_ENABLE_PROJECTS=clang;clang-tools-extra",
        "-Thost=ARM64",
        "-DCMAKE_CXX_FLAGS=/MP /std:c++14",
        # Building every LLVM backend runs the windows-11-arm runner out of
        # memory; the tools only need the native one (as on macOS).
        "-DLLVM_TARGETS_TO_BUILD=AArch64",
    ]


CMAKE_ARGS_BY_OS = {
    "linux-amd64": cmake_args_linux_amd64,
    "linux-arm64": cmake_args_linux_arm64,
    "macos-amd64": cmake_args_macos_amd64,
    "macos-arm64": cmake_args_macos_arm64,
    "windows-amd64": cmake_args_windows_amd64,
    "windows-arm64": cmake_args_windows_arm64,
}


def build_args_by_os(is_windows: bool) -> list[str]:
    if is_windows:
        return ["--config", "MinSizeRel"]

    cpu_count = os.cpu_count() or 1
    return [f"-j{cpu_count}"]


def bin_dir(release: str, is_windows: bool) -> Path:
    sub = "MinSizeRel/bin" if is_windows else "bin"
    return Path(release) / "build" / sub


# ---------------------------------------------------------------------------
# Main build logic
# ---------------------------------------------------------------------------


def build(version: str, target_platform: str, script_dir: Path) -> None:
    is_macos = target_platform.startswith("macos")
    is_windows = target_platform.startswith("windows")
    is_arm = target_platform.endswith("arm64")
    llvm_major = int(version.split(".")[0])

    release = RELEASES[version]
    suffix = f"{version}_{target_platform}"
    dot_exe = ".exe" if is_windows else ""

    print(f"\n{'=' * 60}")
    print(f"Building clang-tools {version} for {target_platform}")
    print(f"  release : {release}")
    print(f"  suffix  : {suffix}")
    print(f"{'=' * 60}\n")

    # ------------------------------------------------------------------
    # 1. Download source tarball
    # ------------------------------------------------------------------
    tarball = Path(f"{release}.tar.xz")
    ver_tag = release[len("llvm-project-") : -len(".src")]
    url = (
        f"https://github.com/llvm/llvm-project/releases/download/"
        f"llvmorg-{ver_tag}/{release}.tar.xz"
    )
    download_file(url, tarball)

    # ------------------------------------------------------------------
    # 2. Unpack
    # ------------------------------------------------------------------
    extra_excludes: list[str] = []
    if is_windows:
        extra_excludes = [
            f"{release}/clang/test/Driver/Inputs/",
            f"{release}/libcxx/test/std/input.output/filesystems/Inputs/static_test_env/",
            f"{release}/libclc/amdgcn-mesa3d",
        ]
    unpack_tarball(tarball, release, extra_excludes)

    # ------------------------------------------------------------------
    # 3. Platform-specific patches
    # ------------------------------------------------------------------
    if is_macos:
        patch_cmake_implicit_link_macos()

    if is_arm and version == "17":
        patch_path = script_dir / "arm_streaming_fix.patch"
        if patch_path.exists():
            apply_patch(patch_path, Path(release))
        else:
            print(
                f"[warn] Patch not found at {patch_path}; skipping ARM streaming fix."
            )

    # ------------------------------------------------------------------
    # 4. CMake configure
    # ------------------------------------------------------------------
    source_dir = Path(release) / "llvm"
    build_dir = Path(release) / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    cmake_cmd = [
        "cmake",
        "-S",
        str(source_dir),
        "-B",
        str(build_dir),
    ] + CMAKE_ARGS_BY_OS[target_platform]()
    if is_windows:
        cmake_cmd += windows_runtime_args(llvm_major)

    run(cmake_cmd)

    # ------------------------------------------------------------------
    # 5. Build
    # ------------------------------------------------------------------
    tools = active_tools(version)
    build_cmd = (
        [
            "cmake",
            "--build",
            str(build_dir),
        ]
        + build_args_by_os(is_windows)
        + ["--target"]
        + tools
    )
    run(build_cmd)

    # ------------------------------------------------------------------
    # 5b. Check that the binaries only need libraries the OS ships
    # ------------------------------------------------------------------
    bins = bin_dir(release, is_windows)
    check_dependencies(bins, tools, target_platform)

    # ------------------------------------------------------------------
    # 6. Smoke test
    # ------------------------------------------------------------------
    # Basic --version smoke test for every built tool.
    # Note: llvm-profdata on LLVM < 17 uses a subcommand interface and
    # does NOT support --version. See llvm-profdata.cpp main() — LLVM 17
    # added explicit `if (strcmp(argv[1], "--version") == 0)` handling.
    # Older versions only recognise subcommands (merge/show/overlap) and
    # --help. We verify the binary is executable by running it with no
    # args and checking for the expected usage output.
    for tool in tools:
        exe = bins / f"{tool}{dot_exe}"
        print(f"\nSmoke-testing {exe} ...")
        if tool == "llvm-profdata" and llvm_major < 17:
            result = subprocess.run(
                [str(exe)],
                capture_output=True,
                text=True,
                check=False,
            )
            if "USAGE" not in result.stdout and "USAGE" not in result.stderr:
                raise RuntimeError(
                    f"{exe.name} did not produce expected usage output:\n"
                    f"  stdout: {result.stdout.strip() or '(empty)'}\n"
                    f"  stderr: {result.stderr.strip() or '(empty)'}"
                )
            print("  Binary OK (subcommand interface)")
            continue
        run([str(exe), "--version"])

    # ------------------------------------------------------------------
    # 7. Rename binaries
    # ------------------------------------------------------------------
    for tool in tools:
        src = bins / f"{tool}{dot_exe}"
        dst = bins / f"{tool}-{suffix}{dot_exe}"
        print(f"Renaming {src.name} -> {dst.name}")
        src.rename(dst)

    # ------------------------------------------------------------------
    # 8. Generate SHA512SUMS (single file with all checksums)
    # ------------------------------------------------------------------
    sha512sums_path = bins / "SHA512SUMS"
    with open(sha512sums_path, "w") as sha_file:
        for tool in tools:
            binary = bins / f"{tool}-{suffix}{dot_exe}"
            digest = sha512_file(binary)
            sha_file.write(f"{digest}  {binary.name}\n")
            print(f"{digest}  {binary.name}")
    print(f"Checksums written to {sha512sums_path.name}")

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
        "--platform",
        "-p",
        choices=[
            "linux-amd64",
            "linux-arm64",
            "macos-amd64",
            "macos-arm64",
            "windows-amd64",
            "windows-arm64",
        ],
        default=None,
        help=(
            "Target platform. Defaults to auto-detected host platform. "
            "linux-amd64=Linux x86-64, linux-arm64=Linux ARM64, "
            "macos-amd64=macOS x86-64, macos-arm64=macOS ARM64, "
            "windows-amd64=Windows x86-64, "
            "windows-arm64=Windows ARM64."
        ),
    )
    parser.add_argument(
        "--build-dir",
        "-b",
        default=None,
        metavar="DIR",
        help="Working directory for downloads and build artifacts (default: current directory).",
    )

    arguments = parser.parse_args()
    target_platform = arguments.platform or f"{detect_os()}-{detect_arch()}"
    script_dir = Path(__file__).parent.resolve()

    if arguments.build_dir:
        build_path = Path(arguments.build_dir)
        build_path.mkdir(parents=True, exist_ok=True)
        os.chdir(build_path)

    try:
        build(arguments.version, target_platform, script_dir)
    except subprocess.CalledProcessError as exc:
        print(f"\nBuild failed (exit code {exc.returncode}).", file=sys.stderr)
        sys.exit(exc.returncode)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
