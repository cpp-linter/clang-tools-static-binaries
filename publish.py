#!/usr/bin/env python3
"""Plan builds and publish releases for clang-tools-static-binaries.

Each LLVM version gets its own immutable GitHub release, tagged after its
source release (for example ``llvm-22.1.8``). A version is built once; later
workflow runs only build versions that do not have a release yet. A rebuild
of the same source gets a revision suffix (``llvm-22.1.8-2``), like a Debian
package revision.

There is no release that holds every version. Instead, ``index.json`` lists
every file of every supported version with its URL, size and SHA-512, and is
published on GitHub Pages with a download page, ``index.html`` (see
download_page.py). Downstream tools read the index, the way uv reads its
Python download metadata.

Subcommands (all are called from .github/workflows/build.yml):

* ``plan``    - decide which versions to copy out of the last combined
                release (SEED_FROM, once) and which to build.
* ``seed``    - copy one version's files out of SEED_FROM into a new
                version release.
* ``prepare`` - check one version's build artifacts and acceptance results,
                write its BUILDINFO.json and list the files to attest.
* ``version`` - publish one version release from the build artifacts.
* ``index``   - write index.json and index.html from the version releases.

``gh`` (GitHub CLI) must be installed and ``GH_TOKEN``/``GH_REPO`` set for the
subcommands that talk to GitHub.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build
import download_page

PLATFORMS = [
    {"platform": "linux-amd64", "runner": "ubuntu-22.04"},
    {"platform": "linux-arm64", "runner": "ubuntu-22.04-arm"},
    {"platform": "macos-amd64", "runner": "macos-15-intel"},
    {"platform": "macos-arm64", "runner": "macos-15"},
    {"platform": "windows-amd64", "runner": "windows-latest"},
    {"platform": "windows-arm64", "runner": "windows-11-arm"},
]
PLATFORM_NAMES = [p["platform"] for p in PLATFORMS]

# Files whose changes affect the binaries. A pull request that touches one of
# them builds the newest and the oldest version to check the change.
BUILD_INPUTS = ["build.py", "acceptance.py", "*.patch", ".github/workflows/build.yml"]

METADATA_FILES = {"SHA512SUMS", "versions.json"}

# How each platform was built, merged from the buildinfo-<suffix>.json files
# that build.py writes.
BUILDINFO_FILE = "BUILDINFO.json"
BUILDINFO_SCHEMA_VERSION = 1

DEFAULT_REPO = "cpp-linter/clang-tools-static-binaries"
INDEX_SCHEMA_VERSION = 1

# The last release that held every LLVM version in one place. Versions whose
# source matches it are copied out of it once instead of being built again.
SEED_FROM = "2026.09.01-5fb8802d"


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
    is ``llvm_versions`` from the versions.json of release SEED_FROM.
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


def seed_versions_json() -> dict | None:
    """versions.json of the last combined release, or None if it is gone."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            gh("release", "download", SEED_FROM, "-p", "versions.json", "-D", tmp)
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


def _digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha512(path: Path) -> str:
    return _digest(path, "sha512")


def sha256(path: Path) -> str:
    return _digest(path, "sha256")


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


def bundle_name(version: str, platform_name: str) -> str:
    """Name of the archive with everything of *version* for one platform."""
    return f"clang-tools-{version}_{platform_name}.tar.gz"


def bundle_names(version: str) -> set[str]:
    return {bundle_name(version, platform_name) for platform_name in PLATFORM_NAMES}


def prepared_files(version: str) -> set[str]:
    """Files that `prepare` writes in the publish job, from the build
    artifacts: no build has their checksum."""
    return {BUILDINFO_FILE} | bundle_names(version)


def common_files(version: str) -> set[str]:
    """Files besides the binaries that every newly built release has.

    clang's builtin headers, the helper scripts and LLVM's license are the
    same for every platform; BUILDINFO.json describes the builds of all
    platforms; each bundle holds all of this for one platform. Older releases
    do not have all of them.
    """
    return {
        build.resource_headers_name(version),
        build.scripts_name(version),
        build.LICENSE_FILE,
    } | prepared_files(version)


def select_files(
    version: str, paths, extra: set[str] | frozenset[str] = frozenset()
) -> dict[str, Path]:
    """Map file name to path for the binaries of *version* and the *extra*
    files; ignore anything else."""
    wanted = expected_files(version) | extra
    files = {}
    for path in paths:
        if path.is_file() and path.name in wanted:
            files[path.name] = path
        elif (
            path.is_file()
            and path.name not in METADATA_FILES | {BUILDINFO_FILE}
            and not re.fullmatch(r"buildinfo-.+\.json", path.name)
        ):
            print(f"::warning::Ignoring unexpected file {path.name}")
    return files


def check_files(
    version: str,
    files: dict[str, Path],
    sums: dict[str, str],
    extra: set[str] | frozenset[str] = frozenset(),
) -> None:
    """Fail unless every tool is present for every platform, the *extra* files
    are present, and all match SHA512SUMS."""
    missing = sorted(expected_files(version) - set(files))
    if missing:
        raise SystemExit(
            f"LLVM {version}: {len(missing)} binaries missing, not publishing. "
            f"First missing: {', '.join(missing[:6])}"
        )
    missing = sorted(extra - set(files))
    if missing:
        raise SystemExit(
            f"LLVM {version}: {', '.join(missing)} missing, not publishing."
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
    dry_run: bool,
) -> None:
    """Create a draft, upload *files*, then publish it.

    The tag goes on the head of the default branch, not on the commit the
    binaries were built from (the notes name that commit). GitHub refuses
    (HTTP 403 "Resource not accessible by integration") to let GITHUB_TOKEN
    tag an older commit once a workflow file has changed since, because that
    needs the `workflows` permission, which GITHUB_TOKEN cannot have. Builds
    take hours, so Dependabot updates to the workflow often land meanwhile.
    """
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


def version_notes(version: str, source: str, how: str, *, built: bool = False) -> str:
    """Release notes. *built* releases also have the common_files()."""
    lines = [
        f"LLVM {version} binaries built from `{source}`.",
        "",
        how,
        "",
        "Platforms: " + ", ".join(PLATFORM_NAMES),
        "",
        "Tools: " + ", ".join(build.active_tools(version)),
        "",
    ]
    if built:
        lines += [
            (
                "clang-tidy, clang-query and clang-include-cleaner need clang's "
                "builtin headers (`stddef.h`, `stdarg.h` and others). Unpack "
                f"`{build.resource_headers_name(version)}` in the parent of the "
                "directory that holds the binaries: it creates "
                "`lib/clang/<version>/include`, where the tools look for them."
            ),
            "",
            (
                "Before this release was published, the binaries of every "
                "platform were installed that way and run on test code "
                "(`acceptance.py`)."
            ),
            "",
            (
                f"`{build.scripts_name(version)}` holds `run-clang-tidy`, "
                "`clang-tidy-diff.py`, `clang-format-diff.py` and "
                "`git-clang-format` from the same LLVM release. It unpacks to "
                "`bin/`, next to the headers' `lib/`. The scripts need Python 3."
            ),
            "",
            (
                f"`clang-tools-{version}_<platform>.tar.gz` holds everything for "
                "one platform in the layout of an LLVM installation: the tools "
                "under their plain names and the scripts in `bin/`, the headers "
                "in `lib/clang/`, and the license."
            ),
            "",
            (
                f"`{build.LICENSE_FILE}` is LLVM's license. `{BUILDINFO_FILE}` "
                "records how each platform was built: source and its SHA-256, "
                "patches, CMake options, compiler and runner image."
            ),
            "",
        ]
    lines += [
        "Verify downloads with `sha512sum -c SHA512SUMS --ignore-missing`.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_plan(args: argparse.Namespace) -> None:
    releases = build.RELEASES
    unpinned = [
        source
        for source in releases.values()
        if f"{source}.tar.xz" not in build.SOURCE_SHA256
    ]
    if unpinned:
        raise ValueError(
            f"Not in sources.sha256: {', '.join(unpinned)}. See CONTRIBUTING.md."
        )
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
        seed_from = seed_versions_json() or {}
        plan = make_plan(
            event=args.event,
            releases=releases,
            published=published_tags(),
            latest_sources=seed_from.get("llvm_versions"),
            rebuild=rebuild,
        )
        plan["latest"] = SEED_FROM

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
            dry_run=args.dry_run,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def collect_files(
    version: str, artifacts: Path, extra: set[str]
) -> tuple[dict[str, Path], dict[str, str]]:
    """The files of *version* in the build artifacts and their SHA-512.

    Fails unless every binary and every *extra* file is there and matches
    the SHA512SUMS files that the builds wrote.
    """
    sums: dict[str, str] = {}
    for sums_file in artifacts.rglob("SHA512SUMS"):
        sums.update(read_sums(sums_file))
    files = select_files(version, artifacts.rglob("*"), extra)
    for name in prepared_files(version) & set(files):
        # Written by `prepare` in the publish job, so no build has its sum.
        sums[name] = sha512(files[name])
    check_files(version, files, sums, extra)
    return files, sums


def merge_buildinfo(
    version: str,
    tag: str,
    fragments: list[Path],
    *,
    repo: str,
    commit: str,
    run_url: str,
) -> dict:
    """BUILDINFO.json: the build details of every platform in one file."""
    platforms: dict[str, dict] = {}
    for path in fragments:
        info = json.loads(path.read_text(encoding="utf-8"))
        if info.get("llvm_major") != version:
            raise SystemExit(f"{path.name} is not about LLVM {version}")
        platforms[info["platform"]] = info
    missing = [p for p in PLATFORM_NAMES if p not in platforms]
    if missing:
        raise SystemExit(
            f"LLVM {version}: no build details for {', '.join(missing)}, "
            "not publishing."
        )
    source = platforms[PLATFORM_NAMES[0]]["source"]
    if source["name"] != f"{build.RELEASES[version]}.tar.xz":
        raise SystemExit(f"LLVM {version}: built from {source['name']}")
    for name, info in platforms.items():
        if info["source"] != source:
            raise SystemExit(
                f"LLVM {version}: {name} was built from a different source "
                f"({info['source']} instead of {source})"
            )
    drop = {"platform", "llvm_major", "source"}
    return {
        "schema_version": BUILDINFO_SCHEMA_VERSION,
        "llvm_version": tag_source(tag)[len("llvm-project-") : -len(".src")],
        "tag": tag,
        "source": source,
        "build": {
            "repository": repo,
            "commit": commit,
            "workflow": ".github/workflows/build.yml",
            "run_url": run_url,
        },
        "tools": build.active_tools(version),
        "resource_headers": {
            "name": build.resource_headers_name(version),
            "built_on": build.COMMON_FILES_PLATFORM,
        },
        "scripts": build.scripts_name(version),
        "license": build.LICENSE_FILE,
        "platforms": {
            p: {k: v for k, v in platforms[p].items() if k not in drop}
            for p in PLATFORM_NAMES
        },
    }


def check_acceptance(version: str, accepted: Path) -> None:
    """Fail unless acceptance.py passed on every platform."""
    failed = []
    for platform_name in PLATFORM_NAMES:
        report = accepted / f"{version}_{platform_name}.json"
        if not report.is_file():
            failed.append(f"{platform_name} (no result)")
            continue
        result = json.loads(report.read_text(encoding="utf-8"))
        if result.get("passed") is not True:
            bad = [name for name, ok in result.get("checks", {}).items() if not ok]
            failed.append(f"{platform_name} ({', '.join(bad) or 'failed'})")
    if failed:
        raise SystemExit(
            f"LLVM {version}: acceptance tests failed on {'; '.join(failed)}, "
            "not publishing."
        )


def write_bundle(
    version: str, platform_name: str, files: dict[str, Path], archive: Path
) -> None:
    """Write everything of *version* for *platform_name* to *archive*.

    The archive unpacks to one directory, named like the archive, with the
    layout of an LLVM installation: ``bin/`` holds the tools under their plain
    names (``clang-tidy``, not ``clang-tidy-23_linux-amd64``) and the helper
    scripts, ``lib/clang/<version>/include`` holds clang's builtin headers,
    and ``LICENSE.TXT`` is LLVM's license. Owners and times are fixed, so the
    same files always give the same bytes.
    """
    root = archive.name[: -len(".tar.gz")]
    exe = ".exe" if platform_name.startswith("windows") else ""

    def add(tar: tarfile.TarFile, path: Path, name: str, mode: int) -> None:
        info = tarfile.TarInfo(f"{root}/{name}")
        info.size = path.stat().st_size
        info.mode = mode
        with open(path, "rb") as fh:
            tar.addfile(info, fh)

    with (
        open(archive, "wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tar,
    ):
        for tool in build.active_tools(version):
            add(
                tar,
                files[f"{tool}-{version}_{platform_name}{exe}"],
                f"bin/{tool}{exe}",
                0o755,
            )
        # Both archives already hold paths relative to the installation
        # directory (bin/..., lib/clang/...), with fixed owners and times.
        for name in (build.scripts_name(version), build.resource_headers_name(version)):
            with tarfile.open(files[name]) as inner:
                for member in inner:
                    member.name = f"{root}/{member.name}"
                    tar.addfile(
                        member, inner.extractfile(member) if member.isfile() else None
                    )
        add(tar, files[build.LICENSE_FILE], build.LICENSE_FILE, 0o644)


def cmd_prepare(args: argparse.Namespace) -> None:
    version, tag = args.version, args.tag
    artifacts = Path(args.artifacts)
    common = common_files(version)
    built, _ = collect_files(version, artifacts, common - prepared_files(version))
    info = merge_buildinfo(
        version,
        tag,
        sorted(artifacts.rglob("buildinfo-*.json")),
        repo=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO),
        commit=args.commit,
        run_url=args.run_url,
    )
    (artifacts / BUILDINFO_FILE).write_text(
        json.dumps(info, indent=2) + "\n", encoding="utf-8"
    )
    check_acceptance(version, Path(args.accepted))
    for platform_name in PLATFORM_NAMES:
        name = bundle_name(version, platform_name)
        write_bundle(version, platform_name, built, artifacts / name)
        print(f"Wrote {name}")
    files, _ = collect_files(version, artifacts, common)
    # The input of actions/attest-build-provenance: every file the release
    # will have, in `sha256sum` format.
    Path(args.subjects).write_text(
        "".join(f"{sha256(files[name])}  {name}\n" for name in sorted(files)),
        encoding="utf-8",
    )
    print(f"LLVM {version}: {len(files)} files ready to attest and publish.")


def cmd_version(args: argparse.Namespace) -> None:
    version, tag = args.version, args.tag
    source = build.RELEASES[version]
    files, sums = collect_files(version, Path(args.artifacts), common_files(version))
    out = Path(tempfile.mkdtemp()) / "SHA512SUMS"
    write_sums(out, {n: sums[n] for n in files})
    how = ""
    if args.commit or args.run_url:
        how = "Built"
        if args.commit:
            how += f" from commit {args.commit}"
        if args.run_url:
            how += f" by {args.run_url}"
    notes = version_notes(version, source, how, built=True)
    # build.yml attests every file but SHA512SUMS (written here) before it
    # runs this command.
    repo = os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO)
    notes += (
        "\nEvery file except SHA512SUMS has a build provenance attestation. "
        "Check which workflow run and commit built a file with "
        f"`gh attestation verify <file> --repo {repo}`.\n"
    )
    create_release(
        tag,
        tag,
        notes,
        [out, *sorted(files.values())],
        latest=False,
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


def choose_tags(releases: dict[str, str], published: set[str]) -> dict[str, str]:
    """Pick the release to copy for each LLVM major in *releases*.

    When the source in releases.json has no release yet (its build failed),
    keep shipping the newest earlier release of that major, so one failing
    version does not hold back the others. A major with no release at all
    is left out.
    """
    tags: dict[str, str] = {}
    for version in sort_versions(releases):
        tag = current_tag(releases[version], published)
        if tag is None:
            tag = fallback_tag(version, published)
            print(
                f"::warning::LLVM {version} ({releases[version]}) has no published "
                f"release; {'keeping ' + tag if tag else 'leaving it out'}."
            )
        if tag:
            tags[version] = tag
    return tags


def release_assets(tag: str) -> dict[str, dict]:
    """Name -> {url, size} for every file of release *tag*."""
    out = gh(
        "api",
        f"repos/{{owner}}/{{repo}}/releases/tags/{tag}",
        "--jq",
        "[.assets[] | {name, size, url: .browser_download_url}]",
    )
    return {a["name"]: a for a in json.loads(out)}


def release_sums(tag: str) -> dict[str, str]:
    with tempfile.TemporaryDirectory() as tmp:
        gh("release", "download", tag, "-p", "SHA512SUMS", "-D", tmp)
        return read_sums(Path(tmp) / "SHA512SUMS")


def older_releases(
    version: str, tag: str, published: set[str] | frozenset[str], repo: str
) -> list[dict[str, str]]:
    """Releases of earlier LLVM patch versions of major *version*, newest first.

    ``index.json`` lists the files of one release per major. A project that
    pins a full version, such as 21.1.0, finds its release here: the files
    have the same names as in the current release, and SHA512SUMS has their
    checksums. Each patch version appears once, with its newest build.
    """
    current = tag_source(tag)
    newest: dict[tuple[int, ...], tuple[int, str]] = {}
    for other in published:
        match = re.fullmatch(
            rf"llvm-({re.escape(version)}(?:\.\d+)+)(?:-(\d+))?", other
        )
        if not match or tag_source(other) == current:
            continue
        key = tuple(int(x) for x in match.group(1).split("."))
        revision = int(match.group(2) or 1)
        if key not in newest or revision > newest[key][0]:
            newest[key] = (revision, other)
    return [
        {
            "version": ".".join(str(x) for x in key),
            "tag": other,
            "release_url": f"https://github.com/{repo}/releases/tag/{other}",
            "sha512sums_url": (
                f"https://github.com/{repo}/releases/download/{other}/SHA512SUMS"
            ),
        }
        for key, (_, other) in sorted(newest.items(), reverse=True)
    ]


def asset_entry(
    assets: dict[str, dict], sums: dict[str, str], name: str
) -> dict | None:
    """The index entry of file *name* of a release, or None if it has none."""
    asset = assets.get(name)
    if asset is None or name not in sums:
        return None
    return {
        "name": name,
        "url": asset["url"],
        "size": asset["size"],
        "sha512": sums[name],
    }


def build_index(
    tags: dict[str, str],
    assets: dict[str, dict[str, dict]],
    sums: dict[str, dict[str, str]],
    repo: str,
    published: set[str] | frozenset[str] = frozenset(),
) -> dict:
    """The index downstream tools read: every file of every supported version."""
    versions = {}
    for version, tag in tags.items():
        files: dict[str, dict[str, dict]] = {}
        for tool in build.active_tools(version):
            for platform_name in PLATFORM_NAMES:
                exe = ".exe" if platform_name.startswith("windows") else ""
                name = f"{tool}-{version}_{platform_name}{exe}"
                asset = assets[tag].get(name)
                if asset is None or name not in sums[tag]:
                    raise SystemExit(f"{tag} has no {name} or no checksum for it")
                files.setdefault(tool, {})[platform_name] = {
                    "name": name,
                    "url": asset["url"],
                    "size": asset["size"],
                    "sha512": sums[tag][name],
                }
        source = tag_source(tag)
        versions[version] = {
            "version": source[len("llvm-project-") : -len(".src")],
            "source": source,
            "tag": tag,
            "release_url": f"https://github.com/{repo}/releases/tag/{tag}",
            "sha512sums_url": assets[tag]["SHA512SUMS"]["url"],
            "tools": files,
        }
        # Only in releases built since these files were added.
        for key, name in (
            ("resource_headers", build.resource_headers_name(version)),
            ("scripts", build.scripts_name(version)),
            ("license", build.LICENSE_FILE),
            ("buildinfo", BUILDINFO_FILE),
        ):
            if (found := asset_entry(assets[tag], sums[tag], name)) is not None:
                versions[version][key] = found
        bundles = {
            platform_name: asset_entry(
                assets[tag], sums[tag], bundle_name(version, platform_name)
            )
            for platform_name in PLATFORM_NAMES
        }
        if all(bundles.values()):
            versions[version]["bundles"] = bundles
        older = older_releases(version, tag, published, repo)
        if older:
            versions[version]["older_releases"] = older
    tools_info: dict[str, dict] = {}
    for tool in build.TOOLS:
        info: dict[str, int] = {}
        if tool == "clang-include-cleaner":
            info["min_llvm_version"] = build.INCLUDE_CLEANER_MIN_VERSION
        if tool == "clang-scan-deps":
            info["min_llvm_version"] = build.CLANG_SCAN_DEPS_MIN_VERSION
        tools_info[tool] = info
    latest = max(versions, key=int) if versions else None
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "repository": repo,
        "latest": latest,
        # The full version of the newest LLVM major, e.g. "23.1.3", for the
        # README badge and for tools that only want the newest version.
        "latest_version": versions[latest]["version"] if latest else None,
        "platforms": PLATFORM_NAMES,
        "tools": tools_info,
        "versions": {v: versions[v] for v in sort_versions(versions)},
    }


def cmd_index(args: argparse.Namespace) -> None:
    repo = os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO)
    published = published_tags()
    tags = choose_tags(build.RELEASES, published)
    if not tags:
        raise SystemExit("No version has a published release.")
    assets = {tag: release_assets(tag) for tag in tags.values()}
    sums = {tag: release_sums(tag) for tag in tags.values()}
    index = build_index(tags, assets, sums, repo, published)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )
    others = download_page.other_sources(list(index["versions"]))
    for source, versions in others.items():
        found = ", ".join(sorted(versions, key=int)) if versions else "not read"
        print(f"{source}: {found}")
    (out / "index.html").write_text(
        download_page.render_html(index, others), encoding="utf-8"
    )
    for version, tag in tags.items():
        print(f"LLVM {version}: {tag}")
    print(f"Wrote {out / 'index.json'} and {out / 'index.html'}")


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

    p = sub.add_parser("seed", help="Copy a version out of SEED_FROM.")
    p.add_argument("--version", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--from-tag", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser(
        "prepare",
        help="Check one version's artifacts and acceptance results, write "
        "BUILDINFO.json and list the files to attest.",
    )
    p.add_argument("--version", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--artifacts", required=True)
    p.add_argument(
        "--accepted",
        required=True,
        help="Directory with the <version>_<platform>.json files of acceptance.py.",
    )
    p.add_argument("--commit", required=True)
    p.add_argument("--run-url", default="")
    p.add_argument(
        "--subjects", required=True, help="Write the SHA-256 of every file here."
    )
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("version", help="Publish one version from build artifacts.")
    p.add_argument("--version", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--artifacts", required=True)
    p.add_argument(
        "--commit", help="Commit the binaries were built from, named in the notes."
    )
    p.add_argument("--run-url", default="")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_version)

    p = sub.add_parser("index", help="Write index.json and index.html.")
    p.add_argument("--output", "-o", default="site")
    p.set_defaults(func=cmd_index)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
