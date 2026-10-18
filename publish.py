#!/usr/bin/env python3
"""Plan builds and publish releases for clang-tools-static-binaries.

Each LLVM version gets its own immutable GitHub release, tagged after its
source release (for example ``llvm-22.1.8``). A version is built once; later
workflow runs only build versions that do not have a release yet. A rebuild
of the same source gets a revision suffix (``llvm-22.1.8-2``), like a Debian
package revision.

There is no release that holds every version. Instead, ``index.json`` (and
a matching ``index.html``) lists every file of every supported version with
its URL, size and SHA-512, and is published on GitHub Pages. Downstream
tools read the index, the way uv reads its Python download metadata.

Subcommands (all are called from .github/workflows/build.yml):

* ``plan``    - decide which versions to copy out of the last combined
                release (SEED_FROM, once) and which to build.
* ``seed``    - copy one version's files out of SEED_FROM into a new
                version release.
* ``version`` - publish one version release from the build artifacts.
* ``index``   - write index.json and index.html from the version releases.

``gh`` (GitHub CLI) must be installed and ``GH_TOKEN``/``GH_REPO`` set for the
subcommands that talk to GitHub.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build

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


def build_index(
    tags: dict[str, str],
    assets: dict[str, dict[str, dict]],
    sums: dict[str, dict[str, str]],
    repo: str,
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
    tools_info: dict[str, dict] = {}
    for tool in build.TOOLS:
        info: dict[str, int] = {}
        if tool == "clang-include-cleaner":
            info["min_llvm_version"] = build.INCLUDE_CLEANER_MIN_VERSION
        if tool == "clang-scan-deps":
            info["min_llvm_version"] = build.CLANG_SCAN_DEPS_MIN_VERSION
        tools_info[tool] = info
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "repository": repo,
        "latest": max(versions, key=int) if versions else None,
        "platforms": PLATFORM_NAMES,
        "tools": tools_info,
        "versions": {v: versions[v] for v in sort_versions(versions)},
    }


def render_html(index: dict) -> str:
    """A plain download page with the same content as index.json."""
    esc = html.escape
    rows = []
    for major, info in index["versions"].items():
        links = []
        for tool, by_platform in info["tools"].items():
            cells = " ".join(
                f'<a href="{esc(f["url"])}">{esc(p)}</a>'
                for p, f in by_platform.items()
            )
            links.append(f"<tr><td>{esc(tool)}</td><td>{cells}</td></tr>")
        rows.append(
            f'<h2 id="llvm-{esc(major)}">LLVM {esc(info["version"])}</h2>'
            f'<p><a href="{esc(info["release_url"])}">{esc(info["tag"])}</a> · '
            f'<a href="{esc(info["sha512sums_url"])}">SHA512SUMS</a></p>'
            f"<table>{''.join(links)}</table>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>clang-tools static binaries</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem;line-height:1.5}}
td{{padding:.2rem .6rem;vertical-align:top}} a{{margin-right:.5rem}}
</style></head><body>
<h1>clang-tools static binaries</h1>
<p>Static builds of clang-format, clang-tidy and other LLVM tools from
<a href="https://github.com/{esc(index["repository"])}">{esc(index["repository"])}</a>.
Machine-readable list: <a href="index.json">index.json</a>.
Generated {esc(index["generated_at"])}.</p>
{"".join(rows)}
</body></html>
"""


def cmd_index(args: argparse.Namespace) -> None:
    repo = os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO)
    published = published_tags()
    tags = choose_tags(build.RELEASES, published)
    if not tags:
        raise SystemExit("No version has a published release.")
    assets = {tag: release_assets(tag) for tag in tags.values()}
    sums = {tag: release_sums(tag) for tag in tags.values()}
    index = build_index(tags, assets, sums, repo)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )
    (out / "index.html").write_text(render_html(index), encoding="utf-8")
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

    p = sub.add_parser("version", help="Publish one version from build artifacts.")
    p.add_argument("--version", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--artifacts", required=True)
    p.add_argument("--target", help="Commit to tag.")
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
