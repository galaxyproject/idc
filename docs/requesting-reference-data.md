# Requesting reference data

Everything the IDC builds, from a MetaPhlAn database to a genome and its
Bowtie2 index, is produced by a Galaxy data manager and requested the same way:
a pull request that adds one YAML file describing which data manager to run and
with which parameters. The file is linted on the PR and, once merged, built on
[test.galaxyproject.org](https://test.galaxyproject.org) as a data-manager bundle
workflow. A maintainer then publishes the result to the `idc.galaxyproject.org`
CVMFS repository, where any Galaxy server that mounts it can use it.

This page covers writing that file, checking it locally, and following it
through the pipeline afterwards. When in doubt, the request files already under
[`data-managers/`](https://github.com/galaxyproject/idc/tree/main/data-managers)
show what works. If you use a coding agent, the repository has
[skills](#using-an-agent) for the same steps.

## Before you start: does it already exist?

Don't request data that a Galaxy already serves. Whether it does is answered by
the build Galaxy's public data table API, which lists what test.galaxyproject.org
can use from every source it loads, the IDC included. There is no separate list
of published versions in this repository.

```bash
curl -s https://test.galaxyproject.org/api/tool_data/metaphlan_database_versioned
```

```json
{"name": "metaphlan_database_versioned",
 "columns": ["value", "name", "dbkey", "path", "db_version"],
 "fields": [["mpa_vOct22_CHOCOPhlAnSGB_202212-03042023", "MetaPhlAn clade-specific marker genes (mpa_vOct22_CHOCOPhlAnSGB_202212)", "mpa_vOct22_CHOCOPhlAnSGB_202212", "...", "SGB"],
            ...]}
```

If one of the rows has a field equal to your version (for MetaPhlAn that's
`dbkey`, for mOTUs `version`), or a `value` that is your version followed by
`-<date>`, the data is already there. If it's served by another repository and
you think it belongs in the IDC, open an issue; moving data is a maintainer
decision. A 404 means the table isn't configured on test at all, which is
expected for a data manager nobody has requested from yet (see
[Adding a new data manager](#adding-a-new-data-manager)).

Search the open PRs as well
(`gh pr list --repo galaxyproject/idc --search "<table>"`) and the
[catalog](catalog.md) of existing requests.

## 1. Find the data manager

The data manager has to be installed on test.galaxyproject.org, which is
managed in usegalaxy-tools'
[`test.galaxyproject.org/data_managers.yml`](https://github.com/galaxyproject/usegalaxy-tools/blob/master/test.galaxyproject.org/data_managers.yml).
[`schemas/data_managers.yml`](https://github.com/galaxyproject/idc/blob/main/schemas/data_managers.yml)
in this repository lists the same set with full tool GUIDs, so you can usually
copy `tool_id` from there:

```bash
grep -n metaphlan schemas/data_managers.yml
```

`tool_id` has to be a production Tool Shed GUID that ends in the tool version:

```
toolshed.g2.bx.psu.edu/repos/<owner>/<repo>/<tool id>/<tool version>
```

`<tool id>` and `<tool version>` are the `id=` and `version=` attributes of the
`<tool>` tag in the data manager's XML, which often differ from the repository
name. The lint rejects GUIDs without a version and GUIDs from the Test Tool
Shed. Note that the tool version isn't the database version: mOTUs'
`motus_db_fetcher/3.1.0+galaxy2` can also fetch mOTUs 3.0.1.

The data tables a data manager writes are the `<data_table name="...">` entries
in its repository's `data_manager_conf.xml`. The Tool Shed API tells you where
the repository's source lives:

```bash
curl -s 'https://toolshed.g2.bx.psu.edu/api/repositories?name=data_manager_motus&owner=bgruening'
```

If the data manager isn't installed yet, see
[Adding a new data manager](#adding-a-new-data-manager).

## 2. Name the file

A request lives at `data-managers/<data_table>/<version>.yaml`.

The directory is the data manager's primary data table, the one tools pick the
data from, and it has to be one of the request's `data_tables`. The file name is
the version identity. It names the build history on test
(`idc-<data_table>-<version>`) and the marker the publish writes on CVMFS
(`record/<data_table>/<version>`), which is what stops the same version from
being imported twice, so it has to be unique within the table.

Use the name the data manager itself gives the data, such as MetaPhlAn's index
name (`mpa_vJan21_CHOCOPhlAnSGB_202103`) or the mOTUs release (`3.1.0`). After
the build, the existence check looks for the file name, the `params` values and
the `depends_on` versions in the row the data manager wrote, and a name that
appears nowhere in that row is never recognised as built.

Data without a version of its own, like whatever BLAST `nr` is on the day it's
downloaded, gets a date: `nr_2026-09-21`, with a `description` saying what the
date means. Such a data manager is typically requested with `params: {}`; the
date in the file name is what tells one build from the next. A database built
from another one names its source, as in `marker_db_motus_3.1.0` for SameStr
built from mOTUs 3.1.0.

The name ends up in history names, paths and shell commands, so stick to
letters, digits, `.`, `_` and `-`.

## 3. Fill in the parameters

`params` holds the data manager's own tool parameters, keyed by the `name=` of
its `<param>` tags and nested the way the tool form nests them, so a
`<conditional name="db_source">` containing `db_type` becomes
`db_source: {db_type: motus}`.

The lint checks `params` against the parameter schema the Tool Shed publishes
for that exact tool version, and its error messages list the names or values the
tool does accept. To look at the schema yourself:

```bash
python scripts/tool_schemas.py toolshed.g2.bx.psu.edu/repos/bgruening/data_manager_motus/motus_db_fetcher/3.1.0+galaxy2
```

The output includes the options of every select, the defaults and the help
text, and leaves out hidden parameters, which a request can't set.

If you start the file with

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/galaxyproject/idc/main/schemas/request.schema.json
```

VS Code (with the Red Hat YAML extension) and JetBrains IDEs complete and check
`params` as you type, for every data manager installed on test and every
`tool_id` a request already uses.

### Matching another server's identifier

The `value` column of a data table is what workflows and tool runs refer to. If
another Galaxy, usegalaxy.eu say, already has the same data, the IDC should
write the same `value`, or a workflow built there won't find its data here. You
can look it up the same way as on test:

```bash
curl -s https://usegalaxy.eu/api/tool_data/motus_db_versioned
```

Some data managers let you set `value` directly. The mOTUs one has a `db_value`
parameter, and the mOTUs request uses it to write usegalaxy.eu's identifier:

```yaml
params:
  version: "3.1.0"
  db_value: "db_from_2026-04-27T094930Z"
```

Nothing in the pipeline treats `db_value` specially; it's an ordinary tool
parameter. If a data manager has no such parameter and would write a different
`value`, mention it in the PR. The IUC guide on
[stable data table identifiers](https://galaxy-iuc-standards.readthedocs.io/en/latest/best_practices/data_managers.html#stable-data-table-identifiers)
describes what new data managers should write.

## 4. Databases built from other databases

Some data managers take another database as input: SameStr builds its marker
database from a MetaPhlAn or an mOTUs database, and a Bowtie2 index is built from
a genome in `all_fasta`. Such a request names what it's built from with
`depends_on`, mapping the upstream table to the upstream request's file name:

```yaml
depends_on:
  motus_db_versioned: "3.1.0"
```

A request file for the upstream has to exist, and if the upstream version is new
too, both files go in the same PR. If test already serves the upstream, the build
uses that entry. Otherwise the upstream data manager runs first in the same
workflow and its output feeds the downstream one. When both are new, the upstream
is built twice on test (once for its own request, once inside the chain), and
the publish imports it once.

`depends_on` also decides which input of the downstream tool the upstream goes
into. `CHAIN_WIRING` in
[`scripts/generate_build.py`](https://github.com/galaxyproject/idc/blob/main/scripts/generate_build.py)
maps each pair of downstream and upstream table to the tool's conditional and
input, which is why SameStr requests have `params: {}` even though the tool has
a `db_source` conditional. A pair that isn't in `CHAIN_WIRING` yet needs an
entry added in the same PR, and the lint says so.

## 5. Write the file

| field | required | |
|---|---|---|
| `tool_id` | yes | version-pinned production Tool Shed GUID of the data manager |
| `data_tables` | yes | the tables the data manager writes, including the directory name |
| `params` | no | the tool's parameters for this build |
| `depends_on` | no | `{upstream_table: upstream_version}` for a database built from another |
| `description` | no | what the data is, for reviewers and the catalog |
| `doi` | no | DOI of the publication or dataset |

Any other field is an error. Here is the mOTUs request,
[`data-managers/motus_db_versioned/3.1.0.yaml`](https://github.com/galaxyproject/idc/blob/main/data-managers/motus_db_versioned/3.1.0.yaml):

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/galaxyproject/idc/main/schemas/request.schema.json
# mOTUs database v3.1.0 (fetched from Zenodo record 7778108).
# Standalone build: the data manager downloads the DB by version.
tool_id: toolshed.g2.bx.psu.edu/repos/bgruening/data_manager_motus/motus_db_fetcher/3.1.0+galaxy2
data_tables:
  - motus_db_versioned
params:
  version: "3.1.0"   # the tool's "Database Version" select param
  # "Database identifier override": the exact `value` the tool writes to the data
  # table. Set to usegalaxy.eu's existing identifier so workflows built there
  # resolve here unchanged (left empty, the tool would stamp today's date).
  db_value: "db_from_2026-04-27T094930Z"
description: mOTUs profiler database, version 3.1.0
```

and the SameStr database built from it,
[`data-managers/samestr_db/marker_db_motus_3.1.0.yaml`](https://github.com/galaxyproject/idc/blob/main/data-managers/samestr_db/marker_db_motus_3.1.0.yaml):

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/galaxyproject/idc/main/schemas/request.schema.json
# SameStr marker database built from the mOTUs 3.1.0 database.
# Chained build: samestr's "motus_db" input is backed by the motus_db_versioned
# data table, so the build workflow first runs the mOTUs data manager (bundle
# mode) and feeds its output into this step.
tool_id: toolshed.g2.bx.psu.edu/repos/iuc/data_manager_samestr/samestr_db/1.2025.111+galaxy4
data_tables:
  - samestr_db
depends_on:
  # Which mOTUs version this SameStr DB is built against. A request file must
  # exist at data-managers/motus_db_versioned/<version>.yaml.
  motus_db_versioned: "3.1.0"
# Empty on purpose: the tool's db_source conditional is not steered from here.
# depends_on names the upstream table, and CHAIN_WIRING in generate_build.py maps
# (samestr_db, motus_db_versioned) -> db_source: {db_type: motus} plus the input
# that receives the bundle, so the non-default branch is selected by the
# dependency itself. params is for a build's own tool parameters - see
# data-managers/motus_db_versioned/3.1.0.yaml for an example that uses it.
params: {}
description: SameStr marker database derived from mOTUs 3.1.0
```

Quote version numbers (`"3.1.0"`) so YAML keeps them as strings. For genomes and
their indexes, see [Genomes](genome-indexing.md).

## 6. Check it locally

CI runs these on the PR, but running them first saves a round trip. In a
virtualenv, from a checkout of this repository:

```bash
pip install "pydantic>=2" pyyaml jsonschema gxformat2 pytest

python scripts/request_models.py <files>
python scripts/generate_schema.py --check
python scripts/generate_build.py <files> --outdir build --reference-galaxy https://test.galaxyproject.org
python scripts/check_data_exists.py <files>
```

`request_models.py` validates the request itself, including `params` against
the tool's schema. Without arguments it lints every request; `--no-fetch` keeps
it from asking the Tool Shed and `--no-tool-schemas` skips the `params` check.

`generate_schema.py --check` reports the editor schema as stale when your
request uses a `tool_id` it doesn't know. Run `python scripts/generate_schema.py`
and commit the updated `schemas/request.schema.json` with the request.

`generate_build.py` writes `build/<table>/<version>/workflow.gxwf.yml` and
`job.yml`, the workflow and inputs that will run on test after the merge, and
validates the workflow with gxformat2. With `--reference-galaxy` it uses an
upstream test already has instead of adding a step for it, as the real build
does.

`check_data_exists.py` prints `No requested reference data already exists on
...` if test doesn't have the data. It exits 1 if test has it, and also if test
didn't answer (`cannot tell whether this already exists`), in which case try
again later.

CI additionally runs `generate_schema.py --check --refresh`, the unit tests and
`generate_build.py --all`.

## 7. Open the pull request

Open one PR per request, or one for a new upstream together with what's built
from it. The description should say what the data is and where it comes from,
why it's needed, that you checked it isn't served yet, which server's identifier
it matches if you pinned `value`, and how large the download and how long the
build are if you know. Reviewers plan publishes around builds that take hours.

## After the merge

```mermaid
flowchart LR
  pr(["PR"]) --> lint["Lint<br/>(on the PR)"]
  lint --> merge["Review + merge"]
  merge --> build["Build on test<br/>(on merge)"]
  build --> publish["Publish to CVMFS<br/>(maintainer)"]
  publish --> s1["Stratum 1 snapshot<br/>(hourly)"]
  s1 --> reload["Data table reload<br/>on each Galaxy"]
  reload --> done(["Usable in tools"])
```

Merging starts the build workflow (`build.yml`). It skips requests whose data
test already has, generates the bundle workflow for the rest and starts it on
test.galaxyproject.org in the history `idc-<table>-<version>`. Each data manager
runs in bundle mode and writes a bundle dataset: the data plus
`_gx_data_bundle_index.json`, which describes the new data table rows with paths
relative to the bundle. The GitHub job finishes once the workflow is scheduled;
the build itself takes minutes for small databases and hours for large ones.

When the history is green, a maintainer checks the bundle index for relative
paths and the expected `value` and runs the publish workflow (`deploy.yml`),
first as a rehearsal and then for real. It imports each bundle into the CVMFS
repository, appends the rows to the `.loc` files and writes the
`record/<table>/<version>` marker; see
[Publishing to CVMFS](cvmfs-publish-actions.md). Publishing isn't triggered by
the merge because the build finishes hours later.

Galaxy servers read CVMFS through the Stratum 1 replicas, which pick up a new
revision on their hourly snapshot. Galaxy also keeps data tables in memory, so
each server has to reload the table (`GET /api/tool_data/<table>/reload`, as an
admin) or restart before tools show the new entry. On test.galaxyproject.org a
maintainer does that after publishing.
<!-- TODO(merge post-publish-wait-and-reload): say that deploy.yml's
after-publish job waits for the Stratum 1s and reloads the tables on test. -->

[How it works](architecture.md) has the full picture.

## Checking on a request

None of this needs a Galaxy API key.

| stage | how to check |
|---|---|
| lint | the PR's checks, or `gh pr checks <number> --repo galaxyproject/idc` |
| build started | the *Build reference-data bundles* run for the merge (`gh run list --repo galaxyproject/idc --workflow build.yml`). Its "Select requests to build" step lists what it built and what it skipped. |
| build finished | the history on test belongs to the build account, so ask on the PR |
| published | `curl -s https://test.galaxyproject.org/api/tool_data/<table>` shows the row, or `python scripts/check_data_exists.py --expect-exists <file>` prints `ok: <table>/<version> is present` |

## How the existence check works

`scripts/check_data_exists.py` asks test's data table API, and it is used three
times: the lint warns if a request's data already exists, the build skips such
requests, and the publish has nothing to import for them because no build
history exists.

Matching a request to a row is a heuristic, because data managers keep the
version in different columns. A request counts as present if its file name, one
of its `params` values or one of its `depends_on` versions equals a whole field
of a row, or if the row's `value` starts with one of them followed by `-`. To
see how the check will treat a request once it's built, run it against a server
that already has the same data:

```bash
python scripts/check_data_exists.py --reference-galaxy https://usegalaxy.eu <file>
```

A few consequences follow from relying on the data table:

- Test has to load the IDC's own `tool_data_table_conf.xml`, which it does, or
  the check can't see what the IDC has published.
- A row only shows up after the Stratum 1 snapshot and a table reload. A build
  that runs in between rebuilds data that is already on CVMFS, but the record
  markers keep it from being imported twice.
- If test doesn't answer, the check fails instead of assuming the data is
  missing, so an outage stops the build rather than rebuilding everything.
- After a publish, `python scripts/check_data_exists.py --all --expect-exists`
  confirms that every request is found under the name it was requested with.

SameStr built from mOTUs isn't recognised at the moment. Its row carries the
mOTUs `value` (`db_from_…`) and a free-text name, so neither
`marker_db_motus_3.1.0` nor `3.1.0` matches a field, even where the data is
served. Such a request is rebuilt if its file changes, although it isn't
imported twice. SameStr built from MetaPhlAn is matched, because the MetaPhlAn
`value` starts with the index name.

## Adding a new data manager

A data manager that isn't installed on test yet has to be added to
[usegalaxy-tools](https://github.com/galaxyproject/usegalaxy-tools)'
`test.galaxyproject.org/data_managers.yml` first, and its jobs have to be able to
run on test. That PR has to be merged and deployed before a build can run,
though the lint here works without it.

In this repository, its data tables go in
[`config/tool_data_table_conf.xml`](https://github.com/galaxyproject/idc/blob/main/config/tool_data_table_conf.xml),
reading from `/cvmfs/idc.galaxyproject.org/config/<table>.loc`, with the columns
of the data manager's `tool_data_table_conf.xml.sample` and
`allow_duplicate_entries="False"`. If it's built from another database, it needs
a `CHAIN_WIRING` entry naming the conditional to set (if any) and the input that
receives the upstream bundle. The table, the wiring and the first request can
share a PR. If the tool isn't in `schemas/data_managers.yml`, regenerate the
editor schema with `python scripts/generate_schema.py` and commit it too.

When the installed set changes, maintainers refresh `schemas/data_managers.yml`
and the editor schema with

```bash
python scripts/generate_schema.py --from-lock https://raw.githubusercontent.com/galaxyproject/usegalaxy-tools/master/test.galaxyproject.org/data_managers.yml.lock
```

## Lint errors

**`tool_id must be a production Tool Shed GUID starting with 'toolshed.g2.bx.psu.edu/repos/'`**

The GUID is from the Test Tool Shed or misspelled.

**`tool_id must be a version-pinned GUID host/repos/owner/repo/tool/version`**

The tool version is missing from the end, e.g. `/3.1.0+galaxy2`.

**`params: Additional properties are not allowed ('version' was unexpected); this tool's parameters here are ['index']`**

The tool has no parameter of that name at that level. The message lists the ones
it has; nested parameters go inside their section or conditional.

**`params: index: 'mpa_vJan25_CHOCOPhlAnSGB_2025' is not one of [...]`**

A select only accepts the options the tool offers, and the message lists them.
If the version you need isn't among them, the data manager has to be updated
first, or you have an older tool version pinned.

**`cannot check params - no parameter schema for tool_id ...`**

The Tool Shed has no schema for that GUID, usually because the tool id or
version is wrong. `python scripts/tool_schemas.py <GUID>` shows the same error.

**`Extra inputs are not permitted`**

A field that isn't in the table under [Write the file](#5-write-the-file). There
is no `version` field; the version is the file name.

**`directory name 'motus_db' is not in data_tables ['motus_db_versioned']`**

The directory has to be named after the primary data table.

**`depends_on ... but no request file exists at data-managers/<table>/<version>.yaml`**

Add the upstream request, or correct the version to an existing request's file
name.

**`No chain wiring defined for downstream ...`**

The pair needs a `CHAIN_WIRING` entry; see
[Adding a new data manager](#adding-a-new-data-manager).

**`error: schemas/request.schema.json is stale`**

Run `python scripts/generate_schema.py` and commit the result. If CI reports
this but the local `--check` passes, the Tool Shed has changed a schema already
in use, and `python scripts/generate_schema.py --refresh` picks that up.

## Other questions

**The lint warns that my data already exists.** Test serves it from some
source. If the existing entry is wrong or should move into the IDC, say so on
the PR; replacing an entry is done by hand by a maintainer.

**The PR is merged but nothing was built.** The build run's "Select requests to
build" step says which requests it skipped and why. A maintainer can re-run the
build for a single request once a failure is fixed.

**It's published, but my Galaxy doesn't show it.** Give the Stratum 1s up to an
hour, then reload the table on that Galaxy or restart it. The server also has
to load the IDC's `tool_data_table_conf.xml`; see
[Using IDC data in Galaxy](using-idc-data.md).

**Can I request a new version of something the IDC already has?** Yes, a new
version is a new file. Requesting the same file name again does nothing.

## Using an agent

[`.claude/skills/`](https://github.com/galaxyproject/idc/tree/main/.claude/skills)
has two skills, listed in
[`AGENTS.md`](https://github.com/galaxyproject/idc/blob/main/AGENTS.md), which
coding agents read when they work in the repository. `request-reference-data` goes from "I need database X, version
Y" to a linted request and a drafted PR, and `check-reference-data-request`
finds out how far a request has got. Both use the scripts described on this
page and neither needs an API key.
