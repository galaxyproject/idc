#!/usr/bin/env python3
"""Resolve the reference-data *bundle* download URLs produced by a build.

Stage 2 runs a data-manager-bundle workflow on a Galaxy server; each data
manager step declares its bundle as a named workflow output (``<step>_bundle``,
see scripts/generate_build.py). This script turns a finished **workflow
invocation** into the list of bundle dataset download URLs that Stage 3
(``galaxy-import-data-bundle``) imports onto CVMFS.

Unlike the legacy ``.ci/get-bundle-url.py`` - which returned a single bundle
from a history - a chained build (e.g. samestr, which also builds MetaPhlAn)
produces **several** bundles in one invocation, and all of them are returned.

The invocation can be supplied three ways:

* ``--invocation-json FILE`` - a saved invocation dict (no Galaxy needed; used
  by the offline unit tests and handy for debugging),
* ``--invocation-id ID`` - fetched from Galaxy via bioblend,
* ``--history-name NAME`` - fallback that scans the history for
  ``data_manager_json`` datasets (order preserved), for builds not driven by a
  named-output workflow.

Prints one bundle URL per line (import loop friendly) and, with
``--record-file``, writes a YAML provenance record.
"""
import argparse
import os
import sys

EXT = "data_manager_json"
DEFAULT_BUNDLE_SUFFIX = "_bundle"
HDA_SRC = "hda"
FAILED_DATASET_STATES = ("error", "failed_metadata", "paused")
FAILED_INVOCATION_STATES = ("failed", "cancelled", "cancelling")


class BuildUnavailable(Exception):
    """A build history exists but holds no build that can be imported."""


def bundle_url(galaxy_url: str, dataset_id: str) -> str:
    """Download URL for a bundle dataset (composite zip of the bundle dir)."""
    return f"{galaxy_url.rstrip('/')}/api/datasets/{dataset_id}/display?to_ext={EXT}"


def bundle_dataset_ids_from_invocation(
    invocation: dict, suffix: str = DEFAULT_BUNDLE_SUFFIX
) -> dict[str, str]:
    """Map bundle output label -> dataset id for a workflow invocation.

    Considers the invocation's named ``outputs`` (``dict[label -> {id, src}]``).
    Keeps HDA outputs whose label ends with ``suffix`` (pass ``suffix=""`` to
    take every HDA output). Order follows the invocation's output ordering.
    """
    result: dict[str, str] = {}
    for label, output in (invocation.get("outputs") or {}).items():
        if output.get("src", HDA_SRC) != HDA_SRC:
            continue
        if suffix and not label.endswith(suffix):
            continue
        dataset_id = output.get("id")
        if not dataset_id:
            raise ValueError(f"Invocation output {label!r} has no dataset id: {output!r}")
        result[label] = dataset_id
    return result


def latest_invocation(gi, history_id: str) -> dict | None:
    """The newest workflow invocation in a history (full ``show_invocation``
    dict), or None if the history has none."""
    invocations = gi.invocations.get_invocations(history_id=history_id)
    if not invocations:
        return None
    latest = max(invocations, key=lambda i: i.get("create_time", ""))
    return gi.invocations.show_invocation(latest["id"])


def build_failed(gi, invocation: dict, suffix: str = DEFAULT_BUNDLE_SUFFIX) -> bool:
    """True if ``invocation`` can no longer deliver its bundles: it failed or was
    cancelled, or one of its bundle outputs errored, was paused or was deleted."""
    if invocation.get("state") in FAILED_INVOCATION_STATES:
        return True
    for dataset_id in bundle_dataset_ids_from_invocation(invocation, suffix=suffix).values():
        dataset = gi.datasets.show_dataset(dataset_id)
        if dataset.get("state") in FAILED_DATASET_STATES or dataset.get("deleted") or dataset.get("purged"):
            return True
    return False


def build_invocation(gi, history_name: str, suffix: str = DEFAULT_BUNDLE_SUFFIX) -> dict | None:
    """The build to import for ``history_name`` (``idc-<dm>-<version>``).

    A build is the latest workflow invocation in a history of that name; a
    rebuild creates another history with the same name, and a history without an
    invocation holds no build. The newest build that has not failed is the one to
    import, even if it is still running: the import then refuses its bundles
    until they are ``ok``. Returns None if no history of that name exists (the
    build was skipped because the data already exists) and raises
    BuildUnavailable if there are histories but none holds a build that has not
    failed.
    """
    histories = gi.histories.get_histories(name=history_name, deleted=False)
    if not histories:
        return None
    details = [gi.histories.show_history(h["id"]) for h in histories]
    for history in sorted(details, key=lambda h: h.get("create_time", ""), reverse=True):
        invocation = latest_invocation(gi, history["id"])
        if invocation is not None and not build_failed(gi, invocation, suffix=suffix):
            return invocation
    raise BuildUnavailable(
        f"no history named {history_name!r} holds a build that has not failed; rebuild it before importing"
    )


def bundles_from_history(gi, history_name: str, suffix: str = DEFAULT_BUNDLE_SUFFIX) -> dict[str, str]:
    """Resolve a build's bundles from its history name (the stable key shared by
    the build and import stages): the named ``*_bundle`` outputs of the
    invocation ``build_invocation`` picks.

    Returns ``{}`` if there is no history of that name, so callers can skip, and
    raises BuildUnavailable if the build has not produced its bundle outputs yet.
    """
    invocation = build_invocation(gi, history_name, suffix=suffix)
    if invocation is None:
        return {}
    bundles = bundle_dataset_ids_from_invocation(invocation, suffix=suffix)
    if not bundles:
        raise BuildUnavailable(
            f"the build in {history_name!r} (invocation {invocation.get('id')}) has no bundle outputs yet"
        )
    return bundles


def _galaxy_connection(args):
    from bioblend.galaxy import GalaxyInstance

    api_key = args.galaxy_api_key or os.environ.get("EPHEMERIS_API_KEY")
    if not api_key:
        raise SystemExit("No Galaxy API key (use --galaxy-api-key or set $EPHEMERIS_API_KEY)")
    return GalaxyInstance(url=args.galaxy_url, key=api_key)


def _load_invocation(args) -> dict:
    if args.invocation_json:
        import json

        with open(args.invocation_json) as fh:
            return json.load(fh)
    if args.invocation_id:
        gi = _galaxy_connection(args)
        return gi.invocations.show_invocation(args.invocation_id)
    raise SystemExit("internal: no invocation source")  # guarded in main()


def _bundles_from_history(args) -> dict[str, str]:
    """The bundle outputs of the build to import from a named history."""
    try:
        return bundles_from_history(_galaxy_connection(args), args.history_name, suffix=args.bundle_suffix)
    except BuildUnavailable as exc:
        raise SystemExit(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-g", "--galaxy-url", default="http://localhost:8080", help="Galaxy server URL")
    parser.add_argument("-a", "--galaxy-api-key", help="Galaxy API key (or set $EPHEMERIS_API_KEY)")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--invocation-json", help="Path to a saved invocation dict (offline)")
    source.add_argument("--invocation-id", help="Workflow invocation id to fetch from Galaxy")
    source.add_argument("--history-name", help="Build history name (idc-<dm>-<version>); uses its newest build that has not failed")
    parser.add_argument(
        "--bundle-suffix",
        default=DEFAULT_BUNDLE_SUFFIX,
        help="Only take invocation outputs whose label ends with this (default: %(default)s; '' for all)",
    )
    parser.add_argument("-r", "--record-file", help="Write a YAML provenance record here")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.history_name:
        bundles = _bundles_from_history(args)
    else:
        invocation = _load_invocation(args)
        bundles = bundle_dataset_ids_from_invocation(invocation, suffix=args.bundle_suffix)

    if not bundles:
        raise SystemExit("No bundle datasets found for this build")

    urls = {label: bundle_url(args.galaxy_url, ds_id) for label, ds_id in bundles.items()}

    if args.record_file:
        import yaml

        record = {
            "galaxy_url": args.galaxy_url,
            "invocation_id": args.invocation_id,
            "history_name": args.history_name,
            "bundles": [
                {"label": label, "dataset_id": bundles[label], "url": urls[label]}
                for label in bundles
            ],
        }
        with open(args.record_file, "w") as fh:
            yaml.safe_dump(record, fh, sort_keys=False)

    for label in bundles:
        print(urls[label])
    return 0


if __name__ == "__main__":
    sys.exit(main())
