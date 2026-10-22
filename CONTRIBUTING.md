# Contributing

Thanks for your interest in contributing! This project builds and distributes static binaries of clang/LLVM tools (clang-format, clang-tidy, clang-query, clang-apply-replacements, clang-include-cleaner, llvm-cov, llvm-profdata, llvm-symbolizer, clang-scan-deps) for multiple platforms.

## Quick Start

The build pipeline is in `.github/workflows/build.yml`. You can also reproduce any build locally with the provided Python script:

```bash
python build.py --version 18
python build.py --help  # see all options
```

The script mirrors exactly what CI does: download LLVM source → configure with CMake → build → smoke-test → rename and checksum.

Before CI publishes a version, `acceptance.py` installs the binaries of every platform with clang's headers and runs them on test code. Run it on a local build with `python acceptance.py --version 18 --files <the release folder>`.

## Ways to Contribute

- **Add a new clang version** — Add the version-to-tarball mapping to `releases.json` and the tarball's sha256 to `sources.sha256`. The CI matrix is generated automatically from `releases.json`.
- **Fix a build** — Look for failures in the [Build](https://github.com/cpp-linter/clang-tools-static-binaries/actions/workflows/build.yml) workflow.
- **Improve documentation** — Clarify README, add platform notes, etc.

## `releases.json`

[`releases.json`](releases.json) maps clang major versions to LLVM source tarballs:

```json
{ "23": "llvm-project-23.1.3.src", ... }
```

`build.py` and the CI plan in `publish.py` read from this file.

## `sources.sha256`

[`sources.sha256`](sources.sha256) lists the sha256 of every source tarball in
`releases.json`, in the format `sha256sum` writes. `build.py` refuses to build
from a tarball that is missing from this file or does not match it, and the CI
plan fails when a version in `releases.json` has no entry.

### Adding a new LLVM version

1. Add an entry to `releases.json` in descending order (newest first).
2. Download the tarball and its signature from the LLVM release, check the
   signature, and add the tarball's sha256 line at the top of `sources.sha256`:

   ```sh
   v=24.1.0
   base=https://github.com/llvm/llvm-project/releases/download/llvmorg-$v
   curl -LO $base/llvm-project-$v.src.tar.xz -LO $base/llvm-project-$v.src.tar.xz.sig
   curl -L https://releases.llvm.org/release-keys.asc | gpg --import
   gpg --verify llvm-project-$v.src.tar.xz.sig llvm-project-$v.src.tar.xz
   sha256sum llvm-project-$v.src.tar.xz  # on macOS: shasum -a 256
   ```

3. Open a PR. CI builds the new or changed entries on all platforms.
4. Always test at least one platform locally before opening the PR.

After the PR is merged, CI publishes each new version in its own release (for
example `llvm-24.1.0`) and updates `index.json` on GitHub Pages. Versions that
already have a release are not built again. A change to `build.py` or a patch
only rebuilds a version when you ask for it: run the **Build** workflow by hand
with the versions to rebuild, for example `22 23`. Each rebuilt version gets a
new revision (`llvm-22.1.8-2`). If a version fails to build, the other
versions are still published, and `index.json` keeps the previous release of
that LLVM major until the build is fixed. On pull requests that change
`build.py`, `acceptance.py`, a patch or the workflow, CI builds the newest and
the oldest version to check the change. Changing only action versions (`uses:` lines) in
the workflow does not trigger these builds.

### Version retirement policy

To keep build times and release sizes manageable, the project maintains a
**rolling window of the latest LLVM major versions**. The window has no fixed
size, and adding a version does not always retire the oldest one. To retire a
version, remove it from `releases.json` and add a row for it to the retired
versions table in the [README](README.md#supported-versions).

Retiring a version does **not** delete its binaries: its own release (for
example `llvm-12.0.1`) stays on GitHub Releases. It is only left out of
`index.json`.

### New tools and older LLVM versions

Some tools (e.g., `clang-include-cleaner`) only exist as standalone build targets
in newer LLVM releases. This is a fundamental limitation of the upstream source —
the project does not attempt to backport tools to older LLVM versions. The
README lists these gaps under Supported versions.

## Pull Request Flow

1. Fork the repo and create a branch.
2. Make your changes.
3. Test locally with `python build.py` if your change affects the build.
4. Open a PR against `master`.

Keep PRs small and focused.

## Need Help?

Open an issue or start a discussion — we’re happy to help.
