"""Offline tests for scripts/cleanup_builds.py: which build histories are purged."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cleanup_builds as cb  # noqa: E402

NOW = datetime(2026, 11, 27, tzinfo=timezone.utc)
OLD = "2026-10-01T00:00:00"
RECENT = "2026-11-25T00:00:00"


class FakeGi:
    """Build histories, each with an optional invocation that ran stored workflow
    ``sw-<workflow>`` and produced one bundle dataset ``ds-<id>``."""

    def __init__(self):
        self.histories_by_id: dict[str, dict] = {}
        self.invocations_by_history: dict[str, list[dict]] = {}
        self.dataset_states: dict[str, str] = {}
        self.purged: list[str] = []
        self.deleted_workflows: list[str] = []
        gi = self

        class Histories:
            def get_histories(self, deleted=False, view=None, keys=None):
                return [h for h in gi.histories_by_id.values() if h["deleted"] == deleted]

            def delete_history(self, history_id, purge=False):
                assert purge
                gi.purged.append(history_id)

        class Invocations:
            def get_invocations(self, history_id):
                return gi.invocations_by_history.get(history_id, [])

            def show_invocation(self, invocation_id):
                return next(
                    i for invs in gi.invocations_by_history.values() for i in invs if i["id"] == invocation_id
                )

        class Datasets:
            def show_dataset(self, dataset_id):
                return {"id": dataset_id, "state": gi.dataset_states.get(dataset_id, "ok")}

        class Workflows:
            def show_workflow(self, workflow_id, instance=False):
                assert instance
                return {"id": f"sw-{workflow_id}"}

            def delete_workflow(self, workflow_id):
                gi.deleted_workflows.append(workflow_id)

        self.histories = Histories()
        self.invocations = Invocations()
        self.datasets = Datasets()
        self.workflows = Workflows()

    def add(self, history_id, name, updated=OLD, tags=("idc",), state=None, workflow="wf", deleted=False):
        """A history; with ``state``, it holds one invocation whose bundle has that state."""
        self.histories_by_id[history_id] = {
            "id": history_id, "name": name, "tags": list(tags), "create_time": updated,
            "update_time": updated, "deleted": deleted,
        }
        if state is not None:
            self.invocations_by_history[history_id] = [{
                "id": f"inv-{history_id}",
                "create_time": updated,
                "state": "scheduled",
                "workflow_id": workflow,
                "outputs": {"x_bundle": {"id": f"ds-{history_id}", "src": "hda"}},
            }]
            self.dataset_states[f"ds-{history_id}"] = state


def _requests(*names):
    """History name -> (dm, version) for names ``idc-<dm>-<version>``."""
    return {name: tuple(name.removeprefix("idc-").split("-", 1)) for name in names}


def _plan(gi, requests, published):
    decisions = cb.plan(gi, requests, lambda dm, version: (dm, version) in published, timedelta(days=7), now=NOW)
    return {d.history["id"]: (d.purge, d.reason) for d in decisions}


def test_plan_purges_only_what_publishing_no_longer_needs():
    gi = FakeGi()
    gi.add("pub", "idc-a-1", state="ok")
    gi.add("pub-rebuild", "idc-a-1", state="error")
    gi.add("waiting", "idc-b-1", state="ok")
    gi.add("running", "idc-b-2", state="running")
    gi.add("failed", "idc-b-3", state="error")
    gi.add("empty", "idc-b-4")
    gi.add("orphan", "idc-gone-1", state="ok")
    gi.add("untagged", "idc-gone-2", tags=(), state="ok")
    gi.add("fresh", "idc-a-1", updated=RECENT, state="ok")
    gi.add("other", "Unnamed history")

    plan = _plan(gi, _requests("idc-a-1", "idc-b-1", "idc-b-2", "idc-b-3", "idc-b-4"), {("a", "1")})

    assert {h for h, (purge, _) in plan.items() if purge} == {"pub", "pub-rebuild", "failed", "empty", "orphan"}
    assert "other" not in plan
    assert plan["fresh"] == (False, "published (record marker exists), but updated within 7 days")
    assert plan["waiting"] == (False, "unpublished build")


def test_workflow_kept_while_a_kept_history_ran_it():
    gi = FakeGi()
    gi.add("pub", "idc-a-1", state="ok", workflow="shared")
    gi.add("waiting", "idc-b-1", state="ok", workflow="shared")
    gi.add("pub2", "idc-a-2", state="ok", workflow="alone")
    decisions = cb.plan(
        gi, _requests("idc-a-1", "idc-b-1", "idc-a-2"),
        lambda dm, version: dm == "a", timedelta(days=7), now=NOW,
    )
    assert cb.orphaned_workflows(decisions) == {"sw-alone"}


def test_lost_builds_are_unpublished_requests_with_only_deleted_histories():
    gi = FakeGi()
    gi.add("gone", "idc-b-1", deleted=True)
    gi.add("gone-published", "idc-a-1", deleted=True)
    gi.add("gone-but-rebuilt", "idc-b-2", deleted=True)
    gi.add("rebuilt", "idc-b-2", state="ok")
    requests = _requests("idc-a-1", "idc-b-1", "idc-b-2", "idc-b-3")
    assert cb.lost_builds(gi, requests, lambda dm, version: dm == "a") == ["idc-b-1"]


def test_requests_are_what_the_publish_glob_sees():
    requests = cb.requests_on("HEAD")
    assert requests["idc-motus_db_versioned-3.1.0"] == ("motus_db_versioned", "3.1.0")
    assert all(name.startswith("idc-") for name in requests)


def test_main_refuses_without_cvmfs(tmp_path):
    with pytest.raises(SystemExit):
        cb.main(["--galaxy-api-key", "k", "--cvmfs-root", str(tmp_path), "--ref", "HEAD"])


@pytest.mark.parametrize("apply", [False, True])
def test_main_purges_only_with_apply(tmp_path, monkeypatch, apply):
    (tmp_path / "record").mkdir()
    gi = FakeGi()
    gi.add("orphan", "idc-gone-1", state="ok")
    monkeypatch.setattr(cb, "GalaxyInstance", lambda url, key: gi)
    monkeypatch.setattr(cb, "DELETE_PAUSE", 0)
    argv = ["--galaxy-api-key", "k", "--cvmfs-root", str(tmp_path), "--ref", "HEAD"] + (["--apply"] if apply else [])
    assert cb.main(argv) == 0
    assert gi.purged == (["orphan"] if apply else [])
    assert gi.deleted_workflows == (["sw-wf"] if apply else [])
