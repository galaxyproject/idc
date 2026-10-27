#!/usr/bin/env python3
"""Share the exact invocation recorded by a Planemo asynchronous run."""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path


def invocation_id_from_report(report: dict) -> str:
    tests = report.get("tests", [])
    if len(tests) != 1:
        raise ValueError("Expected one workflow run in the Planemo report")
    details = tests[0].get("data", {}).get("invocation_details", {}).get("details", {})
    invocation_id = details.get("invocation_id")
    if not invocation_id:
        raise ValueError("Planemo report has no invocation ID; no build will be shared")
    return invocation_id


def share_invocation(
    report: dict, request: str, galaxy_url: str, api_key: str, outdir: Path
) -> dict:
    galaxy_url = galaxy_url.rstrip("/")

    def api(method: str, path: str) -> dict:
        req = urllib.request.Request(
            f"{galaxy_url}/api/{path}",
            method=method,
            headers={"x-api-key": api_key, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)

    def encoded(value: str) -> str:
        return urllib.parse.quote(value, safe="")

    invocation_id = invocation_id_from_report(report)
    invocation = api("GET", f"invocations/{encoded(invocation_id)}")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "invocation.json").write_text(json.dumps(invocation, indent=2) + "\n")

    # An invocation refers to a Workflow revision; sharing needs its StoredWorkflow ID.
    workflow = api(
        "GET", f"workflows/{encoded(invocation['workflow_id'])}?instance=true"
    )
    for resource, item_id in (
        ("workflows", workflow["id"]),
        ("histories", invocation["history_id"]),
    ):
        shared = api("PUT", f"{resource}/{encoded(item_id)}/enable_link_access")
        if not shared.get("importable"):
            raise ValueError(
                f"Galaxy did not enable link access for {resource}/{item_id}"
            )

    record = {
        "request": request,
        "invocation_id": invocation_id,
        "history_id": invocation["history_id"],
        "workflow_id": workflow["id"],
        "url": f"{galaxy_url}/workflows/invocations/{encoded(invocation_id)}",
    }
    (outdir / "shared-invocation.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report", type=Path, required=True, help="Planemo --test_output_json report"
    )
    parser.add_argument(
        "--request", required=True, help="Repository-relative request YAML path"
    )
    parser.add_argument("--galaxy-url", default="https://test.galaxyproject.org")
    parser.add_argument("--outdir", type=Path, required=True)
    args = parser.parse_args(argv)
    api_key = os.environ.get("GALAXY_API_KEY")
    if not api_key:
        parser.error("GALAXY_API_KEY must be set")
    try:
        record = share_invocation(
            json.loads(args.report.read_text()),
            args.request,
            args.galaxy_url,
            api_key,
            args.outdir,
        )
        line = f"- `{record['request']}`: [View Galaxy invocation]({record['url']})\n"
        print(line, end="")
        if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(summary, "a") as handle:
                handle.write(line)
                handle.write(
                    "\nBuild submitted; follow the invocation for job status and outputs.\n\n"
                )
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: cannot share build invocation: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
