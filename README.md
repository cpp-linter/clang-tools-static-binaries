# clang-tools static binaries

[![Build](https://github.com/cpp-linter/clang-tools-static-binaries/actions/workflows/build.yml/badge.svg)](https://github.com/cpp-linter/clang-tools-static-binaries/actions/workflows/build.yml)
![Supported platforms](https://img.shields.io/badge/platform-linux--64%20%7C%20linux--arm64%20%7C%20macos--64%20%7C%20macos--arm64%20%7C%20windows--64%20%7C%20windows--arm64-blue)

[![pip](https://img.shields.io/badge/pip-supported-006dad?logo=pypi&logoColor=white)](https://github.com/cpp-linter/clang-tools-pip)
[![asdf](https://img.shields.io/badge/asdf--clang--tools-supported-9cf)](https://github.com/cpp-linter/asdf-clang-tools)
[![homebrew](https://img.shields.io/badge/homebrew-tap-FBB040?logo=homebrew&logoColor=white)](https://github.com/cpp-linter/homebrew-tap)
[![cpp-linter hub](https://img.shields.io/badge/%F0%9F%8F%A0_cpp--linter_hub-%E2%86%90_home-22863a)](https://cpp-linter.github.io/)

Includes **[clang-format](https://clang.llvm.org/docs/ClangFormat.html), [clang-tidy](https://clang.llvm.org/extra/clang-tidy/), [clang-query](https://github.com/llvm/llvm-project/tree/main/clang-tools-extra/clang-query), [clang-apply-replacements](https://github.com/llvm/llvm-project/tree/main/clang-tools-extra/clang-apply-replacements) and [clang-include-cleaner](https://clang.llvm.org/extra/clang-tidy/checks/misc/include-cleaner.html)** (LLVM 18+).

## Table of Contents

- [Installation](#installation)
- [Clang Tools Version Support Matrix](#clang-tools-version-support-matrix)
- [Download](#download)
- [How can I trust this repository?](#how-can-i-trust-this-repository)
- [Motivation](#motivation)
- [Building locally](#building-locally)

## Installation

Install clang-tools via your preferred package manager, take clang-format as an example:

```bash
# pip (all platforms)
pip install clang-tools
clang-tools install clang-format

# asdf (all platforms)
asdf plugin add clang-format https://github.com/cpp-linter/asdf-clang-tools.git
asdf install clang-format latest

# Homebrew (macOS only)
brew tap cpp-linter/tap
brew install clang-format
```

Or download pre-built binaries directly from the [Releases](https://github.com/cpp-linter/clang-tools-static-binaries/releases) page.

## Clang Tools Version Support Matrix

| Clang Tools              | OS/Version     | 22  | 21  | 20  | 19  | 18  | 17  | 16  | 15  | 14  | 13  | 12  | 11  |
| :----------------------- | -------------- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| clang-format             | Linux x86-64   | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | Linux ARM64    | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | macOS x86_64   | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | macOS ARM64    | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | Windows x86-64 | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | Windows ARM64  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
| clang-tidy               | Linux x86-64   | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | Linux ARM64    | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | macOS x86_64   | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | macOS ARM64    | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | Windows x86-64 | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | Windows ARM64  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
| clang-query              | Linux x86-64   | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | Linux ARM64    | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | macOS x86_64   | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | macOS ARM64    | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | Windows x86-64 | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | Windows ARM64  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
| clang-apply-replacements | Linux x86-64   | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | Linux ARM64    | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | macOS x86_64   | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | macOS ARM64    | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | Windows x86-64 | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
|                          | Windows ARM64  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  |
| clang-include-cleaner    | Linux x86-64   | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ❌  | ❌  | ❌  | ❌  | ❌  | ❌  | ❌  |
|                          | Linux ARM64    | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ❌  | ❌  | ❌  | ❌  | ❌  | ❌  | ❌  |
|                          | macOS x86_64   | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ❌  | ❌  | ❌  | ❌  | ❌  | ❌  | ❌  |
|                          | macOS ARM64    | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ❌  | ❌  | ❌  | ❌  | ❌  | ❌  | ❌  |
|                          | Windows x86-64 | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ❌  | ❌  | ❌  | ❌  | ❌  | ❌  | ❌  |
|                          | Windows ARM64  | ✔️  | ✔️  | ✔️  | ✔️  | ✔️  | ❌  | ❌  | ❌  | ❌  | ❌  | ❌  | ❌  |

> [!NOTE]
>
> ### Version Support Policy
>
> Each release includes a **rolling window of the latest LLVM major versions**.
> Older versions are retired on a regular cadence to keep build times manageable
> and maintenance sustainable.
>
> **Current policy:** The `N` latest major LLVM versions are supported, where `N`
> is determined by the project maintainers (typically 7–8 major versions). When a
> new LLVM version is added, the oldest one is retired in the same release.
>
> **Retired versions:**
>
> | Version | Released   | Retired   |
> |---------|------------|-----------|
> | v7      | May 2019   | Feb 2025  |
> | v8      | Jul 2019   | Sep 2025  |
> | v9      | Sep 2019   | Mar 2026  |
> | v10     | Mar 2020   | Mar 2026  |
> | v11     | Oct 2020   | TBD       |
>
> Binaries for retired versions remain available in historical releases on the
> [Releases page](https://github.com/cpp-linter/clang-tools-static-binaries/releases).
> Each release ships an immutable [`versions.json`](#download) that documents
> exactly which LLVM versions are included — downstream tools (pip, asdf, Homebrew)
> should use this file to discover available versions rather than hardcoding a list.
>
> If you need a retired version, you can still download it from an older release,
> or build it locally using `python build.py --version <N>`.
>
> Retiring a version is a **build-time and storage decision**, not a statement
> about the quality of that LLVM release. Old binaries remain on GitHub Releases
> indefinitely.

## Download

- Download clang-tools static binaries for your platform from the [Releases](https://github.com/cpp-linter/clang-tools-static-binaries/releases) tab.
- Alternatively, use [pip](https://github.com/cpp-linter/clang-tools-pip), [asdf](https://github.com/cpp-linter/asdf-clang-tools), or [Homebrew](https://github.com/cpp-linter/homebrew-tap) (macOS) to download and manage them.
- For programmatic access, the latest release includes a [`versions.json`](https://github.com/cpp-linter/clang-tools-static-binaries/releases/latest/download/versions.json) file that maps each clang tool version to its LLVM source release (e.g., `{"18": "llvm-project-18.1.5.src"}`). This is a stable machine-readable entry point for scripts and downstream tools.

## How can I trust this repository?

- Releases are **immutable** — once published, assets and metadata (`versions.json`) are never modified.
- Verify sha512sums of binaries against output from GitHub Actions to make sure binaries are not modified
- Fork this repository and run GitHub actions on your behalf
- Build and test manually using `python build.py` (see above) or the steps in [.github/workflows](https://github.com/cpp-linter/clang-tools-static-binaries/tree/master/.github/workflows)

## Motivation behind this repo

Different projects often use different versions of clang-format and clang-tidy. Installing multiple versions via system package managers can quickly get out of hand, and compiling each one from source is time-consuming.

This repository solves that by providing pre-built static binaries for many LLVM versions across multiple platforms.

These binaries aim to:

- be as small as possible
- not require any additional dependencies apart from the OS itself

This repository ([cpp-linter/clang-tools-static-binaries](https://github.com/cpp-linter/clang-tools-static-binaries)) is forked from [muttleyxd/clang-tools-static-binaries](https://github.com/muttleyxd/clang-tools-static-binaries).

## Building locally

A Python build script is provided so you can reproduce any build on your own machine without needing GitHub Actions. See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed contribution guidelines.

**Prerequisites** (install once per platform):

| Platform       | Requirements                          |
| -------------- | ------------------------------------- |
| Linux x86-64   | `gcc-10`, `cmake`, `make`             |
| Linux ARM64    | `gcc-10`, `cmake`, `make`             |
| macOS x86-64   | Homebrew, `gcc@14`, `cmake`           |
| macOS ARM64    | Homebrew, `gcc@14`, `cmake`           |
| Windows x86-64 | Visual Studio with C++ tools, `cmake` |
| Windows ARM64  | Visual Studio with C++ tools, `cmake` |

**Run the script:**

```bash
# build clang-tools version 18 for the auto-detected host OS
python build.py --version 18

# explicitly target a platform
python build.py --version 17 --platform macos-arm64

# write downloads and build artifacts to a custom directory
python build.py --version 20 --platform linux-amd64 --build-dir /tmp/llvm-build
```

Run `python build.py --help` for the full list of options.

The script performs exactly the same steps as the CI workflow:
downloads the LLVM source, applies any necessary patches, configures and
builds with CMake, smoke-tests each binary, and writes the renamed
binaries and their sha512sum files into `<release>/build/bin/`.
