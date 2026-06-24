# Contributing

Thanks for your interest in contributing! This project builds and distributes static binaries of clang tools (clang-format, clang-tidy, clang-query, clang-apply-replacements, clang-include-cleaner) for multiple platforms.

## Quick Start

The build pipeline is in `.github/workflows/build.yml`. You can also reproduce any build locally with the provided Python script:

```bash
python build.py --version 18
python build.py --help  # see all options
```

The script mirrors exactly what CI does: download LLVM source → configure with CMake → build → smoke-test → rename and checksum.

## Ways to Contribute

- **Add a new clang version** — Add the version-to-tarball mapping to `releases.json`. The CI matrix is generated automatically from this file.
- **Fix a build** — Look for failures in the [Build](https://github.com/cpp-linter/clang-tools-static-binaries/actions/workflows/build.yml) workflow.
- **Improve documentation** — Clarify README, add platform notes, etc.

## `releases.json`

[`releases.json`](releases.json) maps clang major versions to LLVM source tarballs:

```json
{ "22": "llvm-project-22.1.0.src", ... }
```

The CI matrix, `build.py`, and `release.py` all read from this file.

### Adding a new LLVM version

1. Add an entry to `releases.json` in descending order (newest first).
2. Open a PR — CI will build all platforms automatically.
3. Always test at least one platform locally before opening the PR.

### Version retirement policy

To keep build times and release sizes manageable, the project maintains a
**rolling window of the latest LLVM major versions**. When a new version is
added, the oldest one should be retired in the same PR.

**Rule of thumb:** Keep the latest 6–8 major versions. When adding version `N`,
remove version `N-8` (or older) from `releases.json`. Check the
[README](README.md) for the current retirement history.

Retiring a version does **not** delete its binaries from previous releases.
Historical assets remain on GitHub Releases indefinitely.

### New tools and older LLVM versions

Some tools (e.g., `clang-include-cleaner`) only exist as standalone build targets
in newer LLVM releases. This is a fundamental limitation of the upstream source —
the project does not attempt to backport tools to older LLVM versions. The
version support matrix in the README uses ❌ to clearly mark these gaps.

## Pull Request Flow

1. Fork the repo and create a branch.
2. Make your changes.
3. Test locally with `python build.py` if your change affects the build.
4. Open a PR against `master`.

Keep PRs small and focused.

## Need Help?

Open an issue or start a discussion — we’re happy to help.
