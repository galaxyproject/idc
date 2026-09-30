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
   so re-running is safe.

Publishing is deliberately manual rather than on merge: the build is
asynchronous, so a publish triggered by the merge would race it.
`import_bundles.py` also refuses any bundle whose dataset is not `ok`, so a
premature run fails instead of publishing a partial database.

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
    the key only resolves histories and invocations.
  - No required reviewers: running the workflow from `main` is the
    authorization step. Deployment branch policy: `main` only.
- **Settings → Actions → General**: allow the workflow to run on the
  self-hosted group (default for org-visible groups).
- **CODEOWNERS** for `/.ci/` and `/.github/`: anything merged there runs with the
  publishing credential.
