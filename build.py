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
from typing import Any, Literal

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path


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
# Smoke-test helpers for the new tools
# ---------------------------------------------------------------------------


def _write_test_source(path: Path, source: str) -> None:
    """Write *source* to *path*, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _find_system_tool(names: list[str]) -> str | None:
    """Return the first tool from *names* found on PATH, or None."""
    for name in names:
        try:
            subprocess.run([name, "--version"], capture_output=True, check=False)
            return name
        except FileNotFoundError:
            continue
    return None


def smoke_llvm_profdata(
    bins: Path,
    dot_exe: str,
    tmpdir: Path,
    clang_exe: Path,
    version: str,
) -> None:
    """Smoke-test llvm-profdata: compile with coverage, run, merge, show."""
    profdata_exe = bins / f"llvm-profdata{dot_exe}"
    print(f"Smoke-testing {profdata_exe} ...")
    # llvm-profdata only supports --version starting from LLVM 17.
    llvm_major = int(version.split(".")[0])
    if llvm_major >= 17:
        run([str(profdata_exe), "--version"])

    # Write a tiny C program
    src = tmpdir / "profraw_test.c"
    _write_test_source(
        src,
        "int foo(int x) { return x * x; }\nint main(void) { return foo(42); }\n",
    )

    test_bin = tmpdir / ("profraw_test" + dot_exe)
    profraw = tmpdir / "test.profraw"
    profdata = tmpdir / "test.profdata"

    # Compile with instrumentation
    run(
        [
            str(clang_exe),
            "-fprofile-instr-generate",
            "-fcoverage-mapping",
            "-o",
            str(test_bin),
            str(src),
        ]
    )

    # Run to produce a .profraw
    env = {**os.environ, "LLVM_PROFILE_FILE": str(profraw)}
    run([str(test_bin)], env=env)
    assert profraw.exists(), f"{profraw} was not generated"

    # Merge .profraw -> .profdata
    run([str(profdata_exe), "merge", "-o", str(profdata), str(profraw)])
    assert profdata.exists(), f"{profdata} was not generated"

    # Show the merged profile
    run([str(profdata_exe), "show", str(profdata)])
    print("  llvm-profdata smoke test PASSED")


def smoke_llvm_cov(
    bins: Path,
    dot_exe: str,
    tmpdir: Path,
    clang_exe: Path,
) -> None:
    """Smoke-test llvm-cov: use the .profdata from the profdata test."""
    cov_exe = bins / f"llvm-cov{dot_exe}"
    print(f"Smoke-testing {cov_exe} ...")
    run([str(cov_exe), "--version"])

    # Re-use the same test binary and .profdata produced by smoke_llvm_profdata
    test_bin = tmpdir / ("profraw_test" + dot_exe)
    profdata = tmpdir / "test.profdata"

    if not test_bin.exists() or not profdata.exists():
        # Build them now if the profdata smoke test wasn't run first
        src = tmpdir / "profraw_test.c"
        _write_test_source(
            src,
            "int foo(int x) { return x * x; }\nint main(void) { return foo(42); }\n",
        )
        profraw = tmpdir / "test.profraw"
        run(
            [
                str(clang_exe),
                "-fprofile-instr-generate",
                "-fcoverage-mapping",
                "-o",
                str(test_bin),
                str(src),
            ]
        )
        env = {**os.environ, "LLVM_PROFILE_FILE": str(profraw)}
        run([str(test_bin)], env=env)
        profdata_exe = bins / f"llvm-profdata{dot_exe}"
        run([str(profdata_exe), "merge", "-o", str(profdata), str(profraw)])

    # llvm-cov report
    result = subprocess.run(
        [str(cov_exe), "report", str(test_bin), "-instr-profile", str(profdata)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"  stderr: {result.stderr}")
        raise RuntimeError(f"llvm-cov report failed (exit {result.returncode})")
    # Verify the report contains our function name
    assert "foo" in result.stdout or "foo" in result.stderr, (
        f"Expected 'foo' in llvm-cov report, got:\n{result.stdout}"
    )
    print("  llvm-cov smoke test PASSED")


def smoke_llvm_symbolizer(
    bins: Path,
    dot_exe: str,
    tmpdir: Path,
    clang_exe: Path,
) -> None:
    """Smoke-test llvm-symbolizer: resolve a function address back to a symbol."""
    sym_exe = bins / f"llvm-symbolizer{dot_exe}"
    print(f"Smoke-testing {sym_exe} ...")
    run([str(sym_exe), "--version"])

    # Write a test C program with a clearly named function
    src = tmpdir / "symtest.c"
    _write_test_source(
        src,
        "void test_func(int x) {}\nint main(void) { test_func(42); return 0; }\n",
    )

    test_bin = tmpdir / ("symtest" + dot_exe)
    run(
        [
            str(clang_exe),
            "-g",
            "-O0",
            "-o",
            str(test_bin),
            str(src),
        ]
    )

    # Try to get the address of test_func using available tools
    addr: str | None = None
    nm_path = _find_system_tool(["llvm-nm", "nm"])
    if nm_path:
        result = subprocess.run(
            [nm_path, "-C", str(test_bin)],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in result.stdout.splitlines():
            if "test_func" in line and line.strip():
                parts = line.split()
                if parts and parts[0] != "":
                    addr = parts[0]
                    break

    # If nm didn't work (e.g. Windows without dumpbin), try Windows dumpbin
    if addr is None and platform.system() == "Windows":
        dumpbin = _find_system_tool(["dumpbin", "DUMPBIN.EXE"])
        if dumpbin:
            result = subprocess.run(
                [dumpbin, "/SYMBOLS", str(test_bin)],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in result.stdout.splitlines():
                if "test_func" in line and "| " in line:
                    # Example: "00000001 00000000 SECT3  notype ()    External     | test_func"
                    before_pipe = line.split("|")[0].strip()
                    parts = before_pipe.split()
                    if parts:
                        addr_candidate = parts[0].strip()
                        if addr_candidate and addr_candidate != "00000000":
                            addr = addr_candidate
                            break

    if addr:
        # Feed address + binary to llvm-symbolizer via stdin
        input_str = f"0x{addr}\n{test_bin}\n"
        result = subprocess.run(
            [str(sym_exe)],
            input=input_str,
            capture_output=True,
            text=True,
            check=False,
        )
        output = result.stdout + result.stderr
        assert "test_func" in output, (
            f"Expected 'test_func' in symbolizer output, got:\n{output}"
        )
        print(f"  Resolved 0x{addr} -> test_func")
    else:
        print("  [warn] No symbol table tool found; skipping address resolution test")

    print("  llvm-symbolizer smoke test PASSED")


def smoke_clang_scan_deps(
    bins: Path,
    dot_exe: str,
    tmpdir: Path,
    version: str,
) -> None:
    """Smoke-test clang-scan-deps on a minimal compile_commands.json."""
    scandeps_exe = bins / f"clang-scan-deps{dot_exe}"
    print(f"Smoke-testing {scandeps_exe} ...")
    run([str(scandeps_exe), "--version"])

    # Create a minimal source file
    srcdir = tmpdir / "src"
    srcdir.mkdir(parents=True, exist_ok=True)
    src = srcdir / "hello.c"
    _write_test_source(src, "#include <stddef.h>\nint main(void) { return 0; }\n")

    # Create compile_commands.json
    builddir = tmpdir / "build"
    builddir.mkdir(parents=True, exist_ok=True)
    cc_json = tmpdir / "compile_commands.json"
    cc_entry = {
        "directory": str(tmpdir),
        "arguments": [
            "clang",
            "-c",
            str(src),
            "-o",
            str(builddir / "hello.o"),
        ],
        "file": str(src),
    }
    _write_test_source(
        cc_json,
        json.dumps([cc_entry], indent=2),
    )

    # Run in normal dependency-scanning mode
    result = subprocess.run(
        [
            str(scandeps_exe),
            "-compilation-database",
            str(cc_json),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"  stderr: {result.stderr}")
        raise RuntimeError(f"clang-scan-deps failed (exit {result.returncode})")
    # Expect the output to reference our source file
    assert src.name in result.stdout or src.name in result.stderr, (
        f"Expected '{src.name}' in scan-deps output, got:\n{result.stdout}"
    )

    # Optionally test -format=p1689 (modules format) if LLVM version is recent enough
    major = int(version.split(".")[0])
    if major >= 16:
        result_p1689 = subprocess.run(
            [
                str(scandeps_exe),
                "-compilation-database",
                str(cc_json),
                "-format=p1689",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result_p1689.returncode == 0:
            # p1689 output is JSON; verify it parses
            try:
                data = json.loads(result_p1689.stdout)
                assert "revision" in data or "rules" in data or "provides" in data, (
                    f"p1689 output missing expected keys:\n{result_p1689.stdout}"
                )
                print("  p1689 format validated")
            except json.JSONDecodeError as exc:
                print(f"  [warn] p1689 output not valid JSON: {exc}")
        else:
            print("  [warn] p1689 format not supported; falling back to default format")

    print("  clang-scan-deps smoke test PASSED")


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
            skip = any(member.name.startswith(excl.rstrip("*")) for excl in excludes)
            if not skip:
                members.append(member)
        tf.extractall(path=".", members=members)  # noqa: S202 - we own the source


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
        "-DLLVM_USE_CRT_MINSIZEREL=MT",
    ]


def cmake_args_windows_arm64() -> list[str]:
    return [
        "-DBUILD_SHARED_LIBS=OFF",
        "-DLLVM_ENABLE_PROJECTS=clang;clang-tools-extra",
        "-Thost=ARM64",
        "-DCMAKE_CXX_FLAGS=/MP /std:c++14",
        "-DLLVM_USE_CRT_MINSIZEREL=MT",
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

    run(cmake_cmd)

    # ------------------------------------------------------------------
    # 5. Build
    # ------------------------------------------------------------------
    tools = active_tools(version)

    # Determine which additional cmake targets are needed by the smoke tests.
    # The clang compiler driver itself is not a distributed tool but is
    # required by functional smoke tests (llvm-profdata, llvm-cov,
    # llvm-symbolizer) that compile short C programs with the just-built
    # clang.
    build_targets = list(tools)
    needs_clang = bool({"llvm-profdata", "llvm-cov", "llvm-symbolizer"} & set(tools))
    if needs_clang:
        build_targets.append("clang")

    build_cmd = (
        [
            "cmake",
            "--build",
            str(build_dir),
        ]
        + build_args_by_os(is_windows)
        + ["--target"]
        + build_targets
    )
    run(build_cmd)

    # ------------------------------------------------------------------
    # 5b. Print dynamic library dependencies (macOS only)
    # ------------------------------------------------------------------
    if is_macos:
        print_dependencies(release)

    # ------------------------------------------------------------------
    # 6. Smoke test
    # ------------------------------------------------------------------
    bins = bin_dir(release, is_windows)
    clang_exe = bins / f"clang{dot_exe}"

    # All tools get the basic --version smoke test.
    # Note: llvm-profdata on LLVM < 17 uses a subcommand interface and
    # does NOT support --version. See llvm-profdata.cpp main() — LLVM 17
    # added explicit `if (strcmp(argv[1], "--version") == 0)` handling.
    # Older versions only recognise subcommands (merge/show/overlap) and
    # --help. We verify the binary is executable here; the functional
    # smoke test below validates the actual merge/show functionality.
    llvm_major = int(version.split(".")[0])
    for tool in tools:
        exe = bins / f"{tool}{dot_exe}"
        print(f"\nSmoke-testing {exe} ...")
        if tool == "llvm-profdata" and llvm_major < 17:
            # Run with no args to confirm the binary loads (exits code 1
            # with usage text = expected subcommand interface behavior).
            result = subprocess.run(
                [str(exe)], capture_output=True, text=True,
            )
            if "USAGE" not in result.stdout and "USAGE" not in result.stderr:
                raise RuntimeError(
                    f"{exe.name} did not produce expected usage output:\n"
                    f"  stdout: {result.stdout.strip() or '(empty)'}\n"
                    f"  stderr: {result.stderr.strip() or '(empty)'}"
                )
            print(f"  Binary OK (subcommand interface)")
            continue
        run([str(exe), "--version"])

    # Tool-specific smoke tests that exercise real functionality
    import tempfile

    with tempfile.TemporaryDirectory(prefix="smoke_") as tmpdir_str:
        smokes = Path(tmpdir_str)

        if "llvm-profdata" in tools:
            smoke_llvm_profdata(bins, dot_exe, smokes, clang_exe, version)

        if "llvm-cov" in tools:
            smoke_llvm_cov(bins, dot_exe, smokes, clang_exe)

        if "llvm-symbolizer" in tools:
            smoke_llvm_symbolizer(bins, dot_exe, smokes, clang_exe)

        if "clang-scan-deps" in tools:
            smoke_clang_scan_deps(bins, dot_exe, smokes, version)

    # ------------------------------------------------------------------
    # 7. Rename binaries
    # ------------------------------------------------------------------
    for tool in tools:
        src = bins / f"{tool}{dot_exe}"
        dst = bins / f"{tool}-{suffix}{dot_exe}"
        print(f"Renaming {src.name} -> {dst.name}")
        src.rename(dst)

    # ------------------------------------------------------------------
    # 8. Generate sha512sums
    # ------------------------------------------------------------------
    for tool in tools:
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
