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
curl -fLO https://github.com/cpp-linter/clang-tools-static-binaries/releases/download/llvm-23.1.5/clang-format-23_linux-amd64
curl -fLO https://github.com/cpp-linter/clang-tools-static-binaries/releases/download/llvm-23.1.5/SHA512SUMS
sha512sum -c SHA512SUMS --ignore-missing
chmod +x clang-format-23_linux-amd64
./clang-format-23_linux-amd64 --version
```

In scripts, read the URL of the current build from [`index.json`](#indexjson) instead of writing the release name by hand:

```bash
curl -fsSL https://cpp-linter.github.io/clang-tools-static-binaries/index.json \
  | jq -r '.versions["23"].tools["clang-format"]["linux-amd64"].url'
```

### Which file to download

| You want                                                          | Download                                                                                                    |
| ----------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| clang-format, or another tool that reads no headers               | the single binary, `<tool>-<LLVM major>_<platform>`                                                         |
| clang-tidy, clang-query or clang-include-cleaner                  | the binary and `clang-resource-headers-<LLVM major>.tar.gz` ([why](#clang-tidy-needs-clangs-headers))       |
| every tool, the helper scripts and the headers, ready to unpack    | `clang-tools-<LLVM major>_<platform>.tar.gz` ([layout](#everything-for-one-platform-in-one-archive))        |
| the files of a version from a script or an installer               | the URLs and SHA-512s in [`index.json`](#indexjson)                                                         |

The current release of every version also has `SHA512SUMS`, `BUILDINFO.json` (how it was built) and LLVM's `LICENSE.TXT`.

### clang-tidy needs clang's headers

clang-tidy, clang-query and clang-include-cleaner parse code the way the compiler does, so they need clang's builtin headers (`stddef.h`, `stdarg.h`, `limits.h` and others). They look for them in `../lib/clang/<version>/include`, relative to the folder that holds the executable. Without them they stop at the first standard include with `'stddef.h' file not found`.

The current release of every version has these headers in `clang-resource-headers-<LLVM major>.tar.gz`. Unpack it in the parent of the folder that holds the binaries:

```bash
index=https://cpp-linter.github.io/clang-tools-static-binaries/index.json
mkdir -p clang-tools/bin
curl -fsSL "$index" | jq -r '.versions["23"].tools["clang-tidy"]["linux-amd64"].url' \
  | xargs curl -fL -o clang-tools/bin/clang-tidy
curl -fsSL "$index" | jq -r '.versions["23"].resource_headers.url' \
  | xargs curl -fL | tar -xz -C clang-tools
chmod +x clang-tools/bin/clang-tidy
# clang-tools/lib/clang/23/include/stddef.h is where clang-tidy looks for it
```

The archive is the same for every platform.

[clang-tools](https://cpp-linter.github.io/clang-tools-pip/) (pip), the [npm packages](https://github.com/cpp-linter/clang-tools-npm) (`npm install --save-dev @cpp-linter/clang-tidy@23`, with the headers included), the [asdf plugin](https://github.com/cpp-linter/asdf-clang-tools) and the [Homebrew tap](https://github.com/cpp-linter/homebrew-tap) (macOS, `brew install cpp-linter/tap/clang-format` for the newest version, `clang-format@22` for an older one) install these same binaries for you.

### Everything for one platform in one archive

The current release of every version also has `clang-tools-<LLVM major>_<platform>.tar.gz`: every tool, the helper scripts and clang's headers for one platform, in the layout of an LLVM installation. It unpacks to one folder:

```text
clang-tools-23_linux-amd64/
  bin/clang-format, clang-tidy, ...     the tools under their plain names (.exe on Windows)
  bin/run-clang-tidy, git-clang-format, clang-format-diff.py, clang-tidy-diff.py
  lib/clang/23/include/                 clang's builtin headers
  LICENSE.TXT
```

```bash
index=https://cpp-linter.github.io/clang-tools-static-binaries/index.json
curl -fsSL "$index" | jq -r '.versions["23"].bundles["linux-amd64"].url' \
  | xargs curl -fL | tar -xz
clang-tools-23_linux-amd64/bin/clang-tidy --version
```

Tool installers that expect one archive per platform, such as aqua and mise, can use these.

### Helper scripts

It also has `clang-tools-scripts-<LLVM major>.tar.gz` with `run-clang-tidy`, `clang-tidy-diff.py`, `clang-format-diff.py` and `git-clang-format` from the same LLVM release (`scripts` in `index.json`). Like the headers archive, it is the same for every platform and unpacks in the parent of the folder that holds the binaries, to `bin/`. The scripts need Python 3, and `git-clang-format` needs git. On Windows, start them with Python, such as `python bin\run-clang-tidy`. They look for the tools under their plain names (`clang-tidy`, `clang-format`), as in the [archive with everything](#everything-for-one-platform-in-one-archive); with the single binaries, name the tool, for example `run-clang-tidy -clang-tidy-binary ./clang-tidy-23_linux-amd64` or `git clang-format --binary ./clang-format-23_linux-amd64`.

### clang-tidy on macOS needs the SDK path

On macOS the C and C++ standard headers (`ctype.h`, `string` and others) are in the Xcode or Command Line Tools SDK, and `/usr/include` does not exist. Apple's clang finds the SDK by itself. These binaries are built from upstream LLVM, which does not, so clang-tidy, clang-query and clang-include-cleaner stop with `'ctype.h' file not found` unless the compile command has `-isysroot`. Set `SDKROOT` before running them:

```bash
export SDKROOT="$(xcrun --show-sdk-path)"
clang-tidy foo.c
```

Or pass the path to a single run with `clang-tidy --extra-arg=-isysroot"$(xcrun --show-sdk-path)" foo.c`. If the commands in `compile_commands.json` already have `-isysroot`, you need neither.

The C++ headers in a recent SDK can use builtins that an older clang does not know, so check C++ code with a recent LLVM version. For example, with the SDK that comes with Apple clang 21, LLVM 16 stops on `__builtin_clzg` in `<string>`, while LLVM 23 works.

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
  "latest_version": "23.1.5",
  "versions": {
    "23": {
      "version": "23.1.5",
      "tag": "llvm-23.1.5",
      "tools": {
        "clang-format": {
          "linux-amd64": {
            "name": "clang-format-23_linux-amd64",
            "url": "https://github.com/cpp-linter/clang-tools-static-binaries/releases/download/llvm-23.1.5/clang-format-23_linux-amd64",
            "size": 3145728,
            "sha512": "..."
          }
        }
      }
    }
  }
}
```

It also has `platforms`, the minimum LLVM version of each tool under `tools`, and for each version `release_url`, `sha512sums_url`, `resource_headers` (clang's headers, see [above](#clang-tidy-needs-clangs-headers)), `scripts` (the [helper scripts](#helper-scripts)), `bundles` (one [archive with everything](#everything-for-one-platform-in-one-archive) per platform), `license` (LLVM's `LICENSE.TXT`) and `buildinfo` (`BUILDINFO.json`), each file with a name, URL, size and SHA-512. The index is updated whenever a version is added or rebuilt; the files it points to never change.

### Earlier patch releases

`index.json` lists the files of one release per LLVM major: the newest. If your project pins a full version, such as 21.1.0, look in `older_releases` of that major. It lists every earlier patch version that still has a release, newest first, each with its newest build:

```json
"older_releases": [
  {
    "version": "21.1.0",
    "tag": "llvm-21.1.0",
    "release_url": "https://github.com/cpp-linter/clang-tools-static-binaries/releases/tag/llvm-21.1.0",
    "sha512sums_url": "https://github.com/cpp-linter/clang-tools-static-binaries/releases/download/llvm-21.1.0/SHA512SUMS"
  }
]
```

The files of an earlier release have the same names as those of the current one, so replace the tag in a URL from `tools`, and check the download against that release's `SHA512SUMS`. An earlier release may lack files that were added later, such as the headers archive, the helper scripts or the archive with everything.

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

## How a release is made

1. [`releases.json`](releases.json) names the LLVM release built for each major, and [`sources.sha256`](sources.sha256) pins the SHA-256 of its source, checked against LLVM's GPG signature when the version is added (see [CONTRIBUTING](CONTRIBUTING.md)).
2. On every push to `master`, the [Build workflow](.github/workflows/build.yml) builds each version that has no release yet on all six platforms. The build jobs get no access token.
3. [`acceptance.py`](acceptance.py) installs the binaries of every platform with clang's headers, the way users get them, and runs every tool on test code. A version is published only when every platform passes.
4. The workflow writes `BUILDINFO.json`, the archives and `SHA512SUMS`, signs a [build provenance attestation](SECURITY.md#checking-where-a-binary-was-built) for every file, and publishes the release `llvm-<version>`. Published releases never change.
5. It writes [`index.json`](#indexjson), attests it and publishes it on GitHub Pages, together with the [download page](https://cpp-linter.github.io/clang-tools-static-binaries/).

A maintainer can rebuild a version by hand, giving the reason. The rebuild becomes a new release with a revision suffix (`llvm-22.1.8-2`), whose notes say which release it replaces and why, and `index.json` moves to it.

### Known limitations

- File names carry only the LLVM major (`clang-format-23_linux-amd64`). The full version is in the release name, `index.json` and `BUILDINFO.json`.
- A release's tag points to the head of `master` when it was published, not to the commit it was built from. The release notes, `BUILDINFO.json` and the attestations name the commit that built it.
- Earlier builds of a version stay published after a rebuild or a new patch release, so several releases can hold the same LLVM major. `index.json` and the download page name the current one.
- The archives with everything for one platform are made after the acceptance tests, from the files that passed them.
- [`build.py`](#building-locally) builds a version from the same source with the same options and patches, but the result is not checked to be byte for byte the same as the release.

## How can I trust this repository?

- Releases since 2026.06.05 are **immutable**: once published, their files are never modified.
- Verify checksums using the `SHA512SUMS` file in the release, as in the [Quick start](https://github.com/cpp-linter/clang-tools-static-binaries#quick-start). It holds the SHA-512 hash of every binary in that release (`index.json` has the same hashes), in the format `sha512sum -c` reads.
- Check where a binary was built: every file of the current releases has a [build provenance attestation](https://github.com/cpp-linter/clang-tools-static-binaries/blob/master/SECURITY.md#checking-where-a-binary-was-built), except `SHA512SUMS` in releases built before it was attested too (their notes say which files have one). `gh attestation verify <file> --repo cpp-linter/clang-tools-static-binaries` checks it.
- See how a release was built: every current release has `BUILDINFO.json`, which records for every platform the LLVM source and its SHA-256, the patches, the CMake options, the compiler and the runner image. It also has LLVM's `LICENSE.TXT`.
- Every current release was tested before it was published: [`acceptance.py`](https://github.com/cpp-linter/clang-tools-static-binaries/blob/master/acceptance.py) installs the binaries of every platform with clang's headers, the way users get them, and runs clang-tidy, clang-apply-replacements, clang-format, clang-query and clang-include-cleaner on test code, and starts every helper script. A version is published only when every platform passes. You can run it on any release, for example `python3 acceptance.py --version 21 --tag llvm-21.1.0`.
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

A Python build script is provided so you can build any version on your own machine, with the same source, options and patches as CI, without needing GitHub Actions.

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
binaries, a `SHA512SUMS` checksum file and the build details
(`buildinfo-<version>_<platform>.json`) into `<release>/build/bin/`
(`<release>/build/MinSizeRel/bin/` on Windows). clang's headers and LLVM's
license go into `<release>/common/`.

To run the acceptance tests that CI runs before publishing, on the files you
built:

```bash
python3 acceptance.py --version 21 --files llvm-project-21.1.0.src
```

## Contributing

See [CONTRIBUTING.md](https://github.com/cpp-linter/clang-tools-static-binaries/blob/master/CONTRIBUTING.md) and the [issues](https://github.com/cpp-linter/clang-tools-static-binaries/issues).

## License

This repository is released under the [Unlicense](https://github.com/cpp-linter/clang-tools-static-binaries/blob/master/LICENSE). The binaries are built from LLVM, which is under the [Apache License v2.0 with LLVM Exceptions](https://llvm.org/LICENSE.txt).
