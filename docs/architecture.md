# How it works

This page follows a request from the pull request to a Galaxy tool. How to
write a request is covered in
[Requesting reference data](requesting-reference-data.md), and the publish job
in [Publishing to CVMFS](cvmfs-publish-actions.md).

## The pieces

| piece | role |
|---|---|
| `data-managers/<table>/<version>.yaml` | one request: data manager `tool_id`, `params`, optional `depends_on` |
| `scripts/request_models.py` | the request schema and lint, including `params` against the Tool Shed's parameter schema |
| `scripts/generate_build.py` | turns a request into a gxformat2 data-manager-bundle workflow and a job file |
| `scripts/check_data_exists.py` | asks a Galaxy's public data table API whether the data is already served |
| `scripts/import_bundles.py`, `scripts/get_bundle_urls.py` | resolve a finished build's bundles and import them onto CVMFS |
| `.github/workflows/lint.yml` | lint, on pull requests |
| `.github/workflows/build.yml` | build, on merge to `main` |
| `.github/workflows/deploy.yml` | publish, started by a maintainer |
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

The request's directory is the data manager's primary data table, and its file
name is the version identity, which names the build history
(`idc-<table>-<version>`) and the record marker on CVMFS
(`record/<table>/<version>`). Whether data already exists is decided by the
build Galaxy's public data table API, which covers every source that Galaxy
loads, so data that's already served somewhere isn't built again.

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

Reviewers check that the lint is green, that the file name matches what the
data manager will write, and that the `value` matches what other servers use for
the same data, which is why the mOTUs request pins `db_value` to usegalaxy.eu's
identifier. The data manager has to be installed on test and able to run jobs
there.

The build starts by itself on merge. It runs planemo with `--no_use_cache`,
because Galaxy's job cache could otherwise hand back a copy of an earlier,
possibly broken, bundle instead of running the data manager again. Builds take
minutes for small databases and hours for large ones. A maintainer then checks
the bundle through its index,
`GET /api/datasets/<id>/display?filename=_gx_data_bundle_index.json`, where the
paths should be relative and `value` as expected. `display?preview=true` isn't
useful for this: it shows the data manager's primary output, which has absolute
job paths.

Publishing starts with a rehearsal (`publish=false`), which imports everything
into a CVMFS transaction and aborts it, and whose summary shows what would be
imported and what skipped. The real publish follows. Replacing an entry that's
already published isn't automated; a maintainer comments out the `.loc` row and
removes the data directory and the record marker in a Stratum 0 transaction,
and the next publish imports the replacement.

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
  GH->>R: publish job (self-hosted runner)
  R->>S0: connect to the Stratum 0
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

### Lint

`lint.yml` runs on pull requests and on pushes to `main`. It validates every
request with `scripts/request_models.py`, checks with
`scripts/generate_schema.py --check --refresh` that the committed editor schema
still matches the model and the Tool Shed, generates and gxformat2-validates
every build workflow, runs the unit tests, and warns about requests whose data
already exists. It needs no secrets, so it runs on pull requests from forks.

### Build

`build.yml` runs on pushes to `main` that change `data-managers/`, and can be
started by hand for a single request. It drops requests whose data test already
serves, and for a chained request whose upstream is already served it uses that
entry instead of building it again. Each data manager runs in bundle mode and
writes the data together with `_gx_data_bundle_index.json`, which describes the
new rows with paths relative to the bundle. In a chained build, the upstream
step's bundle goes straight into the downstream data manager. planemo returns
once the workflow is scheduled, and the build carries on in the history
`idc-<table>-<version>`.

### Publish

`deploy.yml` is started by a maintainer once the builds are green, since they
finish hours after the merge. It runs on a self-hosted runner that can reach the
Stratum 0, with its credentials in a protected GitHub environment. The job opens
a CVMFS transaction, syncs `config/tool_data_table_conf.xml`, and resolves each
request's bundles from its build history, refusing datasets that aren't `ok`.
Requests that already have a record marker are skipped, and so is a chain's
upstream when it has one, so an upstream built both on its own and inside a
chain is imported once. `galaxy-import-data-bundle` moves the data under
`/cvmfs/idc.galaxyproject.org/data/` and appends the `.loc` rows, the record
marker is written, and the transaction is published, or aborted for a
rehearsal.

### Distribution

The Stratum 1 replicas take a snapshot of the Stratum 0 every hour, and CVMFS
clients see the new revision a few minutes later. A Galaxy server that loads the
IDC's `tool_data_table_conf.xml` then has to reload the table, or restart,
before its tools offer the new entry; see
[Using IDC data in Galaxy](using-idc-data.md).

### The list of data managers

`schemas/data_managers.yml` lists the data managers installed on the build
Galaxy, and the editor schema includes their parameters. When the installed set
changes in usegalaxy-tools, a maintainer refreshes both with

```bash
python scripts/generate_schema.py --from-lock https://raw.githubusercontent.com/galaxyproject/usegalaxy-tools/master/test.galaxyproject.org/data_managers.yml.lock
```

and commits the two files it rewrites.
<!-- TODO(merge post-publish-wait-and-reload): add that deploy.yml's
after-publish job waits for the Stratum 1s, then reloads and verifies the
tables on test.galaxyproject.org, and show it in the diagrams above. -->
