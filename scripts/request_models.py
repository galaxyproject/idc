#!/usr/bin/env python3
"""Schema + linter for IDC reference-data *request* files.

A contribution to the IDC is a single flat YAML file placed under::

    data-managers/<data_manager>/<version>.yaml

where ``<data_manager>`` is the name of the data manager / primary data table
(e.g. ``metaphlan_database_versioned``) and ``<version>`` is the version being
requested (the file stem, e.g. ``mpa_vJan21_CHOCOPhlAnSGB_202103``).

The file describes how to build one reference-data *bundle* on a Galaxy server
(the build itself runs a gxformat2 data-manager-bundle workflow via planemo -
see ``workflows/`` and ``scripts/generate_build.py``). This module validates
those files and is run as the Stage 1 CI lint::

    python scripts/request_models.py                 # lint everything
    python scripts/request_models.py data-managers/motus_db_versioned/3.1.0.yaml
    python scripts/request_models.py --no-tool-schemas   # offline: skip the params check

``params`` are checked against the data manager's own parameter schema, which
the Tool Shed publishes per tool version (see ``tool_schemas.py``); the schema
for every GUID already in use is baked into ``schemas/request.schema.json`` so
that check normally needs no network.

Exit code is non-zero if any file fails validation.
"""
import argparse
import sys
from pathlib import Path
from typing import Optional

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool_schemas import (  # noqa: E402
    SchemaResolver,
    SchemaSource,
    SchemaUnavailable,
    validate_params,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_MANAGERS_DIR = REPO_ROOT / "data-managers"

# A toolshed GUID looks like:
#   toolshed.g2.bx.psu.edu/repos/<owner>/<repo>/<tool>/<version>
TOOL_ID_PREFIX = "toolshed.g2.bx.psu.edu/repos/"
# Same rule as _tool_id_is_a_guid below, for the exported JSON Schema (editors);
# the validator keeps the friendlier error message for the lint.
TOOL_ID_PATTERN = r"^toolshed\.g2\.bx\.psu\.edu/repos/[^/]+/[^/]+/[^/]+/[^/]+$"


class Request(BaseModel):
    """One requested reference-data version -> one bundle build."""

    model_config = ConfigDict(extra="forbid")

    # Full toolshed GUID of the data manager tool that builds this data.
    tool_id: str = Field(
        description=(
            "Version-pinned Tool Shed GUID of the data manager tool: "
            "toolshed.g2.bx.psu.edu/repos/<owner>/<repo>/<tool id>/<version>. <tool id> and <version> "
            "are the id= and version= of the <tool> tag, not the repository name."
        ),
        json_schema_extra={"pattern": TOOL_ID_PATTERN},
    )
    # Data table(s) the data manager populates (its bundle carries these rows).
    data_tables: list[str] = Field(
        min_length=1,
        description="Data table(s) the data manager writes. The request's directory must be named after one of them.",
    )
    # Tool parameters for this specific build, e.g. {"index": "mpa_vJan21_..."},
    # nested like the tool form; generate_build.py flattens them to a|b paths.
    params: dict[str, object] = Field(
        default={},
        description=(
            "The data manager's tool parameters for this build, keyed by <param name=> and nested like the "
            "tool form (db_source: {db_type: motus}). Checked against the tool's parameter schema from the Tool Shed."
        ),
    )
    # For chained builds: maps an upstream data table name -> the upstream
    # version this build depends on. e.g. samestr depends on a metaphlan db:
    #   depends_on: {metaphlan_database_versioned: mpa_vJan21_CHOCOPhlAnSGB_202103}
    depends_on: Optional[dict[str, str]] = Field(
        default=None,
        description=(
            "Chained builds: upstream data table name -> the upstream version this build is derived from. "
            "A request file must exist at data-managers/<table>/<version>.yaml."
        ),
    )

    # Human-facing provenance (unused by the build, but reviewed in the PR).
    description: Optional[str] = Field(default=None, description="What this data is, for reviewers.")
    doi: Optional[str] = Field(default=None, description="DOI of the publication or dataset this data comes from.")

    @field_validator("tool_id")
    @classmethod
    def _tool_id_is_a_guid(cls, v: str) -> str:
        if not v.startswith(TOOL_ID_PREFIX):
            raise ValueError(
                f"tool_id must be a production Tool Shed GUID starting with {TOOL_ID_PREFIX!r}, got: {v!r}"
            )
        # Require the full, version-pinned GUID:
        #   host/repos/owner/repo/tool/version   (6 slash-separated parts)
        # The pinned tool version is what makes a reference-data build
        # reproducible and auditable, so it is mandatory - not left to whatever
        # revision happens to be installed at build time.
        if len(v.split("/")) < 6:
            raise ValueError(
                f"tool_id must be a version-pinned GUID host/repos/owner/repo/tool/version "
                f"(the trailing tool version is required for reproducibility): {v!r}"
            )
        return v


class LintError(Exception):
    pass


def data_manager_name(path: Path) -> str:
    """The data manager identity == the parent directory name."""
    return path.parent.name


def version_id(path: Path) -> str:
    """The requested version == the file stem."""
    return path.stem


def iter_request_files() -> list[Path]:
    if not DATA_MANAGERS_DIR.is_dir():
        return []
    return sorted(p for p in DATA_MANAGERS_DIR.rglob("*.y*ml") if p.is_file())


def lint_file(path: Path, tool_schemas: Optional[SchemaResolver] = None) -> list[str]:
    """Return a list of error strings for one request file (empty == ok).

    ``tool_schemas`` resolves a tool GUID to the data manager's params schema
    (see ``tool_schemas.SchemaSource``); None skips the params check.
    """
    errors: list[str] = []
    try:
        rel = path.relative_to(REPO_ROOT)
    except ValueError:
        rel = path

    # Structural: must be data-managers/<dm>/<version>.yaml (exactly one level deep).
    try:
        depth = path.relative_to(DATA_MANAGERS_DIR).parts
    except ValueError:
        return [f"{rel}: request files must live under data-managers/"]
    if len(depth) != 2:
        errors.append(
            f"{rel}: request files must be at data-managers/<data_manager>/<version>.yaml "
            f"(got {len(depth)} path component(s) under data-managers/)"
        )
        return errors

    dm = data_manager_name(path)

    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        return [f"{rel}: invalid YAML: {exc}"]
    if not isinstance(doc, dict):
        return [f"{rel}: top-level YAML must be a mapping"]

    try:
        req = Request(**doc)
    except Exception as exc:  # pydantic ValidationError et al.
        return [f"{rel}: schema validation failed:\n{exc}"]

    # The directory name should be one of the data tables the DM populates,
    # so the on-disk identity matches what the bundle actually writes.
    if dm not in req.data_tables:
        errors.append(
            f"{rel}: directory name {dm!r} is not in data_tables {req.data_tables} - "
            f"the folder must be named after the data manager's primary data table"
        )

    # Chained builds: the upstream version must itself have a request file, so the
    # generator can build the upstream step of the workflow.
    for up_table, up_version in (req.depends_on or {}).items():
        up_path = DATA_MANAGERS_DIR / up_table / f"{up_version}.yaml"
        up_path_yml = DATA_MANAGERS_DIR / up_table / f"{up_version}.yml"
        if not up_path.exists() and not up_path_yml.exists():
            errors.append(
                f"{rel}: depends_on {up_table}=={up_version} but no request file "
                f"exists at data-managers/{up_table}/{up_version}.yaml - "
                f"add the upstream request so it can be built first"
            )

    # params must be parameters the data manager actually has, with values it
    # accepts. Only the tool knows that, and the Tool Shed publishes its answer
    # as a schema. The chain wiring baked in by generate_build.py is not part
    # of params and is validated there, by gxformat2.
    if tool_schemas is not None:
        try:
            schema = tool_schemas(req.tool_id)
        except SchemaUnavailable as exc:
            errors.append(
                f"{rel}: cannot check params - no parameter schema for tool_id {req.tool_id}: {exc}"
            )
        else:
            for problem in validate_params(schema, req.params):
                errors.append(f"{rel}: params: {problem}")

    return errors


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Lint IDC reference-data request files.")
    parser.add_argument("files", nargs="*", help="Request file(s); default: every file under data-managers/")
    parser.add_argument(
        "--no-tool-schemas",
        action="store_true",
        help="Skip checking params against the data manager's parameter schema (offline mode)",
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Check params only against schemas already in schemas/request.schema.json; never ask the Tool Shed",
    )
    args = parser.parse_args(argv)

    if args.files:
        files = [Path(a).resolve() for a in args.files]
    else:
        files = iter_request_files()

    if not files:
        print("No request files found under data-managers/ - nothing to lint.")
        return 0

    tool_schemas = None if args.no_tool_schemas else SchemaSource(fetch=not args.no_fetch)
    all_errors: list[str] = []
    for path in files:
        errs = lint_file(path, tool_schemas)
        if errs:
            all_errors.extend(errs)
        else:
            print(f"ok: {path.relative_to(REPO_ROOT)}")

    if all_errors:
        print("\n".join(["", "Lint failed:", *all_errors]), file=sys.stderr)
        return 1
    print(f"\nAll {len(files)} request file(s) valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
