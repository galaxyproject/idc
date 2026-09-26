"""Tests for scripts/after_publish.py: no network, a fake clock."""
import json
import sys
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import after_publish as ap  # noqa: E402
from check_data_exists import CheckUnavailable  # noqa: E402

REPO = "idc.galaxyproject.org"
S0 = "https://s0.example"
S1A = "http://s1a.example"
S1B = "http://s1b.example"
MOTUS_COLUMNS = ["value", "version", "name", "path"]
MOTUS_ROW = ["3.1.0", "3.1.0", "mOTUs 3.1.0", "/cvmfs/idc.galaxyproject.org/data/motus/3.1.0"]
MOTUS_ENTRY = "motus_db_versioned.loc\t" + "\t".join(MOTUS_ROW) + "\n"


def motus(row=MOTUS_ROW):
    return ap.Expected("motus_db_versioned", list(row), row[0])


def manifest(revision: int) -> bytes:
    return (
        f"C600333530b8e2b6d2c8c3f7d1a1a3d7b0c1a2b3\nB1234\nRd41d8cd98f00b204e9800998ecf8427e\n"
        f"S{revision}\nT1790000000\nN{REPO}\n--\n"
    ).encode() + b"3f786850e387550fdab836ed7e6dc881de23001b\n\x00\xff\xfe binary signature"


class FakeClock:
    def __init__(self):
        self.t = 1_790_000_000.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def fake_get(revisions: dict[str, list]):
    """Serve each server's manifests in turn (the last one repeats); an
    Exception instance in the list is raised instead."""
    calls: dict[str, int] = {}

    def get(url: str, headers: dict) -> bytes:
        base = url.split("/cvmfs/")[0]
        assert url == ap.manifest_url(base, REPO)
        seq = revisions[base]
        i = calls.get(base, 0)
        calls[base] = i + 1
        item = seq[min(i, len(seq) - 1)]
        if isinstance(item, Exception):
            raise item
        return manifest(item)

    get.calls = calls
    return get


def test_manifest_revision_is_read_before_the_binary_signature():
    assert ap.parse_manifest_revision(manifest(20)) == 20
    assert ap.parse_manifest_revision(b"Cabc\nS7\nT1\n") == 7  # no signature at all
    with pytest.raises(ValueError):
        ap.parse_manifest_revision(b"Cabc\nT1\n--\nS99\n")  # S after '--' is signature


def test_manifest_url():
    assert ap.manifest_url("http://cvmfs1-psu0.galaxyproject.org/", REPO) == (
        "http://cvmfs1-psu0.galaxyproject.org/cvmfs/idc.galaxyproject.org/.cvmfspublished"
    )


def test_stratum1s_are_polled_until_each_catches_up():
    clock = FakeClock()
    start = clock.t
    get = fake_get({S1A: [21], S1B: [20, urllib.error.URLError("reset"), 20, 21]})
    status = ap.wait_for_stratum1s(
        [S1A, S1B], REPO, 21, get=get, now=clock.now, sleep=clock.sleep, interval=60, timeout=3600, log=print
    )
    a, b = status
    assert (a.revision, a.caught_up_at) == (21, start)
    # Polled at 0, 60 (transient error, retried), 120, then caught up at 180.
    assert (b.revision, b.caught_up_at, b.error) == (21, start + 180, None)
    assert get.calls == {S1A: 1, S1B: 4}  # a caught-up Stratum 1 is not polled again


def test_stratum1_wait_times_out_with_one_lagging():
    clock = FakeClock()
    start = clock.t
    get = fake_get({S1A: [20, 21], S1B: [20]})
    status = ap.wait_for_stratum1s(
        [S1A, S1B], REPO, 21, get=get, now=clock.now, sleep=clock.sleep, interval=60, timeout=150, log=print
    )
    a, b = status
    assert a.caught_up_at == start + 60
    assert (b.revision, b.caught_up_at) == (20, None)
    assert clock.sleeps == [60, 60, 30]  # the last sleep ends exactly at the timeout
    assert clock.t == start + 150


def test_value_becomes_visible_after_reload_retries():
    clock = FakeClock()
    start = clock.t
    reloads = []
    fetches = {"motus_db_versioned": 0}
    rows = [["3.0.1", "3.0.1", "old", "/p"]]
    new = ["3.1.0", "3.1.0", "n", "/q"]

    def reload(table):
        reloads.append(table)
        if len(reloads) == 1:
            raise urllib.error.URLError("transient")

    def fetch(table):
        fetches[table] += 1
        if fetches[table] == 2:
            raise CheckUnavailable("HTTP 502")
        if fetches[table] >= 4:
            return {"columns": MOTUS_COLUMNS, "fields": rows + [new]}
        return {"columns": MOTUS_COLUMNS, "fields": rows}

    expected = [motus(new)]
    error = ap.wait_for_galaxy(
        expected, reload=reload, fetch=fetch, now=clock.now, sleep=clock.sleep, interval=60, timeout=1800, log=print
    )
    assert error is None
    assert expected[0].visible_at == start + 180
    assert reloads == ["motus_db_versioned"] * 4


def test_value_that_never_appears_fails_after_the_timeout():
    clock = FakeClock()
    start = clock.t
    expected = [ap.Expected("samestr_db", ["present"], "present"), ap.Expected("samestr_db", ["missing"], "missing")]

    def fetch(table):
        return {"columns": ["value"], "fields": [["present"], ["missing-but-longer"]]}

    error = ap.wait_for_galaxy(
        expected, reload=None, fetch=fetch, now=clock.now, sleep=clock.sleep, interval=60, timeout=300, log=print
    )
    assert error is None
    assert expected[0].visible_at == start
    assert expected[1].visible_at is None  # the match must be exact
    assert clock.t == start + 300


def test_whole_row_is_compared_whichever_column_is_the_value():
    # alignseq_seq: `type` comes first and is 'seq' on every row already there.
    clock = FakeClock()
    table = ap.loc_table_map(ap.DEFAULT_TABLE_CONF)["alignseq.loc"]
    assert table.columns == ["type", "value", "path"]
    expected = [ap.expected_row(table, "seq\tnewgenome\t/cvmfs/idc.galaxyproject.org/data/newgenome.2bit")]
    assert expected[0].value == "newgenome"
    existing = {"columns": table.columns, "fields": [["seq", "dm6", "/cvmfs/idc.galaxyproject.org/data/dm6.2bit"]]}
    ap.wait_for_galaxy(
        expected, reload=None, fetch=lambda t: existing, now=clock.now, sleep=clock.sleep,
        interval=60, timeout=120, log=print,
    )
    assert expected[0].visible_at is None


def test_changed_row_counts_only_once_galaxy_has_the_new_path():
    clock = FakeClock()
    start = clock.t
    edited = MOTUS_ROW[:3] + ["/cvmfs/idc.galaxyproject.org/data/motus/3.1.0-fixed"]
    served = [{"columns": MOTUS_COLUMNS, "fields": [MOTUS_ROW]}] * 2 + [{"columns": MOTUS_COLUMNS, "fields": [edited]}]
    expected = [motus(edited)]
    ap.wait_for_galaxy(
        expected, reload=None, fetch=lambda t: served.pop(0) if len(served) > 1 else served[0],
        now=clock.now, sleep=clock.sleep, interval=60, timeout=600, log=print,
    )
    assert expected[0].visible_at == start + 120


def test_public_view_compares_the_basename_of_the_path_column():
    # What test.galaxyproject.org serves without an admin key (ToolDataManager.show).
    public = {"columns": MOTUS_COLUMNS, "fields": [MOTUS_ROW[:3] + ["3.1.0"]]}
    for is_public, visible in ((True, True), (False, False)):
        clock = FakeClock()
        expected = [motus()]
        ap.wait_for_galaxy(
            expected, reload=None, fetch=lambda t: public, now=clock.now, sleep=clock.sleep,
            interval=60, timeout=0, log=print, public=is_public,
        )
        assert (expected[0].visible_at is not None) is visible
    assert ap.public_view(["a", "b"], ["value", "name"]) == ["a", "b"]


def test_table_missing_on_galaxy_fails_at_once():
    clock = FakeClock()
    fetched = []

    def fetch(table):
        fetched.append(table)
        return None if table == "samestr_db" else {"columns": MOTUS_COLUMNS, "fields": []}

    expected = [motus(), ap.Expected("samestr_db", ["x"], "x")]
    ap.wait_for_galaxy(
        expected, reload=None, fetch=fetch, now=clock.now, sleep=clock.sleep, interval=60, timeout=180, log=print
    )
    assert expected[1].error == "data table not configured on this Galaxy" and expected[1].visible_at is None
    assert fetched == ["motus_db_versioned", "samestr_db"] + ["motus_db_versioned"] * 3


def test_forbidden_reload_stops_the_galaxy_checks():
    def get(url, headers):
        assert headers == {"x-api-key": "not-admin"}
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    reload = ap.make_reload("https://galaxy.example/", "not-admin", get)
    clock = FakeClock()
    expected = [motus()]
    error = ap.wait_for_galaxy(
        expected,
        reload=reload,
        fetch=lambda table: pytest.fail("no point fetching"),
        now=clock.now,
        sleep=clock.sleep,
        interval=60,
        timeout=1800,
        log=print,
    )
    assert "HTTP 403" in error and "https://galaxy.example/api/tool_data/motus_db_versioned/reload" in error
    assert clock.sleeps == []


def test_loc_files_map_to_tables_in_the_real_table_conf():
    mapping = {loc: table.name for loc, table in ap.loc_table_map(ap.DEFAULT_TABLE_CONF).items()}
    assert mapping["motus_db_versioned.loc"] == "motus_db_versioned"
    assert mapping["metaphlan_database_versioned.loc"] == "metaphlan_database_versioned"
    assert mapping["samestr_db.loc"] == "samestr_db"
    # File and table names differ for some tables.
    assert mapping["dbkeys.loc"] == "__dbkeys__"
    assert mapping["bowtie2_indices.loc"] == "bowtie2_indexes"
    assert ap.loc_table_map(ap.DEFAULT_TABLE_CONF)["motus_db_versioned.loc"].columns == MOTUS_COLUMNS


def test_entries_parsing():
    text = (
        "\nmotus_db_versioned.loc\t3.1.0\t3.1.0\tmOTUs\t/p\r\n  \n"
        "samestr_db.loc\tmarker db\tname\n"
        "motus_db_versioned.loc\t3.1.0\t3.1.0\tmOTUs\t/p\n"
    )
    # Only the first tab ends the .loc name; the row keeps its own tabs.
    assert ap.parse_entries(text) == {
        "motus_db_versioned.loc": ["3.1.0\t3.1.0\tmOTUs\t/p"],
        "samestr_db.loc": ["marker db\tname"],
    }
    assert ap.parse_entries("") == {}
    with pytest.raises(ValueError, match="line 2"):
        ap.parse_entries("a.loc\tv\nno tab here\n")
    with pytest.raises(ValueError):
        ap.parse_entries("a.loc\t\n")


def _run(tmp_path, entries, get, fetch, extra_args=(), clock=None):
    clock = clock or FakeClock()
    summary = tmp_path / "summary.md"
    env = {"PUBLISHED_ENTRIES": entries, "REFERENCE_DATA_API_KEY": "key", "GITHUB_STEP_SUMMARY": str(summary)}
    rc = ap.main(
        ["--stratum0", S0, "--stratum1", S1A, "--stratum1", S1B, "--galaxy-url", "https://galaxy.example", *extra_args],
        get=get,
        fetch=fetch,
        now=clock.now,
        sleep=clock.sleep,
        environ=env,
    )
    return rc, summary.read_text() if summary.exists() else ""


def _galaxy_get(revisions):
    manifests = fake_get(revisions)

    def get(url, headers):
        if url.startswith("https://galaxy.example/api/tool_data/"):
            assert url.endswith("/reload") and headers == {"x-api-key": "key"}
            return b"{}"
        return manifests(url, headers)

    return get


def test_main_waits_then_verifies_and_summarizes(tmp_path):
    get = _galaxy_get({S0: [21], S1A: [20, 21], S1B: [21]})
    fetch = lambda table: {"columns": MOTUS_COLUMNS, "fields": [MOTUS_ROW]}  # noqa: E731
    rc, summary = _run(tmp_path, MOTUS_ENTRY, get, fetch)
    assert rc == 0
    assert "Published revision: **21**" in summary
    assert f"| {S1A} | 21 | " in summary and "(after 1m00s)" in summary
    assert "| `motus_db_versioned` | `3.1.0` | " in summary


def test_main_still_checks_galaxy_when_a_stratum1_lags(tmp_path):
    get = _galaxy_get({S0: [21], S1A: [21], S1B: [urllib.error.URLError("down")]})
    fetched = []

    def fetch(table):
        fetched.append(table)
        return {"columns": MOTUS_COLUMNS, "fields": [MOTUS_ROW]}

    rc, summary = _run(tmp_path, MOTUS_ENTRY, get, fetch, ["--stratum1-timeout", "120"])
    assert rc == 1
    assert fetched == ["motus_db_versioned"]
    assert f"| {S1B} | ? | **no** (not within 2m00s); last error: " in summary


def test_main_fails_for_a_loc_file_in_no_table(tmp_path):
    get = _galaxy_get({S0: [21], S1A: [21], S1B: [21]})
    rc, summary = _run(tmp_path, "nowhere.loc\tx\n", get, lambda table: pytest.fail("nothing to fetch"))
    assert rc == 1
    assert "`nowhere.loc` is in no table" in summary


def test_main_with_nothing_published_does_nothing(tmp_path):
    rc, summary = _run(tmp_path, "\n", lambda url, headers: pytest.fail("no requests"), None)
    assert (rc, summary) == (0, "")


def test_main_gives_galaxy_both_timeouts_when_the_stratum0_is_unreachable(tmp_path):
    clock = FakeClock()
    start = clock.t
    get = _galaxy_get({S0: [urllib.error.URLError("no route")], S1A: [21], S1B: [21]})
    rc, summary = _run(
        tmp_path, MOTUS_ENTRY, get, lambda table: {"columns": MOTUS_COLUMNS, "fields": []},
        ["--stratum1-timeout", "600", "--galaxy-timeout", "300"], clock=clock,
    )
    assert rc == 1
    assert "Could not read the published revision from the Stratum 0" in summary
    assert "**no** (not within 15m00s)" in summary
    assert clock.t == start + 4 * 10 + 900  # 5 attempts 10s apart, then 600 + 300 for Galaxy


def test_main_reads_entries_from_a_file_and_fetches_with_the_key(tmp_path, monkeypatch):
    entries = tmp_path / "entries.tsv"
    entries.write_text(MOTUS_ENTRY)
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *args):
            return json.dumps({"columns": MOTUS_COLUMNS, "fields": [MOTUS_ROW]}).encode()

    def urlopen(request, timeout):
        requests.append(request)
        return Response()

    monkeypatch.setattr(ap.fetch_table.__globals__["urllib"].request, "urlopen", urlopen)
    get = _galaxy_get({S0: [21], S1A: [21], S1B: [21]})
    rc, summary = _run(tmp_path, "", get, None, ["--entries-file", str(entries)])
    assert rc == 0, summary
    # The admin key gets Galaxy's full `path` column, so the whole row matches.
    assert [(r.full_url, r.get_header("X-api-key")) for r in requests] == [
        ("https://galaxy.example/api/tool_data/motus_db_versioned", "key")
    ]
