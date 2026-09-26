# How it works

How a versioned reference-data request travels from a pull request to a Galaxy
tool, for contributors and maintainers who want the whole picture. The
step-by-step for contributors is
[Requesting reference data](requesting-reference-data.md); the publish job is
described in [Publishing to CVMFS](cvmfs-publish-actions.md).

## The pieces

| piece | role |
|---|---|
| `data-managers/<table>/<version>.yaml` | one request: data manager `tool_id`, `params`, optional `depends_on` |
| `scripts/request_models.py` | the request schema and lint, including `params` against the Tool Shed's parameter schema |
| `scripts/generate_build.py` | turns a request into a gxformat2 data-manager-bundle workflow and a job file |
| `scripts/check_data_exists.py` | asks a Galaxy's public data table API whether the data is already served |
| `scripts/import_bundles.py`, `scripts/get_bundle_urls.py` | resolve a finished build's bundles and import them onto CVMFS |
| `.github/workflows/lint.yml` | Stage 1, on pull requests |
| `.github/workflows/build.yml` | Stage 2, on merge to `main` |
| `.github/workflows/deploy.yml` | Stage 3, dispatched by a maintainer |
| test.galaxyproject.org | the build Galaxy, and the reference for "does this exist?" |
| `idc.galaxyproject.org` | the CVMFS repository: Stratum 0 (where publishes happen) and Stratum 1 replicas (what clients read) |

## Contributor flow

```mermaid
flowchart TD
  start(["I need a reference database<br/>(e.g. mOTUs 3.1.0) on Galaxy servers"]) --> exists{"Is it already served?<br/>GET /api/tool_data/&lt;table&gt;<br/>on test.galaxyproject.org"}
  exists -- "yes (IDC, byhand, managed...)" --> stop(["Nothing to request.<br/>Migrating existing data is a<br/>manual maintainer decision"])
  exists -- no --> dm{"Is the data manager installed<br/>on test.galaxyproject.org?"}
  dm -- no --> tools["PR to usegalaxy-tools<br/>test.galaxyproject.org/data_managers.yml<br/>(+ new table in config/tool_data_table_conf.xml,<br/>+ CHAIN_WIRING if it builds from another DB)"]
  tools --> dm
  dm -- yes --> find["Find the version-pinned tool_id and parameter names<br/>(Tool Shed / tool form; the editor schema<br/>schemas/request.schema.json autocompletes them)"]
  find --> write[/"Write data-managers/&lt;table&gt;/&lt;version&gt;.yaml<br/>tool_id, data_tables, params,<br/>depends_on (chained builds), description"/]
  write --> local["Optional: lint locally<br/>scripts/request_models.py<br/>scripts/generate_build.py --all"]
  local --> pr["Open a pull request"]
  pr --> lint{"Lint CI green?<br/>schema, params vs Tool Shed schema,<br/>gxformat2 build validation,<br/>'already exists' warning"}
  lint -- no --> fix["Fix the request"] --> pr
  lint -- yes --> review(["Maintainer review and merge"])
```

A request is a single flat YAML file. Its **directory** is the data manager's
primary data table and must be one of its `data_tables`; its **file name** is
the version identity, which keys the build history (`idc-<table>-<version>`)
and the record marker on CVMFS (`record/<table>/<version>`). `tool_id` is a
version-pinned production Tool Shed GUID, and `params` are validated against the
parameter schema the Tool Shed serves for exactly that tool version.

The one rule that shapes everything else: **don't duplicate data a Galaxy
already serves.** The only "already exists" signal is the build Galaxy's public
data table API, which covers every source it loads; there is deliberately no
in-repo list of published versions.

## Maintainer flow

```mermaid
flowchart TD
  pr(["Request PR opened"]) --> rev{"Review<br/>lint green? identity sensible?<br/>value matches other servers (e.g. db_value pin)?<br/>DM installed + routed on test?"}
  rev -- changes needed --> ask["Request changes"] --> pr
  rev -- ok --> merge["Merge to main"]
  merge --> build["build.yml runs automatically<br/>check_data_exists skips existing data<br/>generate_build writes the bundle workflow<br/>planemo run --no_use_cache --no_wait"]
  build --> hist["History idc-&lt;table&gt;-&lt;version&gt;<br/>on test.galaxyproject.org<br/>(minutes to hours)"]
  hist --> ok{"Build green and bundle sane?<br/>_gx_data_bundle_index.json:<br/>relative path, expected value"}
  ok -- no --> debug["Debug: DM bug, routing, container<br/>fix upstream tool, bump in usegalaxy-tools<br/>re-run build.yml (workflow_dispatch)"] --> hist
  ok -- yes --> rehearse["Actions: Publish reference data to CVMFS<br/>publish = false (import, then abort)"]
  rehearse --> rok{"Rehearsal summary as expected?<br/>imports vs skips"}
  rok -- no --> debug
  rok -- yes --> publish["Publish reference data to CVMFS<br/>publish = true"]
  publish --> snap["Stratum 1 snapshots<br/>(hourly)"]
  snap --> reload["Galaxy servers reload tables<br/>GET /api/tool_data/&lt;table&gt;/reload<br/>or restart"]
  reload --> done(["Available to users"])
  supersede["Replacing an existing entry?<br/>manual Stratum 0 transaction:<br/>comment .loc row, remove data + record marker"] -.-> rehearse
```

1. **Review.** Lint is green. The identity makes sense, and the `value` the data
   manager will write matches what other servers use for the same data (which is
   why the mOTUs request pins `db_value` to usegalaxy.eu's identifier). The data
   manager is installed on test and its jobs can run there.
2. **Merge.** `build.yml` starts by itself. `--no_use_cache` matters: without it
   Galaxy's job cache could hand back a copy of an earlier, possibly broken,
   bundle instead of running the data manager again.
3. **Watch the build.** Minutes for small databases, hours for large ones. Check
   the bundle by its index,
   `GET /api/datasets/<id>/display?filename=_gx_data_bundle_index.json`: paths
   must be relative and `value` as expected. (`display?preview=true` shows the
   data manager's primary output instead, with absolute job paths; it says
   nothing about the bundle.)
4. **Rehearse, then publish.** The publish workflow with `publish=false` does a
   full import into a CVMFS transaction and aborts it; its summary shows what
   would be imported and what skipped. Then run it with `publish=true`.
5. **Get it to users.** The Stratum 1s pick the new revision up on their hourly
   snapshot; Galaxy servers then reload the table or restart.
6. **Replacing an existing entry** isn't automated: it's a manual Stratum 0
   transaction (comment out the `.loc` row, remove the data directory and the
   `record/` marker), after which the normal publish imports the replacement.

## End to end

```mermaid
sequenceDiagram
  autonumber
  actor C as Contributor
  participant GH as GitHub (idc repo)
  participant GA as GitHub-hosted Actions
  participant TS as Tool Shed
  participant TG as test.galaxyproject.org
  actor M as Maintainer
  participant R as Publish runner<br/>(self-hosted)
  participant S0 as Stratum 0
  participant S1 as Stratum 1s
  participant G as Galaxy servers

  C->>GH: PR adds data-managers/<table>/<version>.yaml
  GH->>GA: lint.yml
  GA->>TS: fetch data manager parameter schema
  GA->>TG: GET /api/tool_data/<table> (already exists?)
  GA-->>GH: lint result + exists warning
  M->>GH: review + merge
  GH->>GA: build.yml (push to main, data-managers/**)
  GA->>TG: check_data_exists, then planemo run bundle workflow
  TG->>TG: data managers run in bundle mode,<br/>a chained upstream bundle feeds the downstream DM,<br/>bundle index records relative paths
  TG-->>TG: history idc-<table>-<version> with bundle dataset(s)
  M->>GH: dispatch deploy.yml (publish=false, then true)
  GH->>R: job on runner group cvmfs-publish
  R->>S0: SSH as the repository owner
  S0->>S0: cvmfs_server transaction
  S0->>S0: sync config/tool_data_table_conf.xml
  S0->>TG: import_bundles.py resolves bundles from the build history
  S0->>TG: download bundle (galaxy-import-data-bundle)
  S0->>S0: data to /cvmfs/idc.../data, append .loc rows,<br/>write record/<table>/<version>
  S0->>S0: cvmfs_server publish (or abort for a rehearsal)
  S1->>S0: hourly snapshot
  G->>S1: CVMFS clients fetch the new revision
  G->>G: data tables reloaded (admin reload or restart)
  G-->>C: new entry selectable in tools
```

### Stage 1: Lint

`lint.yml`, on pull requests and pushes to `main`:

- `scripts/request_models.py` validates every request;
- `scripts/generate_schema.py --check --refresh` confirms the committed editor
  schema matches the model and what the Tool Shed serves;
- `scripts/generate_build.py --all` generates every build workflow and
  gxformat2-validates it;
- the unit tests run;
- `scripts/check_data_exists.py --all --warn` warns about data that already
  exists.

None of this needs secrets, so it runs safely on pull requests from forks.

### Stage 2: Build

`build.yml`, on a push to `main` that touches `data-managers/**`, or dispatched
by hand for one request. It runs with the build account's test.galaxyproject.org
key. `check_data_exists.py --print-new` drops requests whose data is already
served; for a chained request whose upstream is served, `generate_build.py`
references that entry instead of rebuilding it.

Each data manager runs with `__data_manager_mode: bundle` and writes a bundle
dataset: the data files plus `_gx_data_bundle_index.json`, which describes the
data table rows with paths relative to the bundle. In a chained build the
upstream step's bundle is wired straight into the downstream data manager's
input. `planemo run --no_wait` returns once the workflow is scheduled; the
build itself carries on in the history `idc-<table>-<version>`.

### Stage 3: Publish

`deploy.yml`, dispatched by a maintainer once the build histories are green.
It isn't triggered by the merge because the build finishes hours later and a
publish on merge would race it. It runs on a self-hosted runner, because the
Stratum 0 only accepts SSH from inside its own network, and uses credentials
held in a protected GitHub environment. The job opens a CVMFS transaction,
syncs `config/tool_data_table_conf.xml`, and for each request resolves the
bundles from its build history, refusing any dataset that isn't `ok`. A request
whose `record/<table>/<version>` marker exists is skipped, as is a chain's
upstream if its own marker exists. `galaxy-import-data-bundle` then moves the
data under `/cvmfs/idc.galaxyproject.org/data/` and appends the `.loc` rows, the
record marker is written, and the transaction is published (or aborted, for a
rehearsal). The Jenkins job runs the same code and remains a fallback.

### Stage 4: Distribution

The Stratum 1 replicas snapshot the Stratum 0 every hour, and CVMFS clients pick
up the new revision within minutes after that. A Galaxy server needs the IDC's
`tool_data_table_conf.xml` in its `tool_data_table_config_path`, its job
containers need `/cvmfs/idc.galaxyproject.org` bound in, and it has to reload
the table (or restart) before tools offer the new entry; see
[Using IDC data in Galaxy](using-idc-data.md).
<!-- TODO(merge post-publish-wait-and-reload): add that deploy.yml's
after-publish job waits for the Stratum 1s, then reloads and verifies the
tables on test.galaxyproject.org, and show it in the diagrams above. -->
