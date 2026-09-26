---
name: check-reference-data-request
description: Find out where a Galaxy IDC reference-data request is in the pipeline - PR lint status, whether the merge started a build on test.galaxyproject.org (history idc-<table>-<version>), whether the bundle looks right, whether it has been published to CVMFS and is visible in the data table API. Use when someone asks about the status of an IDC request, PR or reference database version, or why a requested database isn't showing up in Galaxy.
---

# Check an IDC reference-data request

A request (`data-managers/<table>/<version>.yaml`) goes: PR lint → merge →
build on test.galaxyproject.org → (maintainer) bundle check → publish to CVMFS
→ Stratum 1 snapshot → data table reload → visible. This skill works out which
of those it has reached and, if it's stuck, why. Background is in
`docs/requesting-reference-data.md` ("After the merge" and "Checking on a
request").

Ground rules:

- The requester path needs **no secrets**: GitHub (via `gh`, logged in as any
  user, or the web UI) and the public Galaxy data table API. Step 4 has an
  optional part that needs the build account's Galaxy key; only do it if the
  user is a maintainer and has the key in their environment. Never ask for a
  key to be pasted into the conversation.
- Use the repo's scripts for the checks they cover (`check_data_exists.py`,
  `get_bundle_urls.py`); don't re-implement them.
- Report what you found at each stage with the evidence (run URL, table row),
  and stop at the first stage that isn't done.

## 1. Identify the request

From a PR number:

```bash
gh pr view <n> --repo galaxyproject/idc --json state,isDraft,mergedAt,mergeCommit,files \
  --jq '{state, isDraft, mergedAt, merge: .mergeCommit.oid, files: [.files[].path | select(startswith("data-managers/"))]}'
```

Each file under `data-managers/<table>/<version>.yaml` is one request; check each.
From a table and version, the file is `data-managers/<table>/<version>.yaml`
on `main` (or in the PR). Note whether the request has `depends_on`: a chained
build also produces its upstream, unless the upstream was already served.

## 2. Lint (open PR)

```bash
gh pr checks <n> --repo galaxyproject/idc
```

Failing "Lint reference-data requests": read the failed step's log

```bash
gh run view <run id> --repo galaxyproject/idc --log-failed
```

and map the message to the guide's "Lint errors" section. To
reproduce locally from a checkout of the PR branch, run the same commands as
the `request-reference-data` skill's step 7. A `::warning::` that the data
"already exists" doesn't fail the lint but means the build will skip it: say
so. An unmerged, green PR is waiting for review; that's the status.

## 3. Build (after the merge)

The build workflow runs on the push that merged the request:

```bash
gh run list --repo galaxyproject/idc --workflow build.yml --limit 20 \
  --json databaseId,headSha,status,conclusion,createdAt,event,url
```

Pick the run whose `headSha` is the merge commit (or a later
`workflow_dispatch` run, which maintainers use to re-run one request), then:

```bash
gh run view <run id> --repo galaxyproject/idc --log | grep -E "Requests to build|skip |cannot tell|Building |Nothing to build|planemo|rror"
```

- `skip <table>/<version>: already exists on https://test.galaxyproject.org`:
  the data was already served when the build ran. Nothing was built; go to
  step 6 to confirm it's visible.
- `cannot tell whether this already exists`: test didn't answer, so the build
  step failed rather than guess. A maintainer re-runs it.
- `Building <table>/<version>` followed by planemo output and a green run: the
  workflow was *scheduled* on test in history `idc-<table>-<version>`. The run
  finishing says nothing about the build itself, which takes minutes to hours
  (SameStr from MetaPhlAn took about 5.5 hours).
- A red run: report the failing step and its error.

## 4. Build history and bundle (maintainers, needs the build account's key)

The history `idc-<table>-<version>` belongs to the build account and isn't
public, so a requester can't see its state; tell them to ask on the PR, and
skip to step 5. With the key in the environment (`$TEST_GALAXY_KEY` here):

```bash
curl -s -H "x-api-key: $TEST_GALAXY_KEY" \
  "https://test.galaxyproject.org/api/histories?q=name&qv=idc-<table>-<version>&view=detailed&keys=id,name,update_time,state,state_details"
EPHEMERIS_API_KEY="$TEST_GALAXY_KEY" python scripts/get_bundle_urls.py \
  -g https://test.galaxyproject.org --history-name idc-<table>-<version>
```

`get_bundle_urls.py` needs `bioblend` (`pip install bioblend`) and prints one
bundle URL per data manager step of the newest invocation, in the form
`.../api/datasets/<id>/display?to_ext=data_manager_json`. For each `<id>`, read
the bundle index (no key needed for the download itself):

```bash
curl -s "https://test.galaxyproject.org/api/datasets/<id>/display?filename=_gx_data_bundle_index.json" | python3 -m json.tool
```

Check that every `path` is **relative** and that the `value` is the one
expected (e.g. a pinned `db_value`). Don't judge the bundle by
`display?preview=true`: that shows the data manager's primary `.dat` output,
which has absolute job paths and says nothing about the bundle. A history
with several invocations (re-runs) is resolved to the newest one, which is also
what the publish uses.

## 5. Published?

Publishing is a manual maintainer action (*Actions → Publish reference data to
CVMFS*):

```bash
gh run list --repo galaxyproject/idc --workflow deploy.yml --limit 10 \
  --json databaseId,status,conclusion,createdAt,url
```

A run after the build finished, with `publish: true` and a green conclusion,
has published everything that was ready. Its log says what happened to each
request:

```bash
gh run view <run id> --repo galaxyproject/idc --log | grep -E "rehearsal|Recorded import|Already imported|No bundles to import|# skip|# import"
```

**First rule out a rehearsal.** A `publish: false` run imports too, and prints
`Recorded import` before the transaction is aborted; it logs
`PUBLISH=false: importing into a transaction that will be aborted (rehearsal)`,
and its job summary says `published: false`. Only a run without that line (and
`published: true` in its summary) published anything.

`Recorded import: .../record/<table>/<version>` means it was published by that
run; `Already imported` means an earlier publish did; `No bundles to import`
means there was no build history for it (the build skipped it). Only a maintainer can start
one; if the build has been green for a while and nothing ran, that's the
status: "built, waiting for a publish".

## 6. Visible in Galaxy?

```bash
python scripts/check_data_exists.py --expect-exists data-managers/<table>/<version>.yaml
curl -s https://test.galaxyproject.org/api/tool_data/<table> | python3 -m json.tool
```

Run it from an up-to-date checkout of `main` (`git pull` first): a file your
checkout doesn't have yet is an error (exit 2, "no such request file"), not a
"yes". Only the `ok: <table>/<version> is present ...` line means the row is
in test's data table; an `::error::` line means it isn't. Show the matching
row, including its `value`.

Published but not visible yet? In order:

1. **Stratum 1 snapshot.** Compare the revision the Stratum 0 publishes with
   what the replicas serve (all public):

   ```bash
   for h in cvmfs0-psu0 cvmfs1-psu0 cvmfs1-iu0 cvmfs1-tacc0; do
     printf '%s ' "$h"; curl -s "http://$h.galaxyproject.org/cvmfs/idc.galaxyproject.org/.cvmfspublished" | sed -n '/^--$/q;/^S/p'
   done
   ```

   A replica behind the Stratum 0 hasn't taken its hourly snapshot yet; wait.
2. **Data table reload.** Galaxy keeps tables in memory. After the snapshot
   (and a few minutes for the client), an admin of that Galaxy has to call
   `GET /api/tool_data/<table>/reload`, or restart it. On test a maintainer
   does it after publishing (the publish workflow may do it once that step
   lands); elsewhere it's the server's admins.
3. **The identity didn't match.** If the row is there but `--expect-exists`
   still fails, the data manager wrote a `value` that doesn't contain the
   request's version, `params` values or `depends_on` versions. Report both;
   that's a request/identity problem for the maintainers, not a propagation
   delay. SameStr built from mOTUs (`samestr_db/marker_db_motus_*`) is a known
   case: its row carries the mOTUs `db_from_...` value, which the check can't
   match. Confirm it by eye from the table
   instead: a row whose `value` is the mOTUs value the chain used.

For a server other than test, repeat step 6 with `--reference-galaxy <url>`;
it must also load `/cvmfs/idc.galaxyproject.org/config/tool_data_table_conf.xml`
(see `docs/using-idc-data.md`).

## Report

One line per request: `<table>/<version>: <stage reached> - <evidence>`, then
what happens next and who does it (requester, reviewer, maintainer, server
admin).
