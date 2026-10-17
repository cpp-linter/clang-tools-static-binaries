#!/usr/bin/env python3
"""Plan builds and publish releases for clang-tools-static-binaries.

Each LLVM version gets its own immutable GitHub release, tagged after its
source release (for example ``llvm-22.1.8``). A version is built once; later
workflow runs only build versions that do not have a release yet. A rebuild
of the same source gets a revision suffix (``llvm-22.1.8-2``), like a Debian
package revision.

The combined release that downstream projects already use (``YYYY.MM.DD-sha``,
marked as latest) is then assembled by copying the files of every version
release, without compiling anything.

Subcommands (all are called from .github/workflows/build.yml):

* ``plan``      - decide which versions to copy from the latest combined
                  release ("seed") and which to build; print a JSON plan.
* ``seed``      - copy one version's files out of an existing combined
                  release into a new version release.
* ``version``   - publish one version release from the build artifacts.
* ``aggregate`` - publish the combined release from all version releases.

``gh`` (GitHub CLI) must be installed and ``GH_TOKEN``/``GH_REPO`` set for the
subcommands that talk to GitHub.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build
import release

PLATFORMS = [
    {"platform": "linux-amd64", "runner": "ubuntu-22.04"},
    {"platform": "linux-arm64", "runner": "ubuntu-22.04-arm"},
    {"platform": "macos-amd64", "runner": "macos-15-intel"},
    {"platform": "macos-arm64", "runner": "macos-14"},
    {"platform": "windows-amd64", "runner": "windows-latest"},
    {"platform": "windows-arm64", "runner": "windows-11-arm"},
]
PLATFORM_NAMES = [p["platform"] for p in PLATFORMS]

# Files whose changes affect the binaries. A pull request that touches one of
# them builds the newest and the oldest version to check the change.
BUILD_INPUTS = ["build.py", "*.patch", ".github/workflows/build.yml"]

METADATA_FILES = {"SHA512SUMS", "versions.json"}


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


def base_tag(source: str) -> str:
    """``llvm-project-22.1.8.src`` -> ``llvm-22.1.8``."""
    match = re.fullmatch(r"llvm-project-(.+)\.src", source)
    if not match:
        raise ValueError(f"Unexpected source name in releases.json: {source!r}")
    return f"llvm-{match.group(1)}"


def _revision(tag: str, base: str) -> int | None:
    """Revision of *tag* for *base*: 1 for ``base``, N for ``base-N``."""
    if tag == base:
        return 1
    match = re.fullmatch(re.escape(base) + r"-(\d+)", tag)
    return int(match.group(1)) if match else None


def current_tag(source: str, existing: set[str]) -> str | None:
    """Newest existing release tag for *source*, or None."""
    base = base_tag(source)
    revisions = [r for t in existing if (r := _revision(t, base)) is not None]
    if not revisions:
        return None
    top = max(revisions)
    return base if top == 1 else f"{base}-{top}"


def next_tag(source: str, existing: set[str]) -> str:
    """Tag for a new build of *source*."""
    base = base_tag(source)
    revisions = [r for t in existing if (r := _revision(t, base)) is not None]
    if not revisions:
        return base
    return f"{base}-{max(revisions) + 1}"


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def sort_versions(versions) -> list[str]:
    return sorted(versions, key=int, reverse=True)


def make_plan(
    *,
    event: str,
    releases: dict[str, str],
    published: set[str],
    latest_sources: dict[str, str] | None,
    base_releases: dict[str, str] | None = None,
    build_inputs_changed: bool = False,
    rebuild: list[str] | None = None,
) -> dict:
    """Decide what to seed and what to build.

    *published* holds the tags of all published releases. *latest_sources*
    is ``llvm_versions`` from the latest combined release's versions.json.
    On pull requests nothing is published, so only the versions changed
    against *base_releases* are built (plus the newest and oldest version
    when the build inputs changed).
    """
    rebuild = rebuild or []
    unknown = [v for v in rebuild if v not in releases]
    if unknown:
        raise ValueError(f"Not in releases.json: {', '.join(unknown)}")

    seed: list[dict] = []
    builds: list[dict] = []

    if event == "pull_request":
        base_releases = base_releases or {}
        changed = [v for v, s in releases.items() if base_releases.get(v) != s]
        if build_inputs_changed:
            ordered = sort_versions(releases)
            changed += [ordered[0], ordered[-1]]
        for version in sort_versions(set(changed)):
            builds.append(
                {"version": version, "tag": next_tag(releases[version], set())}
            )
    else:
        latest_sources = latest_sources or {}
        for version in sort_versions(releases):
            source = releases[version]
            if version in rebuild:
                builds.append({"version": version, "tag": next_tag(source, published)})
            elif current_tag(source, published) is None:
                if latest_sources.get(version) == source:
                    seed.append({"version": version, "tag": base_tag(source)})
                else:
                    builds.append(
                        {"version": version, "tag": next_tag(source, published)}
                    )

    include = []
    for item in builds:
        for p in PLATFORMS:
            os_name, arch = p["platform"].split("-")
            include.append(
                {
                    "clang-version": item["version"],
                    "release": releases[item["version"]],
                    "tag": item["tag"],
                    "platform": p["platform"],
                    "os": os_name,
                    "arch": arch,
                    "runner": p["runner"],
                }
            )
    return {
        "seed": seed,
        "builds": builds,
        "build-matrix": {"include": include},
    }


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------


def gh(*args: str, capture: bool = True) -> str:
    print(f">>> gh {' '.join(args)}", flush=True)
    result = subprocess.run(
        ["gh", *args], check=True, text=True, capture_output=capture
    )
    return result.stdout if capture else ""


def published_tags() -> set[str]:
    out = gh(
        "api",
        "--paginate",
        "repos/{owner}/{repo}/releases?per_page=100",
        "--jq",
        ".[] | select(.draft == false) | .tag_name",
    )
    return {line for line in out.splitlines() if line}


def latest_tag() -> str | None:
    try:
        out = gh("api", "repos/{owner}/{repo}/releases/latest", "--jq", ".tag_name")
    except subprocess.CalledProcessError:
        return None
    return out.strip() or None


def latest_versions_json() -> dict | None:
    tag = latest_tag()
    if tag is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        try:
            gh("release", "download", tag, "-p", "versions.json", "-D", tmp)
        except subprocess.CalledProcessError:
            return None
        return json.loads((Path(tmp) / "versions.json").read_text(encoding="utf-8"))


def upload_with_retry(tag: str, path: Path, retries: int = 5) -> None:
    delay = 10
    for attempt in range(1, retries + 1):
        try:
            gh("release", "upload", tag, str(path), capture=False)
            return
        except subprocess.CalledProcessError:
            if attempt == retries:
                raise
            print(f"::warning::Upload of {path.name} failed ({attempt}/{retries})")
            time.sleep(delay)
            delay *= 2


def sha512(path: Path) -> str:
    digest = hashlib.sha512()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_sums(path: Path) -> dict[str, str]:
    sums = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, name = line.split(maxsplit=1)
            sums[name.strip().lstrip("*")] = digest
    return sums


def write_sums(path: Path, sums: dict[str, str]) -> None:
    path.write_text(
        "".join(f"{sums[name]}  {name}\n" for name in sorted(sums)), encoding="utf-8"
    )


def expected_files(version: str) -> set[str]:
    names = set()
    for platform_name in PLATFORM_NAMES:
        exe = ".exe" if platform_name.startswith("windows") else ""
        for tool in build.active_tools(version):
            names.add(f"{tool}-{version}_{platform_name}{exe}")
    return names


def select_files(version: str, paths) -> dict[str, Path]:
    """Map file name to path for the binaries of *version*; ignore anything else."""
    wanted = expected_files(version)
    files = {}
    for path in paths:
        if path.is_file() and path.name in wanted:
            files[path.name] = path
        elif path.is_file() and path.name not in METADATA_FILES:
            print(f"::warning::Ignoring unexpected file {path.name}")
    return files


def check_files(version: str, files: dict[str, Path], sums: dict[str, str]) -> None:
    """Fail unless every tool is present for every platform and matches SHA512SUMS."""
    missing = sorted(expected_files(version) - set(files))
    if missing:
        raise SystemExit(
            f"LLVM {version}: {len(missing)} binaries missing, not publishing. "
            f"First missing: {', '.join(missing[:6])}"
        )
    for name, path in files.items():
        if sums.get(name) != sha512(path):
            raise SystemExit(f"LLVM {version}: checksum mismatch for {name}")


def create_release(
    tag: str,
    title: str,
    notes: str,
    files: list[Path],
    *,
    latest: bool,
    target: str | None,
    dry_run: bool,
) -> None:
    """Create a draft, upload *files*, then publish it."""
    print(f"Release {tag}: {len(files)} files, latest={latest}")
    if dry_run:
        for f in files:
            print(f"  [dry-run] would upload {f.name}")
        return
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as nf:
        nf.write(notes)
    args = [
        "release",
        "create",
        tag,
        "--draft",
        "--title",
        title,
        "--notes-file",
        nf.name,
    ]
    if target:
        args += ["--target", target]
    gh(*args, capture=False)
    for f in files:
        upload_with_retry(tag, f)
    gh(
        "release",
        "edit",
        tag,
        "--draft=false",
        f"--latest={str(latest).lower()}",
        capture=False,
    )


def version_notes(version: str, source: str, how: str) -> str:
    return "\n".join(
        [
            f"LLVM {version} binaries built from `{source}`.",
            "",
            how,
            "",
            "Platforms: " + ", ".join(PLATFORM_NAMES),
            "",
            "Tools: " + ", ".join(build.active_tools(version)),
            "",
            "Verify downloads with `sha512sum -c SHA512SUMS --ignore-missing`.",
            "",
        ]
    )


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_plan(args: argparse.Namespace) -> None:
    releases = build.RELEASES
    rebuild = args.rebuild.replace(",", " ").split()
    if args.event == "pull_request":
        base = json.loads(Path(args.base_releases).read_text(encoding="utf-8"))
        plan = make_plan(
            event=args.event,
            releases=releases,
            published=set(),
            latest_sources=None,
            base_releases=base,
            build_inputs_changed=args.build_inputs_changed,
        )
    else:
        latest = latest_versions_json() or {}
        plan = make_plan(
            event=args.event,
            releases=releases,
            published=published_tags(),
            latest_sources=latest.get("llvm_versions"),
            rebuild=rebuild,
        )
        plan["latest"] = latest.get("release_tag")

    print(json.dumps(plan, indent=2), file=sys.stderr)
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as out:
            out.write(f"seed={json.dumps(plan['seed'])}\n")
            out.write(f"builds={json.dumps(plan['builds'])}\n")
            out.write(f"build-matrix={json.dumps(plan['build-matrix'])}\n")
            out.write(f"has-seed={str(bool(plan['seed'])).lower()}\n")
            out.write(f"has-builds={str(bool(plan['builds'])).lower()}\n")
            out.write(f"latest={plan.get('latest') or ''}\n")


def cmd_seed(args: argparse.Namespace) -> None:
    version, tag, src_tag = args.version, args.tag, args.from_tag
    source = build.RELEASES[version]
    work = Path(tempfile.mkdtemp(prefix=f"seed-{version}-"))
    try:
        gh(
            "release",
            "download",
            src_tag,
            "-p",
            f"*-{version}_*",
            "-p",
            "SHA512SUMS",
            "-D",
            str(work),
        )
        all_sums = read_sums(work / "SHA512SUMS")
        (work / "SHA512SUMS").unlink()
        files = select_files(version, work.iterdir())
        sums = {n: all_sums[n] for n in files if n in all_sums}
        check_files(version, files, sums)
        write_sums(work / "SHA512SUMS", sums)
        notes = version_notes(
            version,
            source,
            f"These files were copied unchanged from release `{src_tag}` "
            "when the project moved to one release per LLVM version.",
        )
        create_release(
            tag,
            tag,
            notes,
            [work / "SHA512SUMS", *sorted(files.values())],
            latest=False,
            target=None,
            dry_run=args.dry_run,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def cmd_version(args: argparse.Namespace) -> None:
    version, tag = args.version, args.tag
    source = build.RELEASES[version]
    sums: dict[str, str] = {}
    for sums_file in Path(args.artifacts).rglob("SHA512SUMS"):
        sums.update(read_sums(sums_file))
    files = select_files(version, Path(args.artifacts).rglob("*"))
    check_files(version, files, sums)
    out = Path(tempfile.mkdtemp()) / "SHA512SUMS"
    write_sums(out, {n: sums[n] for n in files})
    notes = version_notes(
        version, source, f"Built by {args.run_url}" if args.run_url else ""
    )
    create_release(
        tag,
        tag,
        notes,
        [out, *sorted(files.values())],
        latest=False,
        target=args.target,
        dry_run=args.dry_run,
    )


def tag_source(tag: str) -> str:
    """``llvm-22.1.8-2`` -> ``llvm-project-22.1.8.src``."""
    match = re.fullmatch(r"llvm-(\d+(?:\.\d+)*)(?:-\d+)?", tag)
    if not match:
        raise ValueError(f"Not a version release tag: {tag!r}")
    return f"llvm-project-{match.group(1)}.src"


def fallback_tag(version: str, published: set[str]) -> str | None:
    """Newest published release of LLVM major *version*, of any source release."""
    best = None
    for tag in published:
        match = re.fullmatch(rf"llvm-({re.escape(version)}(?:\.\d+)*)(?:-(\d+))?", tag)
        if match:
            key = (
                tuple(int(x) for x in match.group(1).split(".")),
                int(match.group(2) or 1),
            )
            if best is None or key > best[0]:
                best = (key, tag)
    return best[1] if best else None


def choose_tags(
    releases: dict[str, str], published: set[str], dry_run: bool = False
) -> dict[str, str]:
    """Pick the release to copy for each LLVM major in *releases*.

    When the source in releases.json has no release yet (its build failed),
    keep shipping the newest earlier release of that major, so one failing
    version does not hold back the others. A major with no release at all
    is left out.
    """
    tags: dict[str, str] = {}
    for version in sort_versions(releases):
        tag = current_tag(releases[version], published)
        if tag is None and dry_run:
            tag = base_tag(releases[version])
            print(f"[dry-run] LLVM {version} has no published release; assuming {tag}")
        elif tag is None:
            tag = fallback_tag(version, published)
            print(
                f"::warning::LLVM {version} ({releases[version]}) has no published "
                f"release; {'keeping ' + tag if tag else 'leaving it out'}."
            )
        if tag:
            tags[version] = tag
    return tags


def cmd_aggregate(args: argparse.Namespace) -> None:
    published = published_tags()
    tags = choose_tags(build.RELEASES, published, args.dry_run)
    if not tags:
        raise SystemExit("No version has a published release.")
    sources = {v: tag_source(t) for v, t in tags.items()}

    latest = latest_versions_json() or {}
    if latest.get("release_tags") == tags and latest.get("llvm_versions") == sources:
        print(
            f"Latest release {latest.get('release_tag')} already has these versions; nothing to do."
        )
        return
    if args.tag in published:
        raise SystemExit(f"Release {args.tag} already exists.")

    work = Path(tempfile.mkdtemp(prefix="aggregate-"))
    try:
        release.generate_versions_json(
            args.tag, str(work), release_tags=tags, llvm_versions=sources
        )
        notes_path = release.generate_release_notes(
            str(work), release_tags=tags, llvm_versions=sources
        )
        notes = notes_path.read_text(encoding="utf-8")
        if latest.get("release_tag"):
            notes += (
                f"\n**Full Changelog**: https://github.com/{os.environ.get('GITHUB_REPOSITORY', 'cpp-linter/clang-tools-static-binaries')}"
                f"/compare/{latest['release_tag']}...{args.tag}\n"
            )
        print(notes)
        if args.dry_run:
            for version, tag in tags.items():
                print(f"[dry-run] would copy {tag} (LLVM {version})")
            return

        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as nf:
            nf.write(notes)
        gh(
            "release",
            "create",
            args.tag,
            "--draft",
            "--title",
            args.tag,
            "--notes-file",
            nf.name,
            "--target",
            args.target,
            capture=False,
        )
        all_sums: dict[str, str] = {}
        # Copy one version at a time so the runner never holds every binary.
        for version, tag in tags.items():
            vdir = work / version
            gh("release", "download", tag, "-D", str(vdir))
            sums = read_sums(vdir / "SHA512SUMS")
            files = select_files(version, vdir.iterdir())
            check_files(version, files, sums)
            for name in sorted(files):
                upload_with_retry(args.tag, files[name])
                all_sums[name] = sums[name]
            shutil.rmtree(vdir)
        write_sums(work / "SHA512SUMS", all_sums)
        upload_with_retry(args.tag, work / "SHA512SUMS")
        upload_with_retry(args.tag, work / "versions.json")
        gh("release", "edit", args.tag, "--draft=false", "--latest=true", capture=False)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="Decide what to seed and build.")
    p.add_argument("--event", required=True, help="GitHub event name.")
    p.add_argument("--base-releases", help="releases.json of the PR base branch.")
    p.add_argument("--build-inputs-changed", action="store_true")
    p.add_argument(
        "--rebuild", default="", help="LLVM majors to rebuild, e.g. '22 23'."
    )
    p.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("seed", help="Copy a version out of a combined release.")
    p.add_argument("--version", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--from-tag", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("version", help="Publish one version from build artifacts.")
    p.add_argument("--version", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--artifacts", required=True)
    p.add_argument("--target", help="Commit to tag.")
    p.add_argument("--run-url", default="")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_version)

    p = sub.add_parser("aggregate", help="Publish the combined latest release.")
    p.add_argument("--tag", required=True)
    p.add_argument("--target", required=True, help="Commit to tag.")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_aggregate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
