# Publishing reference data to CVMFS from GitHub Actions

Stage 3 of the reference-data pipeline (`.github/workflows/deploy.yml`,
`.ci/github-actions.sh`) imports the bundles built on test.galaxyproject.org onto
`idc.galaxyproject.org` and publishes. It is the reference-data-only import path
of `.ci/jenkins.sh` (what `@galaxybot deploy reference-data` ran on Jenkins),
driven from a GitHub Actions self-hosted runner instead. Jenkins keeps working
unchanged as a fallback; both run the same `scripts/import_bundles.py`.

## How a publish runs

1. A request under `data-managers/` merges to `main`. `build.yml` generates the
   data-manager-bundle workflow and starts it on test.galaxyproject.org
   (`planemo run --no_wait`) into the history `idc-<dm>-<version>`.
2. The build runs for minutes to hours. Wait until the history's datasets are
   `ok`.
3. *Actions → Publish reference data to CVMFS → Run workflow* (from `main`).
   `publish: false` imports into a CVMFS transaction and then aborts it - a full
   rehearsal that changes nothing on CVMFS.
4. The job SSHes to the Stratum 0 as the `idc` user, bootstraps a pinned Python
   (through a uv release pinned by version and SHA-256), installs
   `galaxy-maintenance-scripts`, opens a transaction, runs
   `galaxy-import-data-bundle` for every request, records
   `record/<dm>/<version>` and publishes. Already-recorded versions are skipped,
   so re-running is safe. Before closing the transaction it logs the data table
   rows it added (the `.loc` lines in the transaction's upper layer that are not
   in the published copy) and, once published, passes them on as the job output
   `published_entries`.
5. The `after-publish` job (`scripts/after_publish.py`) waits for the Stratum 1s
   to serve the new revision, then reloads each affected data table on
   test.galaxyproject.org and checks the new values are in it. See below.

Publishing is deliberately manual rather than on merge: the build is
asynchronous, so a publish triggered by the merge would race it.
`import_bundles.py` also refuses any bundle whose dataset is not `ok`, so a
premature run fails instead of publishing a partial database.

## After the publish: Stratum 1s and test.galaxyproject.org

Galaxy servers read CVMFS through the Stratum 1s, and those pick up a new
revision only on their own hourly snapshot (root cron `cvmfs_server snapshot -a
-i` at :00, taking a few minutes). Nothing here triggers a snapshot, so no
Stratum 1 access is needed, but the new data can take over an hour to reach any
Galaxy. The `after-publish` job:

1. reads the published revision from the Stratum 0's public
   `/cvmfs/idc.galaxyproject.org/.cvmfspublished` (the `S<revision>` line);
2. polls the same file on `cvmfs1-psu0`, `cvmfs1-iu0` and `cvmfs1-tacc0` every
   minute until each serves that revision or later (90 minutes at most);
3. then, for up to 30 minutes, calls `GET /api/tool_data/<table>/reload` on
   test.galaxyproject.org for every table a new row went into (the `.loc` to
   table mapping is `config/tool_data_table_conf.xml`) and checks each new value
   in the table's first column of `GET /api/tool_data/<table>`. Galaxy's own
   CVMFS client needs a few minutes after the snapshot to see the new `.loc`,
   hence the retries.

The job summary lists when each Stratum 1 caught up and when each value became
visible. The job fails if a Stratum 1 did not catch up or a value never showed
up; Galaxy is checked either way, since test may be served by a Stratum 1 that
did catch up. The publish itself has happened by then - a failure here means
"not visible (yet)", not "not published".

It runs on a GitHub-hosted runner: it mostly sleeps, for up to two hours, and
must not hold the shared `cvmfs-publish` runner. It does not run for a rehearsal
(`publish: false`) or when the publish added no data table row.

## Why a self-hosted runner

The job needs nothing but SSH to the Stratum 0 (no CVMFS mount, overlayfs or
docker on the runner), but `cvmfs0-psu0.galaxyproject.org:22` is only reachable
from inside its network, which rules out GitHub-hosted runners. The
`cvmfs-publish` runner that usegalaxy-tools deploys from already SSHes to that
host (for `sandbox.galaxyproject.org`), so it is reused.

A self-hosted job may run for up to 5 days (the 6-hour cap applies to
GitHub-hosted runners); `timeout-minutes` in the workflow is set explicitly
because the default is still 360.

## Required setup

### Organization (runner group) - org admin

`cvmfs-publish` is an organization-level runner group visible only to
`galaxyproject/usegalaxy-tools` and restricted to exactly one workflow (see
usegalaxy-tools' `docs/self-hosted-runner.md`). It needs:

- **Repository access**: add `galaxyproject/idc`.
- **Allowed workflows**: add
  `galaxyproject/idc/.github/workflows/deploy.yml@refs/heads/main`.

The runner itself holds no credential (keys live in each repository's
environment), so sharing the host does not share access: an idc job can only use
the `idc` Stratum 0 key from idc's environment. What is shared is the queue: the
group has one runner that takes one job per boot, so publishes wait behind tool
deploys and vice versa.

The runner image's `known_hosts` must contain `cvmfs0-psu0.galaxyproject.org`
(it does, for sandbox); `start_ssh_control` uses `StrictHostKeyChecking=yes`.

### Repository - idc admin

- **Settings → Environments → `cvmfs-publish`**
  - Secret `STRATUM0_SSH_KEY`: private key authorized for
    `idc@cvmfs0-psu0.galaxyproject.org` (the same access the Jenkins job has).
  - Secret `REFERENCE_DATA_API_KEY`: a test.galaxyproject.org API key belonging
    to the user who owns the `idc-<dm>-<version>` build histories (the
    `TEST_API_KEY` used by `build.yml`). Bundle downloads are unauthenticated;
    the key resolves histories and invocations, and - which needs an admin
    user - lets the `after-publish` job reload data tables.
  - No required reviewers: running the workflow from `main` is the
    authorization step. Deployment branch policy: `main` only.
- **Settings → Actions → General**: allow the workflow to run on the
  self-hosted group (default for org-visible groups).
- **CODEOWNERS** for `/.ci/` and `/.github/`: anything merged there runs with the
  publishing credential.
