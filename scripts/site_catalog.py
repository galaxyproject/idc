#!/usr/bin/env python3
"""Render the IDC site's catalog page from the files in this repository.

The catalog lists what has been requested (every ``data-managers/<table>/<version>.yaml``),
the data tables the IDC serves (``config/tool_data_table_conf.xml``), the data
managers a request can use (``schemas/data_managers.yml``) and the genomes in
``genomes.yml`` from the earlier Jenkins pipeline. It is generated on every site
build, so it cannot drift from those files.

It is a *request* catalog: whether a version has been published is only known
to the data tables of the Galaxy servers, so each table links to its entry in
test.galaxyproject.org's public data table API instead.

MkDocs runs this as a hook (``hooks:`` in ``mkdocs.yml``) and substitutes the
rendered markdown for ``CATALOG_MARKER`` in ``docs/catalog.md``. Standalone::

    python scripts/site_catalog.py        # print the generated markdown
"""
import html
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from request_models import (  # noqa: E402
    REPO_ROOT,
    Request,
    data_manager_name,
    iter_request_files,
    version_id,
)
from tool_schemas import GUID_RE  # noqa: E402

CATALOG_PAGE = "catalog.md"
CATALOG_MARKER = "<!-- catalog -->"
REPO_URL = "https://github.com/galaxyproject/idc"
GALAXY_URL = "https://test.galaxyproject.org"
TABLE_CONF = REPO_ROOT / "config" / "tool_data_table_conf.xml"
INSTALLED_LIST = REPO_ROOT / "schemas" / "data_managers.yml"
GENOMES = REPO_ROOT / "genomes.yml"


def _cell(value) -> str:
    """A markdown table cell: one line, pipes escaped, empty for None."""
    if value is None:
        return ""
    return " ".join(str(value).split()).replace("|", "\\|")


def _text(value) -> str:
    """A free-text cell (outside code spans): also HTML-escaped, so a request's
    description renders as the text it is."""
    return html.escape(_cell(value), quote=False)


def _tool(tool_id: str) -> str:
    """owner/repo and tool version; the full GUID is in the linked request."""
    m = GUID_RE.match(tool_id)
    if not m:
        return f"`{_cell(tool_id)}`"
    return f"{m['owner']}/{m['repo']} {m['version']}"


def _requests() -> dict[str, list[tuple[str, Request, Path]]]:
    """data table -> [(version, request, path)], sorted by table then version."""
    by_table: dict[str, list[tuple[str, Request, Path]]] = {}
    for path in iter_request_files():
        try:
            request = Request(**yaml.safe_load(path.read_text()))
        except Exception as exc:
            raise ValueError(f"{path.relative_to(REPO_ROOT)}: not a valid request ({exc})") from exc
        by_table.setdefault(data_manager_name(path), []).append((version_id(path), request, path))
    return {table: sorted(rows, key=lambda r: r[0]) for table, rows in sorted(by_table.items())}


def render_requests() -> list[str]:
    requests = _requests()
    lines = [
        "## Versioned reference data",
        "",
        f"{sum(len(r) for r in requests.values())} request(s) in {len(requests)} data table(s). "
        "Each version links to its request file under "
        f"[`data-managers/`]({REPO_URL}/tree/main/data-managers); see "
        "[Requesting reference data](requesting-reference-data.md) to add one.",
        "",
    ]
    if not requests:
        lines += ["No requests yet.", ""]
    for table, rows in requests.items():
        lines += [
            f"### {table}",
            "",
            f"Served entries on test.galaxyproject.org: "
            f"[`/api/tool_data/{table}`]({GALAXY_URL}/api/tool_data/{table})",
            "",
            "| version | description | data manager | built from |",
            "|---|---|---|---|",
        ]
        for version, request, path in rows:
            built_from = ", ".join(
                f"[{up_table}](#{up_table}) {_cell(up_version)}"
                for up_table, up_version in (request.depends_on or {}).items()
            )
            description = _text(request.description)
            if request.doi:
                doi = request.doi.strip().removeprefix("https://doi.org/").removeprefix("doi:")
                description += f" ([doi:{_text(doi)}](https://doi.org/{doi}))"
            rel = path.relative_to(REPO_ROOT).as_posix()
            lines.append(
                f"| [`{_cell(version)}`]({REPO_URL}/blob/main/{rel}) | {description} "
                f"| {_tool(request.tool_id)} | {built_from} |"
            )
        lines.append("")
    return lines


def render_tables() -> list[str]:
    root = ET.parse(TABLE_CONF).getroot()
    tables = root.findall("table")
    lines = [
        "## Data tables",
        "",
        f"The {len(tables)} data tables defined in "
        f"[`config/tool_data_table_conf.xml`]({REPO_URL}/blob/main/config/tool_data_table_conf.xml), "
        "which Galaxy servers load from "
        "`/cvmfs/idc.galaxyproject.org/config/tool_data_table_conf.xml` "
        "(see [Using IDC data in Galaxy](using-idc-data.md)). "
        "The `.loc` files are in `/cvmfs/idc.galaxyproject.org/config/`.",
        "",
        "| table | columns | `.loc` file |",
        "|---|---|---|",
    ]
    for table in sorted(tables, key=lambda t: t.get("name", "")):
        columns = _cell((table.findtext("columns") or "").replace(" ", ""))
        loc = table.find("file")
        loc_path = loc.get("path", "") if loc is not None else ""
        loc_path = loc_path.removeprefix("/cvmfs/idc.galaxyproject.org/config/")
        lines.append(f"| `{_cell(table.get('name'))}` | {columns.replace(',', ', ')} | `{_cell(loc_path)}` |")
    lines.append("")
    return lines


def render_installed() -> list[str]:
    installed = yaml.safe_load(INSTALLED_LIST.read_text()) or {}
    requested_repos = {
        (m["owner"], m["repo"])
        for rows in _requests().values()
        for _, request, _ in rows
        if (m := GUID_RE.match(request.tool_id))
    }
    repos = installed.get("repositories", [])
    lines = [
        "## Data managers available for requests",
        "",
        f"The {len(repos)} data manager repositories installed on test.galaxyproject.org, "
        "the build Galaxy (from "
        f"[`schemas/data_managers.yml`]({REPO_URL}/blob/main/schemas/data_managers.yml), "
        "which is resolved from usegalaxy-tools' "
        "[`data_managers.yml`](https://github.com/galaxyproject/usegalaxy-tools/blob/master/test.galaxyproject.org/data_managers.yml)). "
        "A request can use any of these tools; anything else has to be installed first.",
        "",
        "| repository | tool id(s) at the installed revision | requested |",
        "|---|---|---|",
    ]
    for repo in sorted(repos, key=lambda r: (r["owner"], r["name"])):
        tools = "<br>".join(
            f"`{_cell(m['tool'])}` {_cell(m['version'])}" if (m := GUID_RE.match(guid)) else f"`{_cell(guid)}`"
            for guid in repo.get("tool_ids", [])
        )
        requested = "yes" if (repo["owner"], repo["name"]) in requested_repos else ""
        url = f"https://toolshed.g2.bx.psu.edu/view/{repo['owner']}/{repo['name']}"
        lines.append(f"| [{_cell(repo['owner'])}/{_cell(repo['name'])}]({url}) | {tools} | {requested} |")
    lines.append("")
    return lines


def render_genomes() -> list[str]:
    genomes = (yaml.safe_load(GENOMES.read_text()) or {}).get("genomes", [])
    lines = [
        "## Genomes in genomes.yml",
        "",
        f"The {len(genomes)} genome(s) in [`genomes.yml`]({REPO_URL}/blob/main/genomes.yml), "
        "built with the indexers in "
        f"[`data_managers.yml`]({REPO_URL}/blob/main/data_managers.yml) by the earlier "
        "Jenkins pipeline. New genomes are requested like other data; see "
        "[Genomes](genome-indexing.md).",
        "",
        "| dbkey | description | source | indexers |",
        "|---|---|---|---|",
    ]
    for genome in genomes:
        indexers = ", ".join(i.removeprefix("data_manager_") for i in genome.get("indexers") or [])
        # genomes.yml leaves UCSC descriptions empty: the fetch data manager fills them in.
        description = _text(genome.get("description")) or ("(from UCSC)" if genome.get("source") == "ucsc" else "")
        lines.append(
            f"| `{_cell(genome.get('dbkey'))}` | {description} "
            f"| {_text(genome.get('source'))} | {_cell(indexers)} |"
        )
    lines.append("")
    return lines


def render_catalog() -> str:
    return "\n".join(render_requests() + render_tables() + render_installed() + render_genomes())


def on_page_markdown(markdown: str, page, config, files) -> str:
    """MkDocs hook: fill in the catalog page."""
    if page.file.src_uri != CATALOG_PAGE:
        return markdown
    if CATALOG_MARKER not in markdown:
        raise ValueError(f"docs/{CATALOG_PAGE} lost its {CATALOG_MARKER!r} marker")
    return markdown.replace(CATALOG_MARKER, render_catalog())


if __name__ == "__main__":
    print(render_catalog())
