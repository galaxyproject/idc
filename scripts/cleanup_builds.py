#!/usr/bin/env python3
"""Purge build histories on the build Galaxy that publishing no longer needs.

Every build leaves a history ``idc-<dm>-<version>`` (and the stored workflow it
ran) on test.galaxyproject.org. Deleting them never starts a rebuild: build.yml
runs only on a push that changes a request file or on a manual dispatch, and it
asks the data table API, not the histories, whether data exists. What deleting
can do is lose a build that hasn't been published yet: the publish job
(.ci/jenkins.sh import_reference_data_bundles) runs import_bundles.py for every
request on main, which skips a request only if ``record/<dm>/<version>`` exists
on CVMFS and otherwise imports from the history. So a history is purged only
when:

* its request has a record marker under ``--cvmfs-root``. Data that test
  serves from elsewhere doesn't count: without a marker the publish still
  imports from the history;
* its request has no marker, but the history holds no build that could be
  imported (no invocation, or a failed one; see get_bundle_urls.build_failed),
  which the publish refuses either way;
* no request file on ``--ref`` names it, and it carries the ``idc`` tag
  build.yml sets.

Only histories whose last update is older than ``--min-age-days`` are purged,
so builds that are still being scheduled are left alone. A stored workflow is
deleted when every build history that ran it is purged.

The script also lists requests whose only histories are already deleted and
that have no marker: the publish fails on them until they are rebuilt.

Run it where the IDC CVMFS repository is mounted, with the build account's
API key (``$GALAXY_API_KEY``), since the histories belong to that account.
Dry run by default; ``--apply`` purges.

    python scripts/cleanup_builds.py                    # what would be purged
    python scripts/cleanup_builds.py --apply            # purge it
"""
import argparse
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

try:
    from bioblend import ConnectionError as GalaxyError
    from bioblend.galaxy import GalaxyInstance
except ImportError:  # the lint job's tests run without bioblend
    GalaxyInstance = None
    GalaxyError = Exception

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_data_exists import DEFAULT_GALAXY  # noqa: E402
from get_bundle_urls import build_failed, latest_invocation  # noqa: E402
from import_bundles import record_marker  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
HISTORY_PREFIX = "idc-"
BUILD_TAG = "idc"
HISTORY_KEYS = ["id", "name", "tags", "create_time", "update_time"]
REQUEST_SUFFIXES = (".yaml", ".yml")


@dataclass
class Decision:
    history: dict
    purge: bool
    reason: str
    stored_workflow_ids: set[str] = field(default_factory=set)


def history_name(dm: str, version: str) -> str:
    """The build history name build.yml gives a request."""
    return f"{HISTORY_PREFIX}{dm}-{version}"


def requests_on(ref: str) -> dict[str, tuple[str, str]]:
    """Build history name -> (dm, version) for every request the publish job
    would see on ``ref``: ``data-managers/<dm>/<version>.y(a)ml``."""
    out = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", ref, "--", "data-managers/"],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    ).stdout
    requests = {}
    for line in out.splitlines():
        path = PurePosixPath(line)
        if len(path.parts) == 3 and path.suffix in REQUEST_SUFFIXES:
            dm, version = path.parent.name, path.stem
            requests[history_name(dm, version)] = (dm, version)
    return requests


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    # Galaxy reports naive UTC timestamps.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def build_histories(gi, deleted: bool = False) -> list[dict]:
    """The account's histories that look like builds."""
    histories = gi.histories.get_histories(deleted=deleted, view="summary", keys=HISTORY_KEYS)
    return [h for h in histories if h.get("name", "").startswith(HISTORY_PREFIX)]


def stored_workflow_ids(gi, history_id: str, cache: dict[str, str]) -> set[str]:
    """Stored workflows whose invocations ran in this history."""
    ids = set()
    for invocation in gi.invocations.get_invocations(history_id=history_id):
        workflow_id = invocation["workflow_id"]
        if workflow_id not in cache:
            cache[workflow_id] = gi.workflows.show_workflow(workflow_id, instance=True)["id"]
        ids.add(cache[workflow_id])
    return ids


def plan(
    gi,
    requests: dict[str, tuple[str, str]],
    is_published: Callable[[str, str], bool],
    min_age: timedelta,
    now: Optional[datetime] = None,
) -> list[Decision]:
    """Decide, for every live build history, whether to purge it."""
    now = now or datetime.now(timezone.utc)
    workflow_cache: dict[str, str] = {}
    decisions = []
    for history in build_histories(gi):
        request = requests.get(history["name"])
        if request is None:
            if BUILD_TAG in (history.get("tags") or []):
                purge, reason = True, "no request file names it"
            else:
                purge, reason = False, f"no request file names it, but it lacks the {BUILD_TAG!r} tag"
        elif is_published(*request):
            purge, reason = True, "published (record marker exists)"
        else:
            invocation = latest_invocation(gi, history["id"])
            if invocation is None:
                purge, reason = True, "unpublished, holds no build"
            elif build_failed(gi, invocation):
                purge, reason = True, "unpublished, build failed"
            else:
                purge, reason = False, "unpublished build"
        if purge and now - _parse_time(history["update_time"]) < min_age:
            purge, reason = False, f"{reason}, but updated within {min_age.days} days"
        decisions.append(
            Decision(history, purge, reason, stored_workflow_ids(gi, history["id"], workflow_cache))
        )
    return decisions


def orphaned_workflows(decisions: list[Decision]) -> set[str]:
    """Stored workflows that ran only in histories being purged."""
    purged = set().union(*(d.stored_workflow_ids for d in decisions if d.purge))
    kept = set().union(*(d.stored_workflow_ids for d in decisions if not d.purge))
    return purged - kept


def lost_builds(
    gi, requests: dict[str, tuple[str, str]], is_published: Callable[[str, str], bool]
) -> list[str]:
    """Unpublished requests whose build histories are all deleted already.

    get_bundle_urls.build_invocation refuses these, so the publish fails until
    they are rebuilt.
    """
    live = {h["name"] for h in build_histories(gi)}
    deleted = {h["name"] for h in build_histories(gi, deleted=True)}
    return sorted(
        name for name, request in requests.items()
        if name in deleted and name not in live and not is_published(*request)
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-g", "--galaxy-url", default=DEFAULT_GALAXY, help="Build Galaxy (default: %(default)s)")
    parser.add_argument("-a", "--galaxy-api-key", help="Build account API key (or set $GALAXY_API_KEY)")
    parser.add_argument(
        "--cvmfs-root",
        default="/cvmfs/idc.galaxyproject.org",
        help="Mounted IDC CVMFS repository, for the record markers (default: %(default)s)",
    )
    parser.add_argument(
        "--ref",
        default="origin/main",
        help="Git ref whose data-managers/ the publish job sees (default: %(default)s; fetched first)",
    )
    parser.add_argument(
        "--min-age-days", type=int, default=7, help="Keep histories updated more recently (default: %(default)s)"
    )
    parser.add_argument("--apply", action="store_true", help="Purge; without it, only print the plan")
    args = parser.parse_args(argv)

    api_key = args.galaxy_api_key or os.environ.get("GALAXY_API_KEY")
    if not api_key:
        parser.error("no API key: pass --galaxy-api-key or set $GALAXY_API_KEY")
    if GalaxyInstance is None:
        parser.error("bioblend is not installed: pip install bioblend")
    if not (Path(args.cvmfs_root) / "record").is_dir():
        parser.error(f"{args.cvmfs_root}/record is not a directory: run where the IDC CVMFS repository is mounted")
    if args.ref.startswith("origin/"):
        # A stale ref would make a request merged since look like an orphan.
        subprocess.run(["git", "fetch", "--quiet", "origin"], cwd=REPO_ROOT, check=True)
    gi = GalaxyInstance(url=args.galaxy_url, key=api_key)

    requests = requests_on(args.ref)

    def is_published(dm: str, version: str) -> bool:
        return record_marker(args.cvmfs_root, dm, version).exists()

    decisions = plan(gi, requests, is_published, timedelta(days=args.min_age_days))
    workflows = orphaned_workflows(decisions)

    for decision in sorted(decisions, key=lambda d: (not d.purge, d.history["name"])):
        history = decision.history
        action = "purge" if decision.purge else "keep "
        print(f"{action} {history['name']} {history['id']} (updated {history['update_time']}): {decision.reason}")
    for name in lost_builds(gi, requests, is_published):
        print(f"::warning:: {name} is unpublished and all its histories are deleted; the publish fails until it is rebuilt")
    to_purge = [d for d in decisions if d.purge]
    print(
        f"{len(to_purge)} of {len(decisions)} build histories and {len(workflows)} stored workflows to purge"
        + ("" if args.apply else " (dry run; pass --apply to purge)")
    )
    if not args.apply:
        return 0

    failed = 0
    # Workflows first: a run that stops early leaves no workflow whose histories are gone.
    for workflow_id in sorted(workflows):
        try:
            gi.workflows.delete_workflow(workflow_id)
            print(f"deleted stored workflow {workflow_id}")
        except GalaxyError as exc:
            failed += 1
            print(f"error: deleting stored workflow {workflow_id}: {exc}", file=sys.stderr)
    for decision in to_purge:
        history = decision.history
        try:
            gi.histories.delete_history(history["id"], purge=True)
            print(f"purged history {history['name']} {history['id']}")
        except GalaxyError as exc:
            failed += 1
            print(f"error: purging history {history['name']} {history['id']}: {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
