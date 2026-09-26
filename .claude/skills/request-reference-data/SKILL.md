---
name: request-reference-data
description: Request a new versioned reference-data build from the Galaxy IDC (a data manager database such as a new MetaPhlAn, mOTUs or SameStr version) - check it is not already served, find the data manager's version-pinned tool_id and params, choose the version identity, write data-managers/<table>/<version>.yaml, run the repo's lint and draft the pull request. Use when someone needs a reference database or version on Galaxy servers, or asks to add or fix an IDC request file.
---

# Request reference data from the IDC

Takes a request like "we need MetaPhlAn database mpa_vJan25_CHOCOPhlAnSGB_202503
on Galaxy" to a linted request file and a drafted PR against
`galaxyproject/idc`. The human-facing version of these steps, with background, is
`docs/requesting-reference-data.md`; read it when a step here is unclear, and
treat it and the real files under `data-managers/` as the source of truth.

Ground rules:

- **Use the repo's scripts** (`scripts/request_models.py`,
  `scripts/tool_schemas.py`, `scripts/generate_schema.py`,
  `scripts/generate_build.py`, `scripts/check_data_exists.py`). Don't
  re-implement their checks; if one seems wrong, say so rather than working
  around it.
- **No secrets.** Everything here uses public endpoints: the Galaxy data table
  API and the Tool Shed. Never ask for or use a Galaxy API key on this path.
- **Pushing and opening the PR are the user's call.** Prepare the branch,
  commit and PR text, then ask before `git push` or `gh pr create`.
- **Stop early** when the data already exists or the data manager isn't
  installed on the build Galaxy, and explain what would have to happen instead.

## 0. Gather the request

You need: the database (and the data manager, if the user knows it), the version,
and whether it is built from another database (e.g. SameStr from MetaPhlAn). Also
ask whether another Galaxy (usegalaxy.eu, usegalaxy.org) already has the same
data, since the IDC should reuse its identifier. Don't guess a version the user
didn't give; the tool's select options (step 3) list the valid ones.

Set up once, from the repository root:

```bash
python3 -m venv .venv && . .venv/bin/activate    # or reuse an existing venv
pip install "pydantic>=2" pyyaml jsonschema gxformat2 pytest
```

## 1. Is it already requested or served?

```bash
ls data-managers/*/                                   # existing requests
gh pr list --repo galaxyproject/idc --state open --search "<table or database>"
curl -s https://test.galaxyproject.org/api/tool_data/<table> | python3 -m json.tool
```

- A request file or open PR for the same table and version: stop, point the
  user at it (use the `check-reference-data-request` skill for its status).
- A data table row whose `value` or version column names the version: the data
  is already served (from the IDC or another repository). Stop. Moving
  non-IDC data into the IDC is a maintainer decision, not a request.
- HTTP 404: the table isn't configured on test. Expected for a data manager
  nobody has requested from; continue, but step 2 will need the onboarding path.

If you don't know the table name yet, do step 2 first and come back.

## 2. Find the data manager, its `tool_id` and data table

```bash
grep -n -i "<database or repo name>" schemas/data_managers.yml
```

`schemas/data_managers.yml` lists every data manager installed on
test.galaxyproject.org as full GUIDs
(`toolshed.g2.bx.psu.edu/repos/<owner>/<repo>/<tool id>/<version>`). Use that
GUID as `tool_id` unless the user needs a specific other version; it must stay
version-pinned and on the production Tool Shed (`toolshed.g2.bx.psu.edu`).

Not listed means not installed on the build Galaxy. The request can't build
until the data manager is added to usegalaxy-tools'
`test.galaxyproject.org/data_managers.yml`; see "Adding a brand-new data
manager" in the guide. Tell the user; you can still prepare the idc side
(steps 3 to 7) if they want, and note the dependency in the PR.

The **data table**: if a request directory for this data manager already
exists, that's it. Otherwise read the `<data_table name=...>` entries of the
repository's `data_manager_conf.xml`, found via the Tool Shed:

```bash
curl -s 'https://toolshed.g2.bx.psu.edu/api/repositories?name=<repo>&owner=<owner>' | python3 -m json.tool   # remote_repository_url
```

List every table it writes in `data_tables`; the primary one (the table tools
select the database from) names the directory. Check it is configured for the
IDC:

```bash
grep -n '<table name="<table>"' config/tool_data_table_conf.xml
```

If it isn't, the PR must add it (columns from the data manager's
`tool_data_table_conf.xml.sample`, file
`/cvmfs/idc.galaxyproject.org/config/<table>.loc`,
`allow_duplicate_entries="False"`, like the neighbouring versioned tables).

## 3. Find and fill `params`

```bash
schema="${TMPDIR:-/tmp}/params.schema.json"
python scripts/tool_schemas.py <tool_id> > "$schema"   # the full parameter schema
python3 - <<'PY' < "$schema"
import json, sys
s = json.load(sys.stdin)
for where, node in [("params", s), *s.get("$defs", {}).items()]:
    for name, p in node.get("properties", {}).items():
        opts = [o["value"] for o in p.get("gx_options", [])] or p.get("const", "")
        ref = p.get("$ref", "").rsplit("/", 1)[-1]
        print(f"{where}: {name} [{p.get('gx_type', ref or p.get('type', ''))}] {p.get('title', '')} {opts}")
PY
```

This lists each settable parameter with its type, label and, for selects, the
allowed values; parameters inside a conditional are listed under its
`When_<selector>_<value>` branch. For example, the MetaPhlAn data manager prints
`params: index [gx_select] Version ['mpa_vJan25_CHOCOPhlAnSGB_202503', ...]`,
and SameStr prints its `db_source` conditional with a `database` input on the
`metaphlan` branch and `motus_db` on the `motus` branch. Defaults and help text
are in the JSON. Rules:

- Keys are the tool's `<param name=>`, nested like the tool form
  (`db_source: {db_type: motus}`), never `a|b` paths.
- Set what selects the version (MetaPhlAn `index`, mOTUs `version`, ...). Leave
  out parameters whose default is right.
- **A chained request's upstream branch is not a param.** Don't set the
  conditional that picks the upstream database (SameStr's `db_source`); step 5's
  `depends_on` does that. Such requests usually have `params: {}`.
- **Identifier pinning.** Check what `value` other servers use for the same data:
  `curl -s https://usegalaxy.eu/api/tool_data/<table>` (and usegalaxy.org). If
  the data manager has a parameter that sets the table `value` explicitly
  (mOTUs' `db_value`), set it to that server's identifier so workflows move
  between servers unchanged. If it has none and would write a different
  `value`, mention it in the PR for the reviewers.

## 4. Choose the version identity (file name)

`data-managers/<table>/<version>.yaml`. The `<version>` stem names the build
history (`idc-<table>-<version>`) and the CVMFS record marker, and is what the
existence check looks for in the data table. So:

- use the identity the data manager itself writes (MetaPhlAn index name, mOTUs
  release number); it, a `params` value or a `depends_on` version must appear in
  the row the data manager will write, or the request is never recognised as
  built;
- unversioned upstream data (e.g. "latest nr"): a date stamp, `nr_2026-09-21`,
  with its meaning in `description`;
- derived databases name their source: `marker_db_motus_3.1.0`;
- only letters, digits, `.`, `_`, `-`; unique within the table.

## 5. Chained builds (`depends_on`)

For a database built from another one:

```yaml
depends_on:
  <upstream_table>: "<upstream version>"   # an existing (or added) request's file name
```

```bash
grep -n -A3 '("<table>", "<upstream_table>")' scripts/generate_build.py   # CHAIN_WIRING entry?
ls data-managers/<upstream_table>/                                        # upstream request exists?
```

- No `CHAIN_WIRING` entry for the pair: the PR must add one (the conditional
  selector to bake in, and the `a|b` input receiving the upstream bundle). Read
  the downstream tool's schema (step 3) to find both; ask the user to confirm.
- No upstream request file: add it too, in the same PR, following steps 1 to 4
  for the upstream (it must pass the existence check on its own, unless it's
  already served, in which case its request file still has to exist but the
  build will reference the served entry instead of rebuilding it).

## 6. Write the file

Copy the shape of the closest real request:
`data-managers/motus_db_versioned/3.1.0.yaml` (standalone, with a pinned
`db_value`), `data-managers/metaphlan_database_versioned/mpa_vJan21_CHOCOPhlAnSGB_202103.yaml`
(standalone), `data-managers/samestr_db/marker_db_motus_3.1.0.yaml` (chained).

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/galaxyproject/idc/main/schemas/request.schema.json
# <one line: what this is and where it comes from>
tool_id: toolshed.g2.bx.psu.edu/repos/<owner>/<repo>/<tool id>/<version>
data_tables:
  - <table>
params:
  <param>: "<value>"   # the tool's "<label>" param
description: <what this data is, with the version>
doi: <optional>
```

Only these fields exist: `tool_id`, `data_tables`, `params`, `depends_on`,
`description`, `doi`. Keep the modeline. Quote version-like values
(`"3.1.0"`) so YAML keeps them strings.

## 7. Lint

Run all of these; fix and re-run until they pass:

```bash
python scripts/request_models.py data-managers/<table>/<version>.yaml
python scripts/generate_schema.py --check
python scripts/generate_build.py data-managers/<table>/<version>.yaml --outdir build --reference-galaxy https://test.galaxyproject.org
python scripts/check_data_exists.py data-managers/<table>/<version>.yaml
python -m pytest tests/ -q
```

- `request_models.py` errors map one-to-one to the guide's "FAQ and
  troubleshooting" section; the params messages list the allowed names/values.
- `generate_schema.py --check` reporting "stale": the `tool_id` isn't in the
  committed editor schema. Run `python scripts/generate_schema.py`, check the
  diff only adds that tool, and commit `schemas/request.schema.json` with the
  request.
- `generate_build.py` must succeed (it gxformat2-validates the workflow). Look at
  `build/<table>/<version>/job.yml`: its values are exactly what the build will
  pass to the tool. For a chained request, check whether it included the
  upstream step or referenced an existing entry, and that this is what you
  expect.
- `check_data_exists.py` must print "No requested reference data already
  exists"; "already exists" sends you back to step 1. "cannot tell" means test
  didn't answer: retry later, don't ignore it.
- If you added a `CHAIN_WIRING` entry or other code, also run the full set CI
  runs: `python scripts/request_models.py && python scripts/generate_schema.py --check --refresh && python scripts/generate_build.py --all --outdir build`.

## 8. Commit and draft the PR

```bash
git switch -c request-<table>-<version>
git add data-managers/<table>/<version>.yaml   # + schema / config / CHAIN_WIRING changes
git commit -m "Request <database> <version> (<data manager repo> <tool version>)"
```

Draft the PR description (and show it to the user):

```markdown
Requests <database> <version> for the IDC: `data-managers/<table>/<version>.yaml`.

- **Data:** <what it is, upstream release/DOI, approximate size if known>
- **Why:** <tool/workflow that needs it; which servers>
- **Data manager:** `<tool_id>` (installed on test.galaxyproject.org)
- **Not served yet:** `check_data_exists.py` on test: <output>
- **Identifier:** <the `value` it will write; matches usegalaxy.eu's `<value>` via `<param>` / no other server has it>
- **Chained:** <depends_on and whether the upstream exists on test / is built in this chain>, or n/a
- Local lint: request_models, generate_schema --check, generate_build, pytest all pass.
```

Then ask the user whether to push to their fork and open the PR
(`gh pr create --repo galaxyproject/idc --title ... --body-file ...`). After it's
open, the `check-reference-data-request` skill follows it through the pipeline.

## Done when

- the request file, and any upstream request, schema, table config or
  `CHAIN_WIRING` change it needs, is committed on a branch;
- every command in step 7 passes;
- the PR text is drafted and the user has decided about pushing;
- anything the idc PR can't do itself (installing the data manager on test) is
  spelled out for the user.
