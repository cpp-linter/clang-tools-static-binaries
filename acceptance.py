#!/usr/bin/env python3
"""Install one version's binaries the way users do and run them on real code.

build.yml runs this on every platform before a version is published: a
version gets a release only when every platform passes. It also works on
any published release, which shows what users get today:

  python acceptance.py --version 21 --tag llvm-21.1.0
  python acceptance.py --version 21 --files <directory with downloaded files>

The tools are installed in the layout that installers should use:

  <prefix>/bin/clang-tidy                  (and the other tools)
  <prefix>/lib/clang/21/include/stddef.h   (from clang-resource-headers-21.tar.gz)

clang looks for its builtin headers (stddef.h, stdarg.h, limits.h, ...) in
<directory of the executable>/../lib/clang/<version>/include. Without them,
clang-tidy stops at the first #include of a standard header.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import build

DEFAULT_REPO = "cpp-linter/clang-tools-static-binaries"

# Uses only clang's builtin headers, so it needs no system SDK or standard
# library: with -nostdlibinc, clang searches its resource directory and
# nothing else. It fails with "'limits.h' file not found" when the headers
# are not installed next to the binary.
FREESTANDING_SOURCE = """\
#include <limits.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

int sum(int count, ...) {
  va_list args;
  va_start(args, count);
  int total = 0;
  for (int i = 0; i < count; ++i)
    total += va_arg(args, int);
  va_end(args);
  return total;
}

const size_t int_size = sizeof(int32_t);
const bool positive = INT_MAX > 0;
int *pointer = 0;
"""
FREESTANDING_FLAGS = ["-std=c++11", "-ffreestanding", "-nostdlibinc"]

# A file as users write it, with the system's C++ standard library. Only on
# Linux, where the runner's libstdc++ is found without extra setup; macOS
# and Windows need an SDK path or a Visual Studio environment.
HOSTED_SOURCE = """\
#include <string>
#include <vector>

std::vector<std::string> names() { return {"clang-tidy", "clang-format"}; }
int *pointer = 0;
"""
HOSTED_FLAGS = ["-std=c++17"]

UNFORMATTED = "int  main( ){return 0;}\n"
FORMATTED = "int main() { return 0; }\n"


class Check:
    def __init__(self, name: str) -> None:
        self.name = name
        self.ok = True
        self.details: list[str] = []

    def fail(self, message: str) -> None:
        self.ok = False
        self.details.append(message)


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    print(f">>> {' '.join(str(c) for c in cmd)}", flush=True)
    return subprocess.run(
        [str(c) for c in cmd],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def output_of(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stdout or "") + (result.stderr or "")


def find_file(
    name: str, files: Path | None, tag: str | None, repo: str, work: Path
) -> Path | None:
    """Path of release file *name*, from --files or downloaded from --tag."""
    if files is not None:
        found = sorted(p for p in files.rglob(name) if p.is_file())
        return found[0] if found else None
    url = f"https://github.com/{repo}/releases/download/{tag}/{name}"
    dest = work / "download" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url}", flush=True)
    try:
        urllib.request.urlretrieve(url, dest)
    except OSError as exc:
        print(f"  not available: {exc}")
        return None
    return dest


def unpack_headers(archive: Path, prefix: Path) -> None:
    """Unpack clang's headers into *prefix*, refusing paths outside lib/clang/."""
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            parts = Path(member.name).parts
            if (
                parts[:2] != ("lib", "clang")
                or ".." in parts
                or not (member.isfile() or member.isdir())
            ):
                raise SystemExit(f"Unexpected entry in {archive.name}: {member.name}")
        if hasattr(tarfile, "data_filter"):
            tar.extractall(prefix, filter="data")
        else:
            tar.extractall(prefix)


def check_tidy(
    tools: dict[str, Path], work: Path, name: str, source: str, flags: list[str]
) -> Check:
    """clang-tidy finds every header, reports the 0 and exports a fix."""
    check = Check(name)
    folder = work / name
    folder.mkdir()
    (folder / "test.cpp").write_text(source, encoding="utf-8")
    (folder / "fixes").mkdir()
    result = run(
        [
            tools["clang-tidy"],
            "test.cpp",
            "--checks=-*,modernize-use-nullptr",
            "--export-fixes=fixes/fixes.yaml",
            "--",
            *flags,
        ],
        folder,
    )
    out = output_of(result)
    if result.returncode != 0 or "error:" in out:
        check.fail(f"clang-tidy exited with {result.returncode}:\n{out}")
    elif "modernize-use-nullptr" not in out:
        check.fail(f"clang-tidy did not report modernize-use-nullptr:\n{out}")
    if check.ok and "clang-apply-replacements" in tools:
        result = run([tools["clang-apply-replacements"], "fixes"], folder)
        text = (folder / "test.cpp").read_text(encoding="utf-8")
        if result.returncode != 0 or "int *pointer = nullptr;" not in text:
            check.fail(
                f"clang-apply-replacements exited with {result.returncode} and "
                f"left:\n{text}\n{output_of(result)}"
            )
    return check


def check_format(tools: dict[str, Path], work: Path) -> Check:
    check = Check("clang-format")
    (work / "format.cpp").write_text(UNFORMATTED, encoding="utf-8")
    result = run([tools["clang-format"], "--style=LLVM", "format.cpp"], work)
    if result.returncode != 0 or result.stdout.replace("\r\n", "\n") != FORMATTED:
        check.fail(
            f"clang-format exited with {result.returncode}, printed "
            f"{result.stdout!r}, expected {FORMATTED!r}\n{result.stderr}"
        )
    return check


def check_query(tools: dict[str, Path], work: Path) -> Check:
    check = Check("clang-query")
    folder = work / "query"
    folder.mkdir()
    (folder / "test.cpp").write_text(FREESTANDING_SOURCE, encoding="utf-8")
    result = run(
        [
            tools["clang-query"],
            "-c",
            'match varDecl(hasName("pointer"))',
            "test.cpp",
            "--",
            *FREESTANDING_FLAGS,
        ],
        folder,
    )
    out = output_of(result)
    # clang-query still matches when a header is missing, so look for errors.
    if result.returncode != 0 or "error:" in out or "1 match." not in out:
        check.fail(f"clang-query exited with {result.returncode}:\n{out}")
    return check


def check_include_cleaner(tools: dict[str, Path], work: Path) -> Check:
    check = Check("clang-include-cleaner")
    folder = work / "include-cleaner"
    folder.mkdir()
    (folder / "test.cpp").write_text(FREESTANDING_SOURCE, encoding="utf-8")
    result = run(
        [
            tools["clang-include-cleaner"],
            "--print=changes",
            "test.cpp",
            "--",
            *FREESTANDING_FLAGS,
        ],
        folder,
    )
    out = output_of(result)
    if result.returncode != 0 or "error:" in out:
        check.fail(f"clang-include-cleaner exited with {result.returncode}:\n{out}")
    return check


def check_runs(tool: str, exe: Path, version: str, work: Path) -> Check:
    """The tools without a code test at least start."""
    check = Check(tool)
    if tool == "llvm-profdata" and int(version) < 17:
        # Before LLVM 17, llvm-profdata has no --version, only subcommands.
        result = run([exe], work)
        if "USAGE" not in output_of(result):
            check.fail(f"{exe.name} printed no usage:\n{output_of(result)}")
        return check
    result = run([exe, "--version"], work)
    if result.returncode != 0:
        check.fail(
            f"{exe.name} --version exited with {result.returncode}:\n{output_of(result)}"
        )
    return check


def accept(
    version: str,
    target_platform: str,
    files: Path | None,
    tag: str | None,
    repo: str,
    work: Path,
) -> list[Check]:
    exe = ".exe" if target_platform.startswith("windows") else ""
    prefix = work / "prefix"
    (prefix / "bin").mkdir(parents=True)
    checks: list[Check] = []

    install = Check("install")
    checks.append(install)
    tools: dict[str, Path] = {}
    for tool in build.active_tools(version):
        name = f"{tool}-{version}_{target_platform}{exe}"
        path = find_file(name, files, tag, repo, work)
        if path is None:
            install.fail(f"{name} is missing")
            continue
        dest = prefix / "bin" / f"{tool}{exe}"
        shutil.copyfile(path, dest)
        dest.chmod(0o755)
        tools[tool] = dest

    headers = find_file(build.resource_headers_name(version), files, tag, repo, work)
    if headers is None:
        install.fail(
            f"{build.resource_headers_name(version)} is missing, so clang-tidy "
            "and clang-query cannot find clang's builtin headers"
        )
    else:
        unpack_headers(headers, prefix)

    if "clang-tidy" in tools:
        checks.append(
            check_tidy(
                tools, work, "clang-tidy", FREESTANDING_SOURCE, FREESTANDING_FLAGS
            )
        )
        if target_platform.startswith("linux"):
            checks.append(
                check_tidy(
                    tools, work, "clang-tidy-hosted", HOSTED_SOURCE, HOSTED_FLAGS
                )
            )
    if "clang-format" in tools:
        checks.append(check_format(tools, work))
    if "clang-query" in tools:
        checks.append(check_query(tools, work))
    if "clang-include-cleaner" in tools:
        checks.append(check_include_cleaner(tools, work))
    tested = {"clang-tidy", "clang-format", "clang-query", "clang-include-cleaner"}
    tested.add("clang-apply-replacements")
    for tool, path in tools.items():
        if tool not in tested:
            checks.append(check_runs(tool, path, version, work))
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version", required=True, help="LLVM major, e.g. 21.")
    parser.add_argument(
        "--platform",
        choices=list(build.CMAKE_ARGS_BY_OS),
        default=None,
        help="Platform of the binaries. Defaults to this machine.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--files", type=Path, help="Directory with the downloaded files."
    )
    source.add_argument("--tag", help="Download the files of this release.")
    parser.add_argument("--repo", default=os.environ.get("GH_REPO", DEFAULT_REPO))
    parser.add_argument("--report", type=Path, help="Write the results here as JSON.")
    args = parser.parse_args()
    target_platform = args.platform or f"{build.detect_os()}-{build.detect_arch()}"

    with tempfile.TemporaryDirectory(prefix="acceptance-") as tmp:
        work = Path(tmp)
        checks = accept(
            args.version, target_platform, args.files, args.tag, args.repo, work
        )

    print()
    for check in checks:
        print(f"{'PASS' if check.ok else 'FAIL'}  {check.name}")
        for detail in check.details:
            print("      " + detail.replace("\n", "\n      "))
    passed = all(check.ok for check in checks)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "version": args.version,
                    "platform": target_platform,
                    "passed": passed,
                    "checks": {c.name: c.ok for c in checks},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    if not passed:
        sys.exit(f"LLVM {args.version} on {target_platform}: acceptance failed.")
    print(f"LLVM {args.version} on {target_platform}: all checks passed.")


if __name__ == "__main__":
    main()
