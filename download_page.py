"""Render index.html, the download page of clang-tools-static-binaries.

publish.py writes index.json; this module turns it into a page that lists,
for every supported LLVM version, the files here and the versions that other
common sources of clang-format and clang-tidy have (LLVM releases, PyPI,
conda-forge, Homebrew), with install commands for each source.

The other sources are read from their public APIs when the page is generated.
A source that cannot be read is left off the page; index.json never depends
on them.
"""

from __future__ import annotations

import html
import http.client
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

USER_AGENT = (
    "clang-tools-static-binaries"
    " (+https://github.com/cpp-linter/clang-tools-static-binaries)"
)
# The page's styles, inlined so that index.html is one file.
PAGE_CSS = Path(__file__).with_name("download_page.css").read_text(encoding="utf-8")


def fetch_json(url: str, *, missing_ok: bool = False) -> dict | None:
    """GET *url* as JSON. Return None if that fails, or {} on a 404 if *missing_ok*."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        if missing_ok and e.code == 404:
            return {}
        print(f"::warning::Could not read {url}: {e}")
    except (OSError, ValueError, http.client.HTTPException) as e:
        # HTTPException (e.g. IncompleteRead) is not an OSError.
        print(f"::warning::Could not read {url}: {e}")
    return None


def newest_by_major(versions) -> dict[str, str]:
    """Map each major version to its newest final release in *versions*.

    Pre-releases such as ``22.1.0rc1`` are skipped. Wheel rebuilds such as
    ``22.1.0.1`` count as releases.
    """
    newest: dict[str, tuple[int, ...]] = {}
    for version in versions:
        if not isinstance(version, str):
            continue
        parts = version.split(".")
        if not all(p.isdigit() for p in parts):
            continue
        key = tuple(int(p) for p in parts)
        major = parts[0]
        if major not in newest or key > newest[major]:
            newest[major] = key
    return {m: ".".join(map(str, k)) for m, k in newest.items()}


def pypi_versions(package: str) -> dict[str, str] | None:
    data = fetch_json(f"https://pypi.org/pypi/{package}/json")
    if data is None:
        return None
    releases = data.get("releases", {})
    return newest_by_major(
        v for v, files in releases.items() if any(not f.get("yanked") for f in files)
    )


def conda_forge_versions(package: str) -> dict[str, str] | None:
    data = fetch_json(f"https://api.anaconda.org/package/conda-forge/{package}")
    if data is None:
        return None
    return newest_by_major(data.get("versions", []))


def llvm_versions() -> dict[str, str] | None:
    """Newest final release of each LLVM major on GitHub."""
    try:
        out = subprocess.run(
            [
                "gh",
                "api",
                "repos/llvm/llvm-project/releases?per_page=100",
                "--paginate",
                "--jq",
                ".[] | select((.draft or .prerelease) | not) | .tag_name",
            ],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"::warning::Could not list LLVM releases: {e}")
        return None
    return newest_by_major(
        t.removeprefix("llvmorg-") for t in out.split() if t.startswith("llvmorg-")
    )


def homebrew_versions(majors: list[str]) -> dict[str, str] | None:
    """Map LLVM major -> Homebrew formula that installs it, e.g. ``llvm@21``."""
    latest = fetch_json("https://formulae.brew.sh/api/formula/llvm.json")
    if latest is None:
        return None
    formulae = {latest["versions"]["stable"].split(".")[0]: "llvm"}
    for major in majors:
        if major in formulae:
            continue
        url = f"https://formulae.brew.sh/api/formula/llvm@{major}.json"
        data = fetch_json(url, missing_ok=True)
        if data is None:
            return None
        if data and not data.get("disabled"):
            formulae[major] = f"llvm@{major}"
    return formulae


def other_sources(majors: list[str]) -> dict[str, dict[str, str] | None]:
    """Versions that other sources have, by source; None if it could not be read.

    A source that cannot be read, or answers in an unexpected shape, is left
    off the page with a warning.
    """
    readers = {
        "llvm": llvm_versions,
        "pypi-clang-format": lambda: pypi_versions("clang-format"),
        "pypi-clang-tidy": lambda: pypi_versions("clang-tidy"),
        "conda-clang-format": lambda: conda_forge_versions("clang-format"),
        "conda-clang-tools": lambda: conda_forge_versions("clang-tools"),
        "homebrew": lambda: homebrew_versions(majors),
    }
    found = {}
    for key, read in readers.items():
        try:
            # No version at all means the API answered something unexpected.
            found[key] = read() or None
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            # An API that answers in a new shape must not break the index job.
            print(f"::warning::Could not read the versions of {key}: {e!r}")
            found[key] = None
    return found


def _size(num_bytes: int) -> str:
    return f"{num_bytes / 1_000_000:.1f} MB"


def _newest_major(versions: dict[str, str] | None, at_most: str) -> str | None:
    """The newest major in *versions* not above *at_most*, or None if unknown."""
    majors = [int(m) for m in versions or {} if int(m) <= int(at_most)]
    return str(max(majors)) if majors else None


def render_html(
    index: dict, others: dict[str, dict[str, str] | None] | None = None
) -> str:
    """The download page: versions here and elsewhere, install commands, files."""
    esc = html.escape
    others = others or {}
    repo = index["repository"]
    majors = list(index["versions"])
    latest = index["latest"]
    first = index["versions"][latest]["tools"]["clang-format"]["linux-amd64"]

    def cell(versions: dict[str, str] | None, major: str, href=None) -> str:
        version = (versions or {}).get(major)
        if version is None:
            return "<td>—</td>"
        text = esc(version)
        if href:
            text = f'<a href="{esc(href(version))}">{text}</a>'
        return f"<td>{text}</td>"

    # Versions table: one row per LLVM major, one column per source.
    columns = [("Here: static binaries", None)]
    llvm = others.get("llvm")
    if llvm is not None:
        columns.append(("LLVM releases", "llvm"))
    for key, title in [
        ("pypi-clang-format", "PyPI clang-format"),
        ("pypi-clang-tidy", "PyPI clang-tidy"),
        ("conda-clang-format", "conda-forge clang-format"),
        ("conda-clang-tools", "conda-forge clang-tools"),
        ("homebrew", "Homebrew"),
    ]:
        if others.get(key) is not None:
            columns.append((title, key))
    head = "".join(f"<th>{esc(title)}</th>" for title, _ in columns)
    rows = []
    for major, info in index["versions"].items():
        cells = [f'<td><a href="#llvm-{esc(major)}">{esc(info["version"])}</a></td>']
        for _, key in columns[1:]:
            if key == "llvm":
                cells.append(
                    cell(
                        llvm,
                        major,
                        lambda v: (
                            "https://github.com/llvm/llvm-project/releases/tag/llvmorg-"
                            + v
                        ),
                    )
                )
            elif key == "homebrew":
                formula = others["homebrew"].get(major)
                cells.append(f"<td>{esc(formula)}</td>" if formula else "<td>—</td>")
            else:
                cells.append(cell(others[key], major))
        rows.append(f"<tr><th>{esc(major)}</th>{''.join(cells)}</tr>")
    unread = [
        name
        for key, name in [
            ("llvm", "LLVM releases"),
            ("pypi-clang-format", "PyPI clang-format"),
            ("pypi-clang-tidy", "PyPI clang-tidy"),
            ("conda-clang-format", "conda-forge clang-format"),
            ("conda-clang-tools", "conda-forge clang-tools"),
            ("homebrew", "Homebrew"),
        ]
        if others.get(key) is None
    ]
    unread_note = (
        f" Could not be read this time: {esc(', '.join(unread))}." if unread else ""
    )
    versions_table = (
        f'<div class="scroll"><table><thead><tr><th>LLVM</th>{head}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
        '<p class="muted">The newest release of each LLVM major version. Other sources '
        "are run by their own projects; their versions were read from their public "
        f"APIs when this page was generated.{unread_note}</p>"
    )

    # Install commands, with the newest version each source has. Without a
    # version from the source, the command installs whatever is newest there.
    def pinned(package: str, key: str, spec: str) -> str:
        major = _newest_major(others.get(key), latest)
        return f'"{package}{spec.format(major)}"' if major else package

    brew = others.get("homebrew") or {}
    brew_major = _newest_major(brew, latest)
    brew_formula = brew[brew_major] if brew_major else "llvm"
    # (source, maintained by, tools, platforms, install command or a note)
    sources = [
        (
            '<a href="#files">Download from this page</a>',
            "cpp-linter",
            "All tools listed under Files",
            "Linux, macOS, Windows; amd64, arm64",
            None,
            "Pick a file under Files, or use one of the next four tools.",
        ),
        (
            '<a href="https://github.com/cpp-linter/clang-tools-pip">clang-tools</a> (pip)',
            "cpp-linter",
            "These binaries",
            "Linux, macOS, Windows; amd64, arm64",
            (
                "pip install clang-tools\n"
                f"clang-tools install clang-format clang-tidy --version {latest}"
            ),
            None,
        ),
        (
            '<a href="https://github.com/cpp-linter/asdf-clang-tools">asdf plugin</a>',
            "cpp-linter",
            "These binaries",
            "Linux, macOS",
            (
                "asdf plugin add clang-format https://github.com/cpp-linter/asdf-clang-tools.git\n"
                f"asdf install clang-format {latest}"
            ),
            None,
        ),
        (
            '<a href="https://github.com/cpp-linter/homebrew-tap">Homebrew tap</a>',
            "cpp-linter",
            "These binaries",
            "macOS",
            "brew install cpp-linter/tap/clang-format",
            None,
        ),
        (
            '<a href="https://github.com/cpp-linter/clang-tools-npm">npm packages</a>',
            "cpp-linter",
            "These clang-format and clang-tidy binaries",
            "Linux, macOS, Windows; amd64, arm64",
            (
                "npm install --save-dev "
                f"@cpp-linter/clang-format@{latest} @cpp-linter/clang-tidy@{latest}"
            ),
            None,
        ),
        (
            '<a href="https://pypi.org/project/clang-format/">PyPI wheels</a>',
            "ssciwr",
            "clang-format, clang-tidy",
            "Linux, macOS, Windows",
            (
                "pip install "
                + pinned("clang-format", "pypi-clang-format", "=={}.*")
                + " "
                + pinned("clang-tidy", "pypi-clang-tidy", "=={}.*")
            ),
            None,
        ),
        (
            '<a href="https://github.com/llvm/llvm-project/releases">LLVM releases</a>',
            "LLVM",
            "Full LLVM toolchain archives",
            "Depends on the release",
            None,
            "Download the archive for your platform from the release page.",
        ),
        (
            '<a href="https://apt.llvm.org/">apt.llvm.org</a>',
            "LLVM",
            "clang-format-N, clang-tidy-N packages",
            "Debian, Ubuntu",
            (
                f"wget https://apt.llvm.org/llvm.sh && sudo bash llvm.sh {latest}\n"
                f"sudo apt-get install clang-format-{latest} clang-tidy-{latest}"
            ),
            None,
        ),
        (
            '<a href="https://anaconda.org/conda-forge/clang-format">conda-forge</a>',
            "conda-forge",
            "clang-format; clang-tidy is in clang-tools",
            "Linux, macOS, Windows",
            (
                "conda install -c conda-forge "
                + pinned("clang-format", "conda-clang-format", "={}")
                + " "
                + pinned("clang-tools", "conda-clang-tools", "={}")
            ),
            None,
        ),
        (
            f'<a href="https://formulae.brew.sh/formula/{esc(brew_formula)}">Homebrew</a>',
            "Homebrew",
            "Full LLVM toolchain",
            "macOS, Linux",
            (
                f"brew install {brew_formula}\n"
                f'"$(brew --prefix {brew_formula})/bin/clang-format" --version'
            ),
            None,
        ),
    ]
    source_rows = "".join(
        f"<tr><td>{name}</td><td>{esc(who)}</td><td>{esc(what)}</td>"
        f"<td>{esc(where)}</td>"
        + (
            f'<td class="cmd"><code>{esc(cmd)}</code></td>'
            if cmd
            else f"<td>{esc(note)}</td>"
        )
        + "</tr>"
        for name, who, what, where, cmd, note in sources
    )
    install_table = (
        '<div class="scroll"><table class="install"><thead><tr><th>Source</th>'
        "<th>Maintained by</th><th>Tools</th><th>Platforms</th><th>Install</th></tr>"
        f"</thead><tbody>{source_rows}</tbody></table></div>"
        '<p class="muted">The tools from cpp-linter all install the binaries listed on '
        "this page. Swap the version number for the one you need.</p>"
    )

    # Files: one table per version, tools by platform.
    def file_cell(file: dict | None) -> str:
        if file is None:
            return "<td>—</td>"
        return (
            f'<td class="num"><a href="{esc(file["url"])}" title="{esc(file["name"])}">'
            f"{_size(file['size'])}</a></td>"
        )

    sections = []
    platform_head = "".join(f"<th>{esc(p)}</th>" for p in index["platforms"])
    for major, info in index["versions"].items():
        tool_rows = []
        for tool, by_platform in info["tools"].items():
            cells = "".join(file_cell(by_platform.get(p)) for p in index["platforms"])
            tool_rows.append(f"<tr><th>{esc(tool)}</th>{cells}</tr>")
        # Releases built since these files were added also have clang's
        # builtin headers, LLVM's license and the build details.
        extras = "".join(
            f' · <a href="{esc(info[key]["url"])}">{label}</a>'
            for key, label in (
                ("resource_headers", "clang headers"),
                ("license", "LICENSE.TXT"),
                ("buildinfo", "BUILDINFO.json"),
            )
            if key in info
        )
        sections.append(
            f'<h3 id="llvm-{esc(major)}">LLVM {esc(info["version"])}</h3>'
            f'<p class="muted">Release <a href="{esc(info["release_url"])}">'
            f"{esc(info['tag'])}</a> · "
            f'<a href="{esc(info["sha512sums_url"])}">SHA512SUMS</a>{extras}</p>'
            f'<div class="scroll"><table><thead><tr><th>Tool</th>{platform_head}</tr>'
            f"</thead><tbody>{''.join(tool_rows)}</tbody></table></div>"
        )

    headers_note = ""
    if any("resource_headers" in info for info in index["versions"].values()):
        headers_note = (
            "<p>clang-tidy, clang-query and clang-include-cleaner also need "
            "clang's builtin headers (<code>stddef.h</code> and others). Unpack "
            "the release's <em>clang headers</em> archive in the parent of the "
            "folder that holds the binary: it creates "
            "<code>lib/clang/&lt;version&gt;/include</code>, where they look "
            "for the headers.</p>\n"
        )

    oldest = min(majors, key=int)
    nav = "".join(f'<a href="#llvm-{esc(v)}">{esc(v)}</a>' for v in majors)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>clang-tools static binaries - cpp-linter</title>
<meta name="description" content="Static clang-format, clang-tidy and other LLVM tools for LLVM {esc(oldest)} to {esc(latest)} on Linux, macOS and Windows.">
<link rel="icon" href="https://cpp-linter.github.io/static/favicon.ico">
<link rel="stylesheet" href="https://cpp-linter.github.io/stylesheets/shared.css">
<style>{PAGE_CSS}</style></head><body>
<header class="top"><div>
<a class="brand" href="https://cpp-linter.github.io/"><img src="https://cpp-linter.github.io/static/logo.png" alt="" width="30" height="30">cpp-linter</a>
<nav><a href="https://cpp-linter.github.io/getting-started/">Get started</a>
<a href="https://cpp-linter.github.io/blog/">Blog</a>
<a href="https://github.com/{esc(repo)}">GitHub</a></nav>
</div></header>
<main>
<h1>clang-tools static binaries</h1>
<p>Static builds of clang-format, clang-tidy and other LLVM tools, for LLVM {esc(oldest)}
to {esc(latest)} on Linux, macOS and Windows, on x86-64 and ARM64. Each file is a
single executable: download it and run it. Built by
<a href="https://github.com/{esc(repo)}">{esc(repo)}</a>; tools that download
these files should read <a href="index.json">index.json</a>.</p>
<nav class="muted"><span>Jump to LLVM</span>{nav}</nav>

<h2 id="versions">Versions</h2>
{versions_table}

<h2 id="install">Install</h2>
{install_table}

<h2 id="verify">Verify a download</h2>
<p>Every release has a <code>SHA512SUMS</code> file. Releases built since build
provenance attestations were added also let you check which workflow run and
commit built a file (see
<a href="https://github.com/{esc(repo)}/blob/master/SECURITY.md">SECURITY.md</a>):</p>
<pre><code>curl -fLO {esc(first["url"])}
curl -fLO {esc(index["versions"][latest]["sha512sums_url"])}
sha512sum -c SHA512SUMS --ignore-missing
gh attestation verify {esc(first["name"])} --repo {esc(repo)}</code></pre>

<h2 id="files">Files</h2>
<p class="muted">Sizes link to the files. Windows files end in <code>.exe</code>.</p>
{headers_note}{"".join(sections)}

</main>
<footer class="foot"><div>Part of <a href="https://cpp-linter.github.io/">cpp-linter</a>.
Generated {esc(index["generated_at"])}.</div></footer>
</body></html>
"""
