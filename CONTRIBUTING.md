# Contributing

Thanks for your interest in contributing! This project builds and distributes static binaries of clang tools (clang-format, clang-tidy, clang-query, clang-apply-replacements) for multiple platforms.

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

## Pull Request Flow

1. Fork the repo and create a branch.
2. Make your changes.
3. Test locally with `python build.py` if your change affects the build.
4. Open a PR against `master`.

Keep PRs small and focused. If adding a new LLVM version, always test at least one platform locally before opening the PR.

## Need Help?

Open an issue or start a discussion — we’re happy to help.
