"""Tests for scripts/after_publish.py: no network, a fake clock."""
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

    def reload(table):
        reloads.append(table)
        if len(reloads) == 1:
            raise urllib.error.URLError("transient")

    def fetch(table):
        fetches[table] += 1
        if fetches[table] == 2:
            raise CheckUnavailable("HTTP 502")
        if fetches[table] >= 4:
            return {"columns": ["value", "version", "name", "path"], "fields": rows + [["3.1.0", "3.1.0", "n", "/q"]]}
        return {"columns": ["value", "version", "name", "path"], "fields": rows}

    expected = [ap.Expected("motus_db_versioned", "3.1.0")]
    error = ap.wait_for_galaxy(
        expected, reload=reload, fetch=fetch, now=clock.now, sleep=clock.sleep, interval=60, timeout=1800, log=print
    )
    assert error is None
    assert expected[0].visible_at == start + 180
    assert reloads == ["motus_db_versioned"] * 4


def test_value_that_never_appears_fails_after_the_timeout():
    clock = FakeClock()
    start = clock.t
    expected = [ap.Expected("samestr_db", "present"), ap.Expected("samestr_db", "missing")]

    def fetch(table):
        return {"columns": ["value"], "fields": [["present"], ["missing-but-longer"]]}

    error = ap.wait_for_galaxy(
        expected, reload=None, fetch=fetch, now=clock.now, sleep=clock.sleep, interval=60, timeout=300, log=print
    )
    assert error is None
    assert expected[0].visible_at == start
    assert expected[1].visible_at is None  # a first-column match must be exact
    assert clock.t == start + 300


def test_forbidden_reload_stops_the_galaxy_checks():
    def get(url, headers):
        assert headers == {"x-api-key": "not-admin"}
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    reload = ap.make_reload("https://galaxy.example/", "not-admin", get)
    clock = FakeClock()
    expected = [ap.Expected("motus_db_versioned", "3.1.0")]
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
    mapping = ap.loc_table_map(ap.DEFAULT_TABLE_CONF)
    assert mapping["motus_db_versioned.loc"] == "motus_db_versioned"
    assert mapping["metaphlan_database_versioned.loc"] == "metaphlan_database_versioned"
    assert mapping["samestr_db.loc"] == "samestr_db"
    # File and table names differ for some tables.
    assert mapping["dbkeys.loc"] == "__dbkeys__"
    assert mapping["bowtie2_indices.loc"] == "bowtie2_indexes"


def test_entries_parsing():
    text = "\nmotus_db_versioned.loc\t3.1.0\r\n  \nsamestr_db.loc\tmarker db\nmotus_db_versioned.loc\t3.1.0\n"
    assert ap.parse_entries(text) == {"motus_db_versioned.loc": ["3.1.0"], "samestr_db.loc": ["marker db"]}
    assert ap.parse_entries("") == {}
    with pytest.raises(ValueError, match="line 2"):
        ap.parse_entries("a.loc\tv\nno tab here\n")
    with pytest.raises(ValueError):
        ap.parse_entries("a.loc\t\n")


def _run(tmp_path, entries, get, fetch, extra_args=()):
    clock = FakeClock()
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
    fetch = lambda table: {"columns": ["value"], "fields": [["3.1.0"]]}  # noqa: E731
    rc, summary = _run(tmp_path, "motus_db_versioned.loc\t3.1.0\n", get, fetch)
    assert rc == 0
    assert "Published revision: **21**" in summary
    assert f"| {S1A} | 21 | " in summary and "(after 1m00s)" in summary
    assert "| `motus_db_versioned` | `3.1.0` | " in summary


def test_main_still_checks_galaxy_when_a_stratum1_lags(tmp_path):
    get = _galaxy_get({S0: [21], S1A: [21], S1B: [urllib.error.URLError("down")]})
    fetched = []

    def fetch(table):
        fetched.append(table)
        return {"columns": ["value"], "fields": [["3.1.0"]]}

    rc, summary = _run(tmp_path, "motus_db_versioned.loc\t3.1.0\n", get, fetch, ["--stratum1-timeout", "120"])
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
