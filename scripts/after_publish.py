#!/usr/bin/env python3
"""Wait for a CVMFS publish to reach the Stratum 1s, then check Galaxy sees it.

Runs after ``.github/workflows/deploy.yml`` published a transaction, with the
data table rows it added (the publish job's ``published_entries`` output: one
``<loc file>\\t<value>`` line per row).

1. Read the repository's current revision from the Stratum 0 (the publish
   just made it).
2. Poll every Stratum 1 until it serves that revision or later. The Stratum 1s
   snapshot on their own hourly cron (``cvmfs_server snapshot -a -i`` at :00);
   nothing here triggers a snapshot, so this can take over an hour.
3. Then, until the Galaxy timeout: for every affected data table, ask Galaxy to
   reload it (``GET /api/tool_data/<table>/reload``, admin key) and check that
   every published value is in its first column (``value`` for every reference
   table) of the public ``GET /api/tool_data/<table>``. Galaxy only sees the new
   .loc content once its own CVMFS client picked up the revision (the client's
   TTL runs minutes after the snapshot), hence the retries.

The Galaxy step runs even if a Stratum 1 never caught up - Galaxy may use one
that did - but the script exits 1 if any Stratum 1 lagged or any value never
became visible. A markdown summary goes to ``$GITHUB_STEP_SUMMARY`` when set.

Usage::

    PUBLISHED_ENTRIES="$(printf 'motus_db_versioned.loc\\t3.1.0\\n')" \\
    REFERENCE_DATA_API_KEY=... python scripts/after_publish.py
    python scripts/after_publish.py --entries-file entries.tsv --stratum1-timeout 0
"""
import argparse
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_data_exists import CheckUnavailable, fetch_table  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPO = "idc.galaxyproject.org"
DEFAULT_STRATUM0 = "https://cvmfs0-psu0.galaxyproject.org"
DEFAULT_STRATUM1S = (
    "http://cvmfs1-psu0.galaxyproject.org",
    "http://cvmfs1-iu0.galaxyproject.org",
    "http://cvmfs1-tacc0.galaxyproject.org",
)
DEFAULT_GALAXY = "https://test.galaxyproject.org"
DEFAULT_TABLE_CONF = REPO_ROOT / "config" / "tool_data_table_conf.xml"

HttpGet = Callable[[str, dict], bytes]


def http_get(url: str, headers: dict) -> bytes:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310 (configured hosts)
        return resp.read()


# --- inputs -----------------------------------------------------------------


def parse_entries(text: str) -> dict[str, list[str]]:
    """``<loc file>\\t<value>`` lines -> {loc file: [values]}, in order, deduplicated."""
    entries: dict[str, list[str]] = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        loc, sep, value = line.partition("\t")
        if not sep or not loc.strip() or not value:
            raise ValueError(f"entry line {lineno} is not '<loc file>\\t<value>': {line!r}")
        values = entries.setdefault(loc.strip(), [])
        if value not in values:
            values.append(value)
    return entries


def loc_table_map(conf_path: Path) -> dict[str, str]:
    """{.loc basename: data table name} from a tool_data_table_conf.xml."""
    mapping: dict[str, str] = {}
    for table in ET.parse(conf_path).getroot().iter("table"):
        for file_el in table.iter("file"):
            path = file_el.get("path")
            if path:
                mapping[Path(path).name] = table.get("name")
    return mapping


# --- Stratum 0/1 revisions --------------------------------------------------


def manifest_url(base_url: str, repo: str) -> str:
    return f"{base_url.rstrip('/')}/cvmfs/{repo}/.cvmfspublished"


def parse_manifest_revision(data: bytes) -> int:
    """The ``S<revision>`` line of a .cvmfspublished manifest.

    The manifest is text lines up to a ``--`` line; the signature after it is
    binary, so it is never decoded.
    """
    head = data.split(b"\n--\n", 1)[0]
    for line in head.decode("utf-8", "replace").splitlines():
        if line.startswith("S") and line[1:].strip().isdigit():
            return int(line[1:])
    raise ValueError("no S<revision> line in the manifest")


def read_revision(get: HttpGet, url: str) -> int:
    # The Stratum 1s serve the manifest through squid (max-age=61): ask for a
    # fresh copy so a poll sees a new snapshot as soon as it lands.
    return parse_manifest_revision(get(url, {"Cache-Control": "no-cache"}))


def read_target_revision(get: HttpGet, url: str, *, attempts: int, sleep, log) -> int | None:
    for attempt in range(1, attempts + 1):
        try:
            return read_revision(get, url)
        except Exception as exc:
            log(f"Stratum 0: cannot read {url} (attempt {attempt}/{attempts}): {exc}")
            if attempt < attempts:
                sleep(10)
    return None


@dataclass
class Stratum1:
    url: str
    revision: int | None = None
    caught_up_at: float | None = None
    error: str | None = None


def wait_for_stratum1s(
    stratum1s: list[str],
    repo: str,
    target: int,
    *,
    get: HttpGet,
    now,
    sleep,
    interval: float,
    timeout: float,
    log,
) -> list[Stratum1]:
    """Poll each Stratum 1 until it serves ``target`` or later, or ``timeout``."""
    status = [Stratum1(url) for url in stratum1s]
    start = now()
    while True:
        for s1 in status:
            if s1.caught_up_at is not None:
                continue
            try:
                s1.revision = read_revision(get, manifest_url(s1.url, repo))
                s1.error = None
            except Exception as exc:  # a poll that fails is retried, not fatal
                s1.error = str(exc)
                log(f"{s1.url}: {exc}")
                continue
            if s1.revision >= target:
                s1.caught_up_at = now()
                log(f"{s1.url}: revision {s1.revision} (caught up after {_duration(s1.caught_up_at - start)})")
        pending = [s1 for s1 in status if s1.caught_up_at is None]
        elapsed = now() - start
        if not pending or elapsed >= timeout:
            return status
        log(
            f"waiting for revision {target} on "
            + ", ".join(f"{s1.url} (at {s1.revision if s1.revision is not None else '?'})" for s1 in pending)
            + f" [{_duration(elapsed)} of {_duration(timeout)}]"
        )
        sleep(min(interval, timeout - elapsed))


# --- Galaxy -----------------------------------------------------------------


class ReloadForbidden(Exception):
    """The API key cannot reload data tables; retrying will not help."""


def make_reload(galaxy_url: str, api_key: str, get: HttpGet = http_get):
    def reload(table: str) -> None:
        url = f"{galaxy_url.rstrip('/')}/api/tool_data/{table}/reload"
        try:
            get(url, {"x-api-key": api_key})
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ReloadForbidden(f"{url}: HTTP {exc.code} {exc.reason}") from exc
            raise

    return reload


@dataclass
class Expected:
    table: str
    value: str
    visible_at: float | None = None


def wait_for_galaxy(
    expected: list[Expected],
    *,
    reload,
    fetch,
    now,
    sleep,
    interval: float,
    timeout: float,
    log,
) -> str | None:
    """Reload and re-check each table until every value is visible, or ``timeout``.

    ``reload(table)`` may be None (no API key): then only the public table is
    polled. Marks ``visible_at`` on each Expected; returns an error that stopped
    the checks early, else None.
    """
    start = now()
    while True:
        tables = sorted({e.table for e in expected if e.visible_at is None})
        for table in tables:
            if reload is not None:
                try:
                    reload(table)
                except ReloadForbidden as exc:
                    log(f"{table}: {exc}")
                    return f"reload refused, check REFERENCE_DATA_API_KEY: {exc}"
                except Exception as exc:
                    log(f"{table}: reload failed, will retry: {exc}")
            try:
                table_data = fetch(table)
            except CheckUnavailable as exc:
                log(f"{table}: {exc}")
                continue
            if table_data is None:
                log(f"{table}: not configured on this Galaxy (404)")
                continue
            values = {str(row[0]) for row in table_data.get("fields", []) if row}
            for e in expected:
                if e.table == table and e.visible_at is None and e.value in values:
                    e.visible_at = now()
                    log(f"{table}: {e.value!r} is visible (after {_duration(e.visible_at - start)})")
        missing = [e for e in expected if e.visible_at is None]
        elapsed = now() - start
        if not missing or elapsed >= timeout:
            return None
        log(
            f"not visible yet: {', '.join(f'{e.table}/{e.value}' for e in missing)} "
            f"[{_duration(elapsed)} of {_duration(timeout)}]"
        )
        sleep(min(interval, timeout - elapsed))


# --- reporting ----------------------------------------------------------------


def _duration(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}m{secs:02d}s"


def _clock(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%H:%M:%S UTC")


def summary_markdown(
    *,
    repo: str,
    target: int | None,
    stratum1s: list[Stratum1],
    s1_start: float,
    s1_timeout: float,
    galaxy_url: str,
    expected: list[Expected],
    unmapped: dict[str, list[str]],
    galaxy_start: float,
    galaxy_timeout: float,
    galaxy_error: str | None,
) -> str:
    lines = [f"## {repo} on the Stratum 1s and {galaxy_url}", ""]
    if target is None:
        lines += ["Could not read the published revision from the Stratum 0; the Stratum 1s were not checked.", ""]
    else:
        lines += [f"Published revision: **{target}**", "", "| Stratum 1 | revision | caught up |", "|---|---|---|"]
        for s1 in stratum1s:
            if s1.caught_up_at is not None:
                state = f"{_clock(s1.caught_up_at)} (after {_duration(s1.caught_up_at - s1_start)})"
            else:
                state = f"**no** (not within {_duration(s1_timeout)})"
                if s1.error:
                    state += f"; last error: {s1.error}"
            revision = s1.revision if s1.revision is not None else "?"
            lines.append(f"| {s1.url} | {revision} | {state} |")
        lines.append("")
    lines += ["| data table | value | visible |", "|---|---|---|"]
    for e in expected:
        if e.visible_at is not None:
            state = f"{_clock(e.visible_at)} (after {_duration(e.visible_at - galaxy_start)})"
        else:
            state = f"**no** (not within {_duration(galaxy_timeout)})"
        lines.append(f"| `{e.table}` | `{e.value}` | {state} |")
    for loc, values in unmapped.items():
        for value in values:
            lines.append(f"| **`{loc}` is in no table** | `{value}` | not checked |")
    if galaxy_error:
        lines += ["", f"Stopped checking {galaxy_url}: {galaxy_error}"]
    return "\n".join(lines) + "\n"


# --- main ---------------------------------------------------------------------


def main(
    argv: list[str] | None = None,
    *,
    get: HttpGet = http_get,
    fetch=None,
    now=time.time,
    sleep=time.sleep,
    environ=os.environ,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--entries-file",
        help="'<loc file>\\t<value>' lines (default: the PUBLISHED_ENTRIES environment variable)",
    )
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--stratum0", default=DEFAULT_STRATUM0, help="Stratum 0 base URL")
    parser.add_argument(
        "--stratum1",
        action="append",
        help=f"Stratum 1 base URL, repeatable (default: {', '.join(DEFAULT_STRATUM1S)})",
    )
    parser.add_argument("--galaxy-url", default=DEFAULT_GALAXY)
    parser.add_argument("--table-conf", type=Path, default=DEFAULT_TABLE_CONF)
    parser.add_argument("--interval", type=float, default=60, help="Seconds between polls")
    parser.add_argument("--stratum1-timeout", type=float, default=90 * 60, help="Seconds to wait for the Stratum 1s")
    parser.add_argument(
        "--galaxy-timeout", type=float, default=30 * 60, help="Seconds to wait for the values on Galaxy"
    )
    args = parser.parse_args(argv)

    def log(message: str) -> None:
        print(message, flush=True)

    text = Path(args.entries_file).read_text() if args.entries_file else environ.get("PUBLISHED_ENTRIES", "")
    try:
        entries = parse_entries(text)
    except ValueError as exc:
        print(f"::error:: {exc}", file=sys.stderr)
        return 2
    if not entries:
        log("No published entries: nothing to wait for.")
        return 0

    loc_tables = loc_table_map(args.table_conf)
    expected = [Expected(loc_tables[loc], v) for loc, values in entries.items() if loc in loc_tables for v in values]
    unmapped = {loc: values for loc, values in entries.items() if loc not in loc_tables}
    for loc in unmapped:
        print(f"::error:: {loc} is not a file of any table in {args.table_conf}; cannot check it", file=sys.stderr)

    # 1-2. Stratum 0 revision, then the Stratum 1s.
    stratum1_urls = args.stratum1 or list(DEFAULT_STRATUM1S)
    target = read_target_revision(get, manifest_url(args.stratum0, args.repo), attempts=5, sleep=sleep, log=log)
    s1_start = now()
    stratum1s = [Stratum1(url) for url in stratum1_urls]
    if target is None:
        print("::error:: could not read the published revision from the Stratum 0", file=sys.stderr)
    else:
        log(f"Stratum 0: {args.repo} is at revision {target}")
        stratum1s = wait_for_stratum1s(
            stratum1_urls,
            args.repo,
            target,
            get=get,
            now=now,
            sleep=sleep,
            interval=args.interval,
            timeout=args.stratum1_timeout,
            log=log,
        )
    lagging = [s1 for s1 in stratum1s if s1.caught_up_at is None] if target is not None else []
    for s1 in lagging:
        print(f"::error:: {s1.url} did not reach revision {target}", file=sys.stderr)

    # 3. Reload and verify on Galaxy, whether or not every Stratum 1 caught up.
    api_key = environ.get("REFERENCE_DATA_API_KEY")
    if not api_key:
        print(
            "::warning:: REFERENCE_DATA_API_KEY is not set: polling the tables without reloading them",
            file=sys.stderr,
        )
    reload = make_reload(args.galaxy_url, api_key, get) if api_key else None
    galaxy_start = now()
    galaxy_error = wait_for_galaxy(
        expected,
        reload=reload,
        fetch=fetch or (lambda table: fetch_table(args.galaxy_url, table)),
        now=now,
        sleep=sleep,
        interval=args.interval,
        timeout=args.galaxy_timeout,
        log=log,
    )
    invisible = [e for e in expected if e.visible_at is None]
    for e in invisible:
        print(f"::error:: {e.table}: {e.value!r} is not visible on {args.galaxy_url}", file=sys.stderr)

    summary = summary_markdown(
        repo=args.repo,
        target=target,
        stratum1s=stratum1s,
        s1_start=s1_start,
        s1_timeout=args.stratum1_timeout,
        galaxy_url=args.galaxy_url,
        expected=expected,
        unmapped=unmapped,
        galaxy_start=galaxy_start,
        galaxy_timeout=args.galaxy_timeout,
        galaxy_error=galaxy_error,
    )
    if environ.get("GITHUB_STEP_SUMMARY"):
        with open(environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(summary)
    ok = target is not None and not lagging and not invisible and not unmapped and galaxy_error is None
    log("All Stratum 1s caught up and every value is visible." if ok else "Not everything arrived; see above.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
