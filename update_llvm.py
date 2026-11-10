#!/usr/bin/env python3
"""Move releases.json and sources.sha256 to the newest LLVM releases.

For every LLVM major in releases.json, and for the major after the newest one,
this script looks for a newer final release of LLVM on GitHub. For each one it
finds, it does what CONTRIBUTING.md asks a person to do:

  1. downloads the source tarball and its signature
  2. checks the signature against LLVM's release keys
  3. writes the new source to releases.json and its sha256 to sources.sha256
  4. for the newest major, updates the version in the README's examples

The Monitor LLVM workflow runs it every day and opens a pull request with the
result. Usage:

    python3 update_llvm.py          # update the files
    python3 update_llvm.py --check  # only print what is new

``gpg`` must be installed. Set ``GITHUB_TOKEN`` to avoid GitHub's rate limit
for anonymous API calls, and ``LLVM_RELEASES_API`` to read the list of LLVM
releases from another URL (for example a local file:// copy).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RELEASES_FILE = ROOT / "releases.json"
SOURCES_FILE = ROOT / "sources.sha256"
README = ROOT / "README.md"

# The newest 100 releases always include the newest final one of every major
# that still gets patch releases.
LLVM_RELEASES_API = os.environ.get(
    "LLVM_RELEASES_API",
    "https://api.github.com/repos/llvm/llvm-project/releases?per_page=100",
)
LLVM_DOWNLOAD = "https://github.com/llvm/llvm-project/releases/download"
RELEASE_KEYS_URL = "https://releases.llvm.org/release-keys.asc"

Version = tuple[int, int, int]


def source_version(source: str) -> Version:
    """``llvm-project-22.1.8.src`` -> ``(22, 1, 8)``."""
    match = re.fullmatch(r"llvm-project-(\d+)\.(\d+)\.(\d+)\.src", source)
    if not match:
        raise SystemExit(f"Unexpected source name in releases.json: {source!r}")
    return int(match[1]), int(match[2]), int(match[3])


def dotted(version: Version) -> str:
    return ".".join(str(part) for part in version)


def final_releases(releases: list[dict]) -> list[Version]:
    """The final (not draft, pre-release or release candidate) LLVM versions."""
    versions = []
    for release in releases:
        match = re.fullmatch(
            r"llvmorg-(\d+)\.(\d+)\.(\d+)", release.get("tag_name", "")
        )
        if match and not release.get("draft") and not release.get("prerelease"):
            versions.append((int(match[1]), int(match[2]), int(match[3])))
    return versions


def find_updates(
    current: dict[str, str], available: list[Version]
) -> dict[str, Version]:
    """Major -> newer version, for the majors in *current* and the next one."""
    have = {int(major): source_version(source) for major, source in current.items()}
    updates = {}
    for major in [*have, max(have) + 1]:
        newest = max((v for v in available if v[0] == major), default=None)
        if newest is not None and (major not in have or newest > have[major]):
            updates[str(major)] = newest
    return updates


def fetch_json(url: str):
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json"}
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def download(url: str, dest: Path) -> None:
    print(f"Downloading {url}", flush=True)
    urllib.request.urlretrieve(url, dest)


def verified_sha256(version: Version, work: Path, gnupg_home: Path) -> str:
    """Download the source tarball of *version*, check its signature and
    return its sha256."""
    name = f"llvm-project-{dotted(version)}.src.tar.xz"
    base = f"{LLVM_DOWNLOAD}/llvmorg-{dotted(version)}"
    tarball, signature = work / name, work / f"{name}.sig"
    download(f"{base}/{name}", tarball)
    download(f"{base}/{name}.sig", signature)
    result = subprocess.run(
        [
            "gpg",
            "--batch",
            "--homedir",
            str(gnupg_home),
            "--status-fd",
            "1",
            "--verify",
            str(signature),
            str(tarball),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or "[GNUPG:] VALIDSIG" not in result.stdout:
        raise SystemExit(
            f"{name}: the signature does not verify against {RELEASE_KEYS_URL}.\n"
            f"{result.stdout}{result.stderr}"
        )
    print(f"{name}: signature verified")
    digest = hashlib.sha256()
    with open(tarball, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    tarball.unlink()
    return digest.hexdigest()


def import_release_keys(work: Path) -> Path:
    home = work / "gnupg"
    home.mkdir(mode=0o700)
    keys = work / "release-keys.asc"
    download(RELEASE_KEYS_URL, keys)
    subprocess.run(
        ["gpg", "--batch", "--homedir", str(home), "--import", str(keys)],
        check=True,
        capture_output=True,
    )
    return home


def apply_updates(current: dict[str, str], hashes: dict[str, str]) -> dict[str, str]:
    """Write releases.json, sources.sha256 and the README for *hashes*
    (major -> (version, sha256)); return the new releases."""
    newest_before = max(current, key=int)
    releases = dict(current)
    lines = SOURCES_FILE.read_text(encoding="utf-8").splitlines()
    for major, (version, sha256) in hashes.items():
        new_line = f"{sha256}  llvm-project-{version}.src.tar.xz"
        old_source = current.get(major)
        if old_source is None:
            lines.insert(0, new_line)
        else:
            lines = [
                new_line if line.endswith(f"  {old_source}.tar.xz") else line
                for line in lines
            ]
        releases[major] = f"llvm-project-{version}.src"
    releases = dict(
        sorted(releases.items(), key=lambda item: int(item[0]), reverse=True)
    )
    RELEASES_FILE.write_text(json.dumps(releases, indent=2) + "\n", encoding="utf-8")
    SOURCES_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # The README's examples show the newest major. A new major changes more
    # than a version number there, so that is left to a person.
    if newest_before in hashes and max(releases, key=int) == newest_before:
        old = dotted(source_version(current[newest_before]))
        new = hashes[newest_before][0]
        text = README.read_text(encoding="utf-8")
        README.write_text(
            re.sub(rf"(?<![\d.]){re.escape(old)}(?![\d.])", new, text), encoding="utf-8"
        )
    return releases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only print what is new")
    args = parser.parse_args()

    current = json.loads(RELEASES_FILE.read_text(encoding="utf-8"))
    updates = find_updates(current, final_releases(fetch_json(LLVM_RELEASES_API)))
    if not updates:
        print("releases.json already has the newest release of every LLVM major.")
        return
    for major, version in updates.items():
        old = current.get(major, "not built yet")
        print(f"LLVM {major}: {old} -> llvm-project-{dotted(version)}.src")
    if args.check:
        return

    hashes = {}
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        gnupg_home = import_release_keys(work)
        for major, version in updates.items():
            hashes[major] = (
                dotted(version),
                verified_sha256(version, work, gnupg_home),
            )
    apply_updates(current, hashes)

    # For the workflow: the title and the list of the pull request.
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        versions = [hashes[m][0] for m in sorted(hashes, key=int, reverse=True)]
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"versions={', '.join(versions)}\n")
            fh.write(
                f"new-major={'true' if any(m not in current for m in hashes) else 'false'}\n"
            )


if __name__ == "__main__":
    main()
