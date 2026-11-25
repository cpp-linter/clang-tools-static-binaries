#!/usr/bin/env python3
"""Count the downloads of every release of clang-tools-static-binaries.

GitHub counts how often each release file was downloaded, but only per file
and only the running total. This script adds up the counts of every release
and writes:

* ``stats.json``  - the totals now, by LLVM major, platform, tool and release.
* ``history.csv`` - one row per day with the totals, so that the growth over
  time can be shown. GitHub keeps no history of the counts, so the rows start
  on the day this script first ran.

Checksums and other metadata files (``SHA512SUMS``, ``*.sha512sum``,
``*.json``, ``LICENSE.TXT``) are not counted: people download them next to a
binary, not instead of one. Counts include downloads by CI jobs, so they show
use, not users.

The Stats workflow (.github/workflows/stats.yml) runs this daily and commits
both files to the ``download-stats`` branch, where the download page and the
README badge read them.

Usage::

    python3 download_stats.py --output stats --history-in old-history.csv

``gh`` (GitHub CLI) must be installed and ``GH_TOKEN`` set, or pass
``--releases releases.json`` with the output of the releases API.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_REPO = "cpp-linter/clang-tools-static-binaries"
SCHEMA_VERSION = 1

# Files that are not tools: checksums, the old versions.json, build details
# and the license.
NOT_COUNTED = re.compile(
    r"(^SHA512SUMS$|\.sha512sum$|\.json$|^LICENSE\.TXT$|\.txt$)", re.IGNORECASE
)
# <tool>-<major>[_<platform>][.exe|.tar.gz|.zip], e.g. clang-tidy-23_linux-amd64,
# clang-tools-23_macos-arm64.tar.gz or clang-resource-headers-23.tar.gz. Some
# earlier releases also had files named after the full version
# (clang-format-12.0.1_linux-amd64).
FILE_NAME = re.compile(
    r"^(?P<tool>[a-z][a-z-]*?)-(?P<major>\d+)(?:\.\d+)*"
    r"(?:_(?P<platform>[a-z0-9]+(?:-[a-z0-9]+)+))?"
    r"(?P<ext>\.exe|\.tar\.gz|\.tar\.xz|\.zip)?$"
)
# Platform names of the releases before 2026.06.
PLATFORM_ALIASES = {
    "macosx-amd64": "macos-amd64",
    "macos-intel-amd64": "macos-amd64",
    "macosx-arm64": "macos-arm64",
}
# Archives that hold more than one tool.
ARCHIVES = {
    "clang-tools": "archive with everything",
    "clang-resource-headers": "clang's headers",
    "clang-tools-scripts": "helper scripts",
}
HISTORY_FIELDS = ["date", "total", "llvm_releases", "combined_releases"]


def fetch_releases(repo: str) -> list[dict]:
    """Every published release of *repo* with its assets, from the GitHub API."""
    out = subprocess.run(
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repo}/releases?per_page=100",
        ],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    return [release for page in json.loads(out) for release in page]


def describe(name: str) -> dict | None:
    """Tool, LLVM major and platform of the release file *name*.

    Returns None for files that are not counted. A file whose name does not
    follow the usual pattern is counted under the tool "other".
    """
    if NOT_COUNTED.search(name):
        return None
    match = FILE_NAME.match(name)
    if not match:
        return {"tool": "other", "major": None, "platform": None}
    tool = match["tool"]
    platform = match["platform"]
    if platform:
        platform = PLATFORM_ALIASES.get(platform, platform)
    return {
        "tool": ARCHIVES.get(tool, tool),
        "major": match["major"],
        "platform": platform or "any",
    }


def short(number: int) -> str:
    """421337 -> "421k", 1234567 -> "1.2M"."""
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M".replace(".0M", "M")
    if number >= 1_000:
        return f"{number // 1_000}k"
    return str(number)


def count(releases: list[dict], repo: str, now: datetime) -> dict:
    """Add up the download counts of *releases* (the releases API's output)."""
    by_major: Counter[str] = Counter()
    by_platform: Counter[str] = Counter()
    by_tool: Counter[str] = Counter()
    per_release = []
    llvm = combined = 0
    for release in releases:
        if release.get("draft"):
            continue
        downloads = 0
        for asset in release.get("assets", []):
            what = describe(asset["name"])
            if what is None:
                continue
            n = asset.get("download_count", 0)
            downloads += n
            by_tool[what["tool"]] += n
            if what["major"]:
                by_major[what["major"]] += n
            if what["platform"]:
                by_platform[what["platform"]] += n
        tag = release["tag_name"]
        if tag.startswith("llvm-"):
            llvm += downloads
        else:
            combined += downloads
        per_release.append(
            {
                "tag": tag,
                "published_at": release.get("published_at"),
                "downloads": downloads,
            }
        )

    def ranked(counter: Counter[str]) -> dict[str, int]:
        return dict(counter.most_common())

    total = llvm + combined
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repository": repo,
        "total": total,
        "total_short": short(total),
        # Releases named llvm-<version>, one per LLVM version (since 2026-10).
        "llvm_releases": llvm,
        # Releases that hold every version: the dated ones before 2026-10
        # and the compatibility releases that took their place.
        "combined_releases": combined,
        "by_major": dict(
            sorted(by_major.items(), key=lambda item: int(item[0]), reverse=True)
        ),
        "by_platform": ranked(by_platform),
        "by_tool": ranked(by_tool),
        "releases": sorted(per_release, key=lambda r: r["downloads"], reverse=True),
    }


def update_history(path_in: Path | None, stats: dict) -> list[dict]:
    """The rows of history.csv with today's totals added (or replaced)."""
    rows: list[dict] = []
    if path_in and path_in.is_file():
        with path_in.open(newline="", encoding="utf-8") as f:
            rows = [row for row in csv.DictReader(f) if row.get("date")]
    today = stats["generated_at"][:10]
    rows = [row for row in rows if row["date"] != today]
    rows.append({field: stats[field] for field in HISTORY_FIELDS[1:]} | {"date": today})
    rows.sort(key=lambda row: row["date"])
    return rows


def summary(stats: dict) -> str:
    """The totals as Markdown, for the workflow's job summary."""
    lines = [
        f"## {stats['total']:,} downloads",
        "",
        (
            f"Per-version `llvm-*` releases: {stats['llvm_releases']:,}; "
            f"releases with every version: {stats['combined_releases']:,}. "
            "Checksums and metadata files are not counted."
        ),
        "",
    ]
    for title, key in (
        ("LLVM", "by_major"),
        ("Platform", "by_platform"),
        ("Tool", "by_tool"),
    ):
        lines += [f"| {title} | Downloads |", "| --- | ---: |"]
        lines += [f"| {name} | {n:,} |" for name, n in stats[key].items()]
        lines.append("")
    lines += ["| Release | Downloads |", "| --- | ---: |"]
    lines += [f"| {r['tag']} | {r['downloads']:,} |" for r in stats["releases"][:15]]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", "-o", default="stats", help="Output directory.")
    parser.add_argument(
        "--history-in", type=Path, help="The current history.csv, if there is one."
    )
    parser.add_argument(
        "--releases",
        type=Path,
        help="Read the releases from this JSON file instead of the GitHub API.",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=os.environ.get("GITHUB_STEP_SUMMARY"),
        help="Also write the totals as Markdown here.",
    )
    args = parser.parse_args()

    repo = os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO)
    if args.releases:
        releases = json.loads(args.releases.read_text(encoding="utf-8"))
    else:
        releases = fetch_releases(repo)
    stats = count(releases, repo, datetime.now(UTC))
    history = update_history(args.history_in, stats)

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "stats.json").write_text(
        json.dumps(stats, indent=2) + "\n", encoding="utf-8"
    )
    with (out / "history.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(history)

    text = summary(stats)
    print(text)
    if args.summary:
        with Path(args.summary).open("a", encoding="utf-8") as f:
            f.write(text)
    print(f"Wrote {out / 'stats.json'} and {out / 'history.csv'}", file=sys.stderr)


if __name__ == "__main__":
    main()
