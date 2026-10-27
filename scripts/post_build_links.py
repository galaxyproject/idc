#!/usr/bin/env python3
"""Post shared Galaxy invocation links to the PRs associated with this build."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def github_api(
    endpoint: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    paginate: bool = False,
):
    command = ["gh", "api", endpoint, "--method", method]
    if paginate:
        command += ["--paginate", "--slurp"]
    if payload is not None:
        command += ["--input", "-"]
    result = subprocess.run(
        command,
        input=json.dumps(payload) if payload is not None else None,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    )
    data = json.loads(result.stdout)
    return [item for page in data for item in page] if paginate else data


def post_links(
    api,
    repository: str,
    sha: str,
    run_id: str,
    run_url: str,
    records: list[dict],
    pull_request: int | None = None,
) -> list[str]:
    if not records:
        return []
    base = f"repos/{repository}"
    if pull_request is not None:
        prs = [api(f"{base}/pulls/{pull_request}")]
    else:
        prs = api(f"{base}/commits/{sha}/pulls?per_page=100", paginate=True)
    marker = f"<!-- idc-build-invocations:{run_id} -->"
    urls = []
    for pr in prs:
        if (
            not pr.get("merged_at")
            or pr["base"]["ref"] != "main"
            or pr["base"]["repo"]["full_name"] != repository
        ):
            continue
        number = pr["number"]
        files = api(f"{base}/pulls/{number}/files?per_page=100", paginate=True)
        paths = {file["filename"] for file in files}
        relevant = [record for record in records if record["request"] in paths]
        if not relevant:
            continue
        body = marker + "\nGalaxy reference-data builds were submitted:\n\n"
        body += "\n".join(
            f"- `{record['request']}`: [View invocation]({record['url']})"
            for record in relevant
        )
        body += (
            "\n\nThe workflow and history are accessible via these links. "
            "Follow each invocation for current job status and outputs; "
            "successful submission does not mean the build has finished."
            f"\n\n[Actions run]({run_url})"
        )
        comments = api(f"{base}/issues/{number}/comments?per_page=100", paginate=True)
        existing = next(
            (
                comment
                for comment in comments
                if comment.get("user", {}).get("login") == "github-actions[bot]"
                and comment.get("body", "").startswith(marker)
            ),
            None,
        )
        if existing:
            result = api(
                f"{base}/issues/comments/{existing['id']}",
                method="PATCH",
                payload={"body": body},
            )
        else:
            result = api(
                f"{base}/issues/{number}/comments",
                method="POST",
                payload={"body": body},
            )
        urls.append(result["html_url"])
    if pull_request is not None and not urls:
        raise ValueError(
            f"PR #{pull_request} must be merged into main and contain one of the built requests"
        )
    return urls


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=Path("build"))
    parser.add_argument(
        "--pull-request", type=int, help="Request PR for a manual rebuild"
    )
    args = parser.parse_args(argv)
    try:
        records = [
            json.loads(path.read_text())
            for path in sorted(args.build_dir.glob("*/*/shared-invocation.json"))
        ]
        if not records:
            print("No shared invocations to report.")
            return 0
        repository = os.environ["GITHUB_REPOSITORY"]
        run_id = os.environ["GITHUB_RUN_ID"]
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        run_url = f"{server}/{repository}/actions/runs/{run_id}"
        urls = post_links(
            github_api,
            repository,
            os.environ["GITHUB_SHA"],
            run_id,
            run_url,
            records,
            args.pull_request,
        )
        for url in urls:
            print(url)
        if not urls:
            print(
                "No associated merged request PR; invocation links are in the Actions summary."
            )
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"error: cannot post build links: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
