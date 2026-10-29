"""Submission reporting uses the exact invocation and only comments on its request PR."""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import post_build_links as post  # noqa: E402
import share_build_invocation as share  # noqa: E402


REQUEST = "data-managers/fasta_indexes/hg38canon.yaml"
REPORT = {
    "tests": [
        {
            "data": {
                "invocation_details": {
                    "details": {
                        "invocation_id": "inv1",
                        "workflow_id": "stored1",
                        "history_id": "history1",
                    }
                }
            }
        }
    ]
}
INVOCATION = {
    "id": "inv1",
    "workflow_id": "revision1",
    "history_id": "history1",
    "state": "new",
    "outputs": {},
}


@pytest.fixture
def galaxy():
    calls = []
    responses = {
        ("GET", "/api/invocations/inv1"): (200, INVOCATION),
        ("GET", "/api/workflows/revision1?instance=true"): (200, {"id": "stored1"}),
        ("PUT", "/api/workflows/stored1/enable_link_access"): (
            200,
            {"importable": True},
        ),
        ("PUT", "/api/histories/history1/enable_link_access"): (
            200,
            {"importable": True},
        ),
    }

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            calls.append((self.command, self.path, self.headers.get("x-api-key")))
            status, body = responses.get(
                (self.command, self.path), (404, {"error": "unexpected request"})
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        do_GET = do_PUT = handle_request

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls, responses
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_share_exact_invocation_and_write_summary(
    galaxy, tmp_path, monkeypatch, capsys
):
    url, calls, _ = galaxy
    report = tmp_path / "run-report.json"
    report.write_text(json.dumps(REPORT))
    outdir = tmp_path / "build"
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GALAXY_API_KEY", "test-key")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert (
        share.main(
            [
                "--report",
                str(report),
                "--request",
                REQUEST,
                "--galaxy-url",
                url,
                "--outdir",
                str(outdir),
            ]
        )
        == 0
    )
    assert calls == [
        ("GET", "/api/invocations/inv1", "test-key"),
        ("GET", "/api/workflows/revision1?instance=true", "test-key"),
        ("PUT", "/api/workflows/stored1/enable_link_access", "test-key"),
        ("PUT", "/api/histories/history1/enable_link_access", "test-key"),
    ]
    assert json.loads((outdir / "invocation.json").read_text()) == INVOCATION
    record = json.loads((outdir / "shared-invocation.json").read_text())
    assert record == {
        "request": REQUEST,
        "invocation_id": "inv1",
        "history_id": "history1",
        "workflow_id": "stored1",
        "url": f"{url}/workflows/invocations/inv1",
    }
    assert record["url"] in summary.read_text()
    assert record["url"] in capsys.readouterr().out
    assert "test-key" not in summary.read_text()


@pytest.mark.parametrize(
    "report", [{}, {"tests": []}, {"tests": [{}, {}]}, {"tests": [{"data": {}}]}]
)
def test_missing_or_ambiguous_report_never_shares_a_history(report, galaxy, tmp_path):
    url, calls, _ = galaxy
    with pytest.raises(ValueError):
        share.share_invocation(report, REQUEST, url, "test-key", tmp_path)
    assert calls == []


@pytest.mark.parametrize(
    "response", [(403, {"err_msg": "forbidden"}), (200, {"importable": False})]
)
def test_failed_sharing_keeps_snapshot_but_does_not_advertise_link(
    response, galaxy, tmp_path
):
    url, _, responses = galaxy
    responses[("PUT", "/api/histories/history1/enable_link_access")] = response
    with pytest.raises((HTTPError, ValueError)):
        share.share_invocation(REPORT, REQUEST, url, "test-key", tmp_path)
    assert json.loads((tmp_path / "invocation.json").read_text()) == INVOCATION
    assert not (tmp_path / "shared-invocation.json").exists()


class GitHub:
    """In-memory API boundary for PR discovery, file matching and comment updates."""

    def __init__(self):
        self.prs = [
            {
                "number": 105,
                "merged_at": "2026-10-26",
                "base": {
                    "ref": "main",
                    "repo": {"full_name": "galaxyproject/idc"},
                },
            }
        ]
        self.files = {105: [{"filename": REQUEST}]}
        self.comments = {105: []}
        self.writes = []

    def __call__(self, endpoint, *, method="GET", payload=None, paginate=False):
        path = endpoint.removeprefix("repos/galaxyproject/idc/").split("?")[0]
        if method == "GET":
            if path == "commits/sha/pulls":
                assert paginate
                return self.prs
            parts = path.split("/")
            number = int(parts[1])
            if path == f"pulls/{number}":
                return next(pr for pr in self.prs if pr["number"] == number)
            assert paginate
            if path == f"pulls/{number}/files":
                return self.files[number]
            if path == f"issues/{number}/comments":
                return self.comments[number]
        self.writes.append((method, path, payload))
        if method == "POST":
            number = int(path.split("/")[1])
            comment = {
                "id": 42,
                "user": {"login": "github-actions[bot]"},
                "body": payload["body"],
                "html_url": f"https://github.com/galaxyproject/idc/pull/{number}#comment42",
            }
            self.comments[number].append(comment)
            return comment
        if method == "PATCH":
            comment = next(
                c
                for comments in self.comments.values()
                for c in comments
                if c["id"] == int(path.split("/")[-1])
            )
            comment["body"] = payload["body"]
            return comment
        raise AssertionError(f"Unexpected GitHub request: {method} {endpoint}")


def record(request=REQUEST, invocation_id="inv1"):
    return {
        "request": request,
        "url": f"https://test.galaxyproject.org/workflows/invocations/{invocation_id}",
    }


def post_records(api, records, **kwds):
    return post.post_links(
        api,
        "galaxyproject/idc",
        "sha",
        "123",
        "https://github.com/galaxyproject/idc/actions/runs/123",
        records,
        **kwds,
    )


def test_comment_contains_only_requests_in_that_pr_and_rerun_updates_it():
    api = GitHub()
    api.comments[105] = [
        {
            "id": 1,
            "user": {"login": "contributor"},
            "body": "<!-- idc-build-invocations:123 -->",
        }
    ]
    assert len(post_records(api, [record(), record("data-managers/other/1.yaml")])) == 1
    method, _, payload = api.writes[-1]
    assert method == "POST"
    assert REQUEST in payload["body"]
    assert "other/1.yaml" not in payload["body"]
    assert (
        "successful submission does not mean the build has finished" in payload["body"]
    )
    post_records(api, [record(invocation_id="inv2")])
    assert api.writes[-1][0:2] == ("PATCH", "issues/comments/42")
    assert "/inv2" in api.writes[-1][2]["body"]
    assert "/inv1" not in api.writes[-1][2]["body"]
    assert api.comments[105][0]["body"] == "<!-- idc-build-invocations:123 -->"


def test_multiple_request_prs_each_get_their_own_links():
    api = GitHub()
    api.prs.append({**api.prs[0], "number": 107})
    api.files[107] = [{"filename": "data-managers/other/1.yaml"}]
    api.comments[107] = []
    assert (
        len(post_records(api, [record(), record("data-managers/other/1.yaml", "inv2")]))
        == 2
    )
    assert "/inv1" in api.comments[105][0]["body"]
    assert "/inv2" not in api.comments[105][0]["body"]
    assert "/inv2" in api.comments[107][0]["body"]
    assert "/inv1" not in api.comments[107][0]["body"]


@pytest.mark.parametrize(
    "unrelated", ["unmerged", "other-base", "other-repo", "other-file", "no-pr"]
)
def test_does_not_comment_on_unrelated_prs(unrelated):
    api = GitHub()
    if unrelated == "unmerged":
        api.prs[0]["merged_at"] = None
    elif unrelated == "other-base":
        api.prs[0]["base"]["ref"] = "other"
    elif unrelated == "other-repo":
        api.prs[0]["base"]["repo"]["full_name"] = "fork/idc"
    elif unrelated == "other-file":
        api.files[105] = [{"filename": "docs/index.md"}]
    else:
        api.prs = []
    assert post_records(api, [record()]) == []
    assert api.writes == []


def test_manual_rebuild_posts_to_explicit_request_pr():
    api = GitHub()
    assert len(post_records(api, [record()], pull_request=105)) == 1
    with pytest.raises(ValueError, match="contain one of the built requests"):
        post_records(api, [record("data-managers/other/1.yaml")], pull_request=105)


def test_no_builds_requires_no_github_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert post.main(["--build-dir", str(tmp_path)]) == 0
    assert "No shared invocations" in capsys.readouterr().out
