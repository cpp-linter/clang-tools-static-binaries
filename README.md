# clang-tools-static-binaries

[![LLVM](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fcpp-linter.github.io%2Fclang-tools-static-binaries%2Findex.json&query=%24.latest_version&label=LLVM&labelColor=454a63&color=007ec6)](https://cpp-linter.github.io/clang-tools-static-binaries/)
[![ci](https://img.shields.io/github/actions/workflow/status/cpp-linter/clang-tools-static-binaries/build.yml?branch=master&label=ci&labelColor=454a63)](https://github.com/cpp-linter/clang-tools-static-binaries/actions/workflows/build.yml)
[![part of cpp-linter](https://img.shields.io/badge/part%20of-cpp--linter-ffc20a?labelColor=454a63)](https://cpp-linter.github.io/)

Static binaries of clang-format, clang-tidy and other LLVM tools, so you can use them without building LLVM.

[Website](https://cpp-linter.github.io/) · [Get started](https://cpp-linter.github.io/getting-started/#just-the-clang-tools) · [Discussions](https://github.com/orgs/cpp-linter/discussions)

## Quick start

Every LLVM version has its own release, named after the LLVM release it was built from. The [download page](https://cpp-linter.github.io/clang-tools-static-binaries/) lists the current release of every version and links to each file. It also shows which versions LLVM, PyPI, conda-forge and Homebrew have, with an install command for each source.

To download clang-format 23 for Linux x86-64, check it and run it:

```bash
curl -fLO https://github.com/cpp-linter/clang-tools-static-binaries/releases/download/llvm-23.1.3-2/clang-format-23_linux-amd64
curl -fLO https://github.com/cpp-linter/clang-tools-static-binaries/releases/download/llvm-23.1.3-2/SHA512SUMS
sha512sum -c SHA512SUMS --ignore-missing
chmod +x clang-format-23_linux-amd64
./clang-format-23_linux-amd64 --version
```

In scripts, read the URL of the current build from [`index.json`](#indexjson) instead of writing the release name by hand:

```bash
curl -fsSL https://cpp-linter.github.io/clang-tools-static-binaries/index.json \
  | jq -r '.versions["23"].tools["clang-format"]["linux-amd64"].url'
```

[clang-tools](https://cpp-linter.github.io/clang-tools-pip/) (pip), the [asdf plugin](https://github.com/cpp-linter/asdf-clang-tools) and the [Homebrew tap](https://github.com/cpp-linter/homebrew-tap) (macOS, `brew install cpp-linter/tap/clang-format` for the newest version, `clang-format@22` for an older one) install these same binaries for you.

## Supported versions

LLVM 12 to 23 are built for Linux, macOS and Windows on x86-64 and ARM64. Every version has clang-format, clang-tidy, clang-query, clang-apply-replacements, clang-scan-deps, llvm-cov, llvm-profdata and llvm-symbolizer; clang-include-cleaner is included from LLVM 18.

Files are named `<tool>-<LLVM major>_<platform>`, with `.exe` on Windows, where the platform is `linux-amd64`, `linux-arm64`, `macos-amd64`, `macos-arm64`, `windows-amd64` or `windows-arm64`.

The Windows binaries need nothing but Windows itself. The first builds of LLVM 18 to 23 (`llvm-18.1.5` to `llvm-23.1.3`, without a `-2` suffix) were the exception: they needed the Visual C++ Redistributable. Their `-2` rebuilds fixed that, and `index.json` points to the rebuilds.

### One release per LLVM version

Each LLVM version is built once and published in its own release, for example [`llvm-21.1.0`](https://github.com/cpp-linter/clang-tools-static-binaries/releases/tag/llvm-21.1.0). That release never changes. If the same LLVM release has to be built again, the new build gets a revision suffix, such as `llvm-23.1.3-2`, and `index.json` moves to it. A new patch release of LLVM gets a new release, such as `llvm-23.1.4`.

### `index.json`

Tools that download these binaries should read [`index.json`](https://cpp-linter.github.io/clang-tools-static-binaries/index.json) instead of building URLs by hand. It lists every supported LLVM version with its current release, and for every tool and platform the download URL, size and SHA-512:

```json
{
  "schema_version": 1,
  "latest": "23",
  "latest_version": "23.1.3",
  "versions": {
    "23": {
      "version": "23.1.3",
      "tag": "llvm-23.1.3-2",
      "tools": {
        "clang-format": {
          "linux-amd64": {
            "name": "clang-format-23_linux-amd64",
            "url": "https://github.com/cpp-linter/clang-tools-static-binaries/releases/download/llvm-23.1.3-2/clang-format-23_linux-amd64",
            "size": 3145728,
            "sha512": "..."
          }
        }
      }
    }
  }
}
```

It also has `platforms`, the minimum LLVM version of each tool under `tools`, and `release_url` and `sha512sums_url` for each version. The index is updated whenever a version is added or rebuilt; the files it points to never change.

### Retired versions

The supported versions are a **rolling window of the latest LLVM major versions**. Older versions are retired from time to time to keep build times manageable and maintenance sustainable. The window has no fixed size, and adding a version does not always retire the oldest one. Retiring a version is a **build-time and storage decision**, not a statement about the quality of that LLVM release.

Binaries for retired versions remain available in their releases:

| LLVM | Retired  | Last release with it                                                                                             |
| ---- | -------- | ---------------------------------------------------------------------------------------------------------------- |
| 7    | Feb 2025 | [master-67c95218](https://github.com/cpp-linter/clang-tools-static-binaries/releases/tag/master-67c95218)         |
| 8    | Aug 2025 | [master-b35c5633](https://github.com/cpp-linter/clang-tools-static-binaries/releases/tag/master-b35c5633)         |
| 9    | Mar 2026 | [master-6e612956](https://github.com/cpp-linter/clang-tools-static-binaries/releases/tag/master-6e612956)         |
| 10   | Mar 2026 | [master-6e612956](https://github.com/cpp-linter/clang-tools-static-binaries/releases/tag/master-6e612956)         |
| 11   | Jun 2026 | [2026.06.15-a56c0263](https://github.com/cpp-linter/clang-tools-static-binaries/releases/tag/2026.06.15-a56c0263) |

### Releases before per-version releases

Until 2026.09.01, every release held all versions, and was named after its date or commit (for example `2026.09.01-5fb8802d`). These releases stay available. [`2026.09.01-5fb8802d`](https://github.com/cpp-linter/clang-tools-static-binaries/releases/tag/2026.09.01-5fb8802d) is still marked as the latest release on GitHub, because older versions of cpp-linter and its installers download from `releases/latest`; those links keep working with the binaries they had. New builds are only in the `llvm-*` releases and in `index.json`.

Releases before 2026.06.29 have a `.sha512sum` file next to each binary instead of `SHA512SUMS`. LLVM 7 to 10 were built only for `linux-amd64`, `macosx-amd64` and `windows-amd64`.

## How can I trust this repository?

- Releases since 2026.06.05 are **immutable**: once published, their files are never modified.
- Verify checksums using the `SHA512SUMS` file in the release, as in the [Quick start](https://github.com/cpp-linter/clang-tools-static-binaries#quick-start). It holds the SHA-512 hash of every binary in that release (`index.json` has the same hashes), in the format `sha512sum -c` reads.
- Check where a binary was built: newly built releases have a [build provenance attestation](https://github.com/cpp-linter/clang-tools-static-binaries/blob/master/SECURITY.md#checking-where-a-binary-was-built) for every binary, which `gh attestation verify <file> --repo cpp-linter/clang-tools-static-binaries` checks.
- Fork this repository and run GitHub Actions on your behalf
- Build and test manually using `python3 build.py` (see [Building locally](https://github.com/cpp-linter/clang-tools-static-binaries#building-locally)) or the steps in [.github/workflows](https://github.com/cpp-linter/clang-tools-static-binaries/tree/master/.github/workflows)

## Motivation behind this repo

Different projects often use different versions of clang-format and clang-tidy. Installing multiple versions via system package managers can quickly get out of hand, and compiling each one from source is time-consuming.

This repository solves that by providing pre-built static binaries for many LLVM versions across multiple platforms.

These binaries aim to:

- be as small as possible
- not require any additional dependencies apart from the OS itself

This repository ([cpp-linter/clang-tools-static-binaries](https://github.com/cpp-linter/clang-tools-static-binaries)) is forked from [muttleyxd/clang-tools-static-binaries](https://github.com/muttleyxd/clang-tools-static-binaries).

## Building locally

A Python build script is provided so you can reproduce any build on your own machine without needing GitHub Actions.

**Prerequisites** (install once per platform):

| Platform       | Requirements                          |
| -------------- | ------------------------------------- |
| Linux x86-64   | `gcc-10`, `g++-10`, `cmake`, `make`   |
| Linux ARM64    | `gcc-10`, `g++-10`, `cmake`, `make`   |
| macOS x86-64   | Homebrew, `gcc@14`, `cmake`           |
| macOS ARM64    | Homebrew, `gcc@14`, `cmake`           |
| Windows x86-64 | Visual Studio with C++ tools, `cmake` |
| Windows ARM64  | Visual Studio with C++ tools, `cmake` |

Every platform also needs Python 3.10 or later.

**Run the script:**

```bash
# build clang-tools version 18 for the auto-detected host OS
python3 build.py --version 18

# explicitly target a platform
python3 build.py --version 17 --platform macos-arm64

# write downloads and build artifacts to a custom directory
python3 build.py --version 20 --platform linux-amd64 --build-dir /tmp/llvm-build
```

Run `python3 build.py --help` for the full list of options. It builds only the versions listed in [`releases.json`](https://github.com/cpp-linter/clang-tools-static-binaries/blob/master/releases.json).

The script performs exactly the same steps as the CI workflow:
downloads the LLVM source, applies any necessary patches, configures and
builds with CMake, smoke-tests each binary, and writes the renamed
binaries and a `SHA512SUMS` checksum file into `<release>/build/bin/`
(`<release>/build/MinSizeRel/bin/` on Windows).

## Contributing

See [CONTRIBUTING.md](https://github.com/cpp-linter/clang-tools-static-binaries/blob/master/CONTRIBUTING.md) and the [issues](https://github.com/cpp-linter/clang-tools-static-binaries/issues).

## License

This repository is released under the [Unlicense](https://github.com/cpp-linter/clang-tools-static-binaries/blob/master/LICENSE). The binaries are built from LLVM, which is under the [Apache License v2.0 with LLVM Exceptions](https://llvm.org/LICENSE.txt).
