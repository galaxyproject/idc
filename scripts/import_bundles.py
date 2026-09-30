#!/usr/bin/env python3
"""Import a build's reference-data bundles onto CVMFS (Stage 3).

Given a finished data-manager-bundle workflow invocation, this resolves every
bundle it produced (see scripts/get_bundle_urls.py) and imports each onto CVMFS
with ``galaxy-import-data-bundle`` (from galaxy-maintenance-scripts), which moves
the data under ``<cvmfs-root>/data``, appends the new ``.loc`` rows, relativizes
symlinks, and reloads the tables.

Idempotency mirrors the existing IDC importer's ``record/`` markers, generalized
to the reference-data identity: a build is skipped if
``<cvmfs-root>/record/<dm>/<version>`` already exists, and that marker is written
after a successful import.

A chained build (a request with ``depends_on``) also produces its upstream
data manager's bundle. Given ``--request <yaml>``, an upstream bundle is skipped
when ``record/<upstream dm>/<upstream version>`` already exists (it was published
by its own request), and that marker is written when the chain imports it - so
the same database is never imported twice.

This is meant to run inside the Jenkins CVMFS transaction (see .ci/jenkins.sh);
``--dry-run`` prints the exact commands without importing, so the wiring is
testable offline.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from get_bundle_urls import (  # noqa: E402
    DEFAULT_BUNDLE_SUFFIX,
    bundle_dataset_ids_from_invocation,
    bundle_url,
    bundles_from_history,
)


def import_command(import_cmd: str, cvmfs_root: str, url: str) -> list[str]:
    """The galaxy-import-data-bundle invocation for one bundle URL."""
    return [
        import_cmd,
        "--tool-data-path",
        f"{cvmfs_root}/data",
        "--data-table-config-path",
        f"{cvmfs_root}/config/tool_data_table_conf.xml",
        url,
    ]


def record_marker(cvmfs_root: str, dm: str, version: str) -> Path:
    return Path(cvmfs_root) / "record" / dm / version


def upstream_versions(request_path: str | None) -> dict[str, str]:
    """``depends_on`` of a request file: upstream data manager -> version."""
    if not request_path:
        return {}
    import yaml

    with open(request_path) as fh:
        request = yaml.safe_load(fh) or {}
    return dict(request.get("depends_on") or {})


def bundle_dm(label: str, suffix: str) -> str:
    """The data manager a bundle output label (``<dm>_bundle``) belongs to."""
    return label[: -len(suffix)] if suffix and label.endswith(suffix) else label


def _galaxy_connection(args):
    from bioblend.galaxy import GalaxyInstance

    api_key = args.galaxy_api_key or os.environ.get("EPHEMERIS_API_KEY")
    if not api_key:
        raise SystemExit("No Galaxy API key (use --galaxy-api-key or set $EPHEMERIS_API_KEY)")
    return GalaxyInstance(url=args.galaxy_url, key=api_key)


def check_bundles_ready(gi, bundles: dict[str, str]) -> None:
    """Refuse bundles whose dataset is not ``ok``.

    A build runs for hours after its request merges; importing a bundle that is
    still being produced (or whose job failed) would publish an empty or broken
    database. Fail loudly instead so the publish can be retried later.
    """
    not_ready = {}
    for label, dataset_id in bundles.items():
        state = gi.datasets.show_dataset(dataset_id).get("state")
        if state != "ok":
            not_ready[label] = state
    if not_ready:
        detail = ", ".join(f"{label} is {state!r}" for label, state in not_ready.items())
        raise SystemExit(f"Build not finished or failed - refusing to import: {detail}")


def resolve_bundles(args) -> dict[str, str]:
    """Map bundle label -> dataset id, from whichever source was given.

    Bundles resolved from a live Galaxy are checked to be in the ``ok`` state;
    an offline ``--invocation-json`` is taken as-is.
    """
    if args.invocation_json:
        import json

        with open(args.invocation_json) as fh:
            invocation = json.load(fh)
        return bundle_dataset_ids_from_invocation(invocation, suffix=args.bundle_suffix)
    gi = _galaxy_connection(args)
    if args.invocation_id:
        invocation = gi.invocations.show_invocation(args.invocation_id)
        bundles = bundle_dataset_ids_from_invocation(invocation, suffix=args.bundle_suffix)
    else:
        # history-name fallback (the stable key shared by the build and import stages)
        bundles = bundles_from_history(gi, args.history_name)
    check_bundles_ready(gi, bundles)
    return bundles


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-g", "--galaxy-url", default="http://localhost:8080", help="Galaxy server URL")
    parser.add_argument("-a", "--galaxy-api-key", help="Galaxy API key (or set $EPHEMERIS_API_KEY)")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--invocation-json", help="Path to a saved invocation dict (offline)")
    source.add_argument("--invocation-id", help="Workflow invocation id to fetch from Galaxy")
    source.add_argument("--history-name", help="History to scan for data_manager_json bundles")
    parser.add_argument("--dm", required=True, help="Data manager name (record identity)")
    parser.add_argument("--version", required=True, help="Version being imported (record identity)")
    parser.add_argument("--cvmfs-root", default="/cvmfs/idc.galaxyproject.org", help="CVMFS repo root")
    parser.add_argument("--bundle-suffix", default=DEFAULT_BUNDLE_SUFFIX, help="Bundle output label suffix")
    parser.add_argument(
        "--import-cmd",
        default="galaxy-import-data-bundle",
        help="galaxy-import-data-bundle executable (path)",
    )
    parser.add_argument(
        "--request",
        help="The request YAML; its depends_on lets already-published upstream bundles be skipped",
    )
    parser.add_argument("--overwrite", action="store_true", help="Import even if a record marker exists")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without importing")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    marker = record_marker(args.cvmfs_root, args.dm, args.version)
    if marker.exists() and not args.overwrite:
        print(f"Already imported: {args.dm}/{args.version} (record {marker} exists); skipping")
        return 0

    bundles = resolve_bundles(args)
    if not bundles:
        # Nothing to import - no build history/invocation (e.g. the build was
        # skipped because the data already exists). Not an error.
        print(f"No bundles to import for {args.dm}/{args.version}; skipping")
        return 0

    upstream = upstream_versions(args.request)
    imported: dict[str, str] = {}
    upstream_markers: list[Path] = []
    for label, dataset_id in bundles.items():
        dm = bundle_dm(label, args.bundle_suffix)
        if dm != args.dm and dm in upstream:
            # The chain rebuilt its upstream database; import it only if that
            # database's own request has not already published it.
            up_marker = record_marker(args.cvmfs_root, dm, upstream[dm])
            if up_marker.exists() and not args.overwrite:
                print(f"# skip {label}: {dm}/{upstream[dm]} already imported (record {up_marker} exists)")
                continue
            upstream_markers.append(up_marker)
        url = bundle_url(args.galaxy_url, dataset_id)
        cmd = import_command(args.import_cmd, args.cvmfs_root, url)
        print(f"# import {label}")
        print(" ".join(cmd))
        if not args.dry_run:
            subprocess.run(cmd, check=True)
        imported[label] = dataset_id

    if args.dry_run:
        for up_marker in upstream_markers:
            print(f"# (dry-run) would record: {up_marker}")
        print(f"# (dry-run) would record: {marker}")
        return 0

    record = "\n".join(f"{label}: {ds}" for label, ds in imported.items()) + "\n"
    for record_path in [*upstream_markers, marker]:
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(record)
        print(f"Recorded import: {record_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
