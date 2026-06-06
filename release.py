#!/usr/bin/env python3
"""Generate release metadata (versions.json and release notes) for clang-tools-static-binaries.

Usage:
    python3 release.py --tag 2026.06.04-a1b2c3d4

This script is called from the CI workflow (.github/workflows/build.yml) in the
``draft-release`` job to produce:

* ``versions.json`` – machine-readable metadata about this release.
* ``release-notes.md`` – human-readable Markdown table of included LLVM versions.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow importing build.py from the repo root (same directory as this script).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402


def generate_versions_json(tag: str, output_dir: str = ".") -> Path:
    """Write ``versions.json`` to *output_dir* and return its path.

    The generated JSON contains the build timestamp, release tag, and a
    mapping of LLVM release names (keys) to source tarball identifiers
    (values).
    """
    data = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "release_tag": tag,
        "llvm_versions": build.RELEASES,
    }
    out_path = Path(output_dir) / "versions.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Created {out_path} ({len(build.RELEASES)} versions)")
    return out_path


def generate_release_notes(output_dir: str = ".") -> Path:
    """Write ``release-notes.md`` to *output_dir* and return its path.

    The notes include a Markdown table of every LLVM version and its
    corresponding source tarball, plus the list of supported platforms.
    """
    lines = [
        "## LLVM Versions in this release",
        "",
        "| Version | Source |",
        "|---------|--------|",
    ]
    # Sort by major version number for readability.
    for ver, src in sorted(
        build.RELEASES.items(),
        key=lambda x: int(x[0].split(".")[0]),
    ):
        lines.append(f"| {ver} | `{src}` |")

    lines += [
        "",
        "## Platforms",
        "",
        "Linux x86-64 / Linux ARM64 / macOS x86-64 / macOS ARM64 / Windows x86-64 / Windows ARM64",
    ]

    out_path = Path(output_dir) / "release-notes.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Created {out_path}")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate versions.json and release-notes.md for a release."
    )
    parser.add_argument(
        "--tag",
        required=True,
        metavar="TAG",
        help="Release tag (e.g. 2026.06.04-a1b2c3d4).",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default=".",
        metavar="DIR",
        help="Directory to write output files (default: current directory).",
    )
    args = parser.parse_args()

    generate_versions_json(args.tag, args.output_dir)
    generate_release_notes(args.output_dir)


if __name__ == "__main__":
    main()
