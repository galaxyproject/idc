#!/usr/bin/env python3
"""Check whether a request's reference data already exists in a Galaxy data table.

The most authoritative "does this already exist?" signal is the target Galaxy's
tool data table: ``GET /api/tool_data/<table>`` (public, no API key) lists the
entries actually available there - from *any* source, including the byhand
``data.galaxyproject.org`` CVMFS - so we never rebuild or re-import data a Galaxy
already has. It is the pipeline's only idempotency signal, so a check that cannot
be answered (timeout, 5xx, unparseable response) is reported as such rather than
silently read as "not present" - see ``CheckUnavailable``.

Matching the request's version to a table entry is done heuristically, because
the identifying column differs per data manager (e.g. MetaPhlAn keys on ``dbkey``,
mOTUs on ``value``, SameStr on the upstream MetaPhlAn value). An entry counts as
present if any of the request's identity strings - its version, its ``params``
values, or its ``depends_on`` versions - equals any field of a row, or is the
``value`` column optionally followed by a ``-<suffix>`` (e.g. a build date).

Usage::

    python scripts/check_data_exists.py --all                       # exit 1 if any exist
    python scripts/check_data_exists.py --all --warn                # annotate, exit 0
    python scripts/check_data_exists.py --all --expect-exists        # verify after an import
    python scripts/check_data_exists.py data-managers/motus_db_versioned/3.1.0.yaml
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from request_models import (  # noqa: E402
    Request,
    data_manager_name,
    iter_request_files,
    version_id,
)

DEFAULT_GALAXY = "https://test.galaxyproject.org"


class CheckUnavailable(Exception):
    """The reference Galaxy could not be asked whether the data exists.

    Distinct from a definitive "no": a timeout, a 5xx or an unparseable response
    means we do not know. Since this check is the pipeline's only idempotency
    signal, callers must not read it as "absent" and go build.
    """


def fetch_table(galaxy_url: str, table: str) -> dict | None:
    """GET /api/tool_data/<table> -> {columns, fields}.

    Returns None if the table is not configured on that Galaxy (404) - a
    definitive "this Galaxy has no such data". Raises CheckUnavailable if the
    question could not be answered at all. Public endpoint, no key needed.
    """
    url = f"{galaxy_url.rstrip('/')}/api/tool_data/{table}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (fixed https host)
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise CheckUnavailable(f"{url}: HTTP {exc.code} {exc.reason}") from exc
    except Exception as exc:
        raise CheckUnavailable(f"{url}: {exc}") from exc


def identity_strings(request: Request, version: str) -> set[str]:
    """The strings that could identify this build in a data table entry."""
    candidates = {version}
    candidates |= {str(v) for v in (request.params or {}).values()}
    candidates |= {str(v) for v in (request.depends_on or {}).values()}
    return {c for c in candidates if c}


def matching_value(table_data: dict, candidates: set[str]) -> str | None:
    """The ``value`` column of the first row matching any candidate, else None."""
    for row in table_data.get("fields", []):
        row_strings = [str(x) for x in row]
        value = row_strings[0] if row_strings else ""
        for candidate in candidates:
            if candidate in row_strings or value == candidate or value.startswith(candidate + "-"):
                return value
    return None


def entry_exists(table_data: dict, candidates: set[str]) -> bool:
    return matching_value(table_data, candidates) is not None


def resolve_existing_value(galaxy_url: str, table: str, version: str) -> str | None:
    """The data-table ``value`` for an existing entry of ``version``, else None.

    Used to reference an already-built upstream database (e.g. a MetaPhlAn DB a
    SameStr build depends on) instead of rebuilding it. CheckUnavailable
    propagates: generating a workflow that silently rebuilds a multi-hour
    upstream database because the Galaxy was briefly unreachable is worse than
    failing the build step.
    """
    table_data = fetch_table(galaxy_url, table)
    if table_data is None:
        return None
    return matching_value(table_data, {version})


def request_exists(request: Request, version: str, galaxy_url: str) -> bool:
    """True if any of the request's data tables already carries this version.

    Raises CheckUnavailable if a table could not be queried and no other table
    gave a positive answer - "we could not tell" must not pass for "not there".
    """
    candidates = identity_strings(request, version)
    unavailable: list[str] = []
    for table in request.data_tables:
        try:
            table_data = fetch_table(galaxy_url, table)
        except CheckUnavailable as exc:
            unavailable.append(str(exc))
            continue
        if table_data is None:
            # Not a failure: the table simply is not configured there, which is
            # expected for a brand-new data manager. Say so, since it means this
            # request can never be recognised as already-built via this table.
            print(
                f"::warning:: data table {table!r} is not configured on {galaxy_url} - "
                f"the existence check cannot answer from it",
                file=sys.stderr,
            )
            continue
        if entry_exists(table_data, candidates):
            return True
    if unavailable:
        raise CheckUnavailable("; ".join(unavailable))
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requests", nargs="*", help="Request file(s) to check")
    parser.add_argument("--all", action="store_true", help="Check every request under data-managers/")
    parser.add_argument("--from-file", help="Read request paths from this file (one per line)")
    parser.add_argument("--reference-galaxy", default=DEFAULT_GALAXY, help="Galaxy whose data tables to query")
    parser.add_argument("--warn", action="store_true", help="Annotate and exit 0 instead of failing")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--print-new",
        action="store_true",
        help="Print (stdout) the requests whose data does NOT exist yet. For build/import filtering. "
        "Exits non-zero only if the reference Galaxy could not answer for some request.",
    )
    mode.add_argument(
        "--expect-exists",
        action="store_true",
        help="Invert: fail if a request is NOT present. Post-import verification that the version "
        "identity we key on is the one the data manager actually wrote into the table.",
    )
    args = parser.parse_args(argv)

    if args.all:
        paths = iter_request_files()
    else:
        raw = list(args.requests)
        if args.from_file:
            raw += [ln.strip() for ln in Path(args.from_file).read_text().splitlines() if ln.strip()]
        paths = [Path(r) for r in raw if Path(r).is_file()]

    new, existing, unknown = [], [], []
    for path in paths:
        request = Request(**yaml.safe_load(Path(path).read_text()))
        version = version_id(Path(path))
        dm = data_manager_name(Path(path))
        try:
            found = request_exists(request, version, args.reference_galaxy)
        except CheckUnavailable as exc:
            unknown.append((path, dm, version, str(exc)))
            continue
        (existing.append((path, dm, version)) if found else new.append(path))

    prefix = "::warning:: " if args.warn or args.print_new else ""
    for path, dm, version, reason in unknown:
        print(
            f"{prefix}{dm}/{version}: cannot tell whether this already exists - {reason} ({path})",
            file=sys.stderr,
        )

    if args.expect_exists:
        # The pipeline keys idempotency on data-managers/<dm>/<version>, assuming
        # <version> is findable in the row the data manager wrote. Nothing can
        # check that before the build - only the data manager knows what value it
        # emits - so this is the post-condition, run once CVMFS has propagated.
        for _path, dm, version in existing:
            print(f"ok: {dm}/{version} is present on {args.reference_galaxy}")
        for path in new:
            print(
                f"::error:: {data_manager_name(Path(path))}/{version_id(Path(path))} is not in any of its "
                f"data tables on {args.reference_galaxy} - either it has not propagated yet, or the "
                f"request's version identity does not match the entry that was written ({path})",
                file=sys.stderr,
            )
        return 1 if (new or unknown) else 0

    if args.print_new:
        # Say what was dropped, so an empty build list is diagnosable.
        for path, dm, version in existing:
            print(f"skip {dm}/{version}: already exists on {args.reference_galaxy} ({path})", file=sys.stderr)
        for path in new:
            print(path)
        # A question we could not answer must not silently become "build it".
        return 1 if unknown else 0

    for path, dm, version in existing:
        print(f"{prefix}{dm}/{version} already exists on {args.reference_galaxy} ({path})", file=sys.stderr)

    if not existing and not unknown:
        print(f"No requested reference data already exists on {args.reference_galaxy}.")
        return 0
    return 0 if args.warn else 1


if __name__ == "__main__":
    raise SystemExit(main())
