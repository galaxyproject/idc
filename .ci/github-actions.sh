#!/usr/bin/env bash
# Reference-data publish driver for GitHub Actions (.github/workflows/deploy.yml).
#
# Stage 3 of the reference-data pipeline: import the bundles built by
# .github/workflows/build.yml on test.galaxyproject.org onto CVMFS and publish.
# This is the reference-data-only import path of .ci/jenkins.sh (what
# "@galaxybot deploy reference-data" ran on Jenkins), driven from a self-hosted
# runner instead: everything happens over SSH on the Stratum 0, so the runner
# needs no CVMFS mount, overlayfs or docker - only network access to the
# Stratum 0 and the key loaded in an ssh-agent (the workflow does that).
#
# The workflow sets:
#   PUBLISH                 'true' to publish the CVMFS transaction, 'false' to import and abort (rehearsal)
#   REFERENCE_DATA_API_KEY  test.galaxyproject.org key used to look up the built bundles
set -euo pipefail

: "${PUBLISH:=true}"
[ -n "${REFERENCE_DATA_API_KEY:-}" ] || { echo "REFERENCE_DATA_API_KEY is not set" >&2; exit 1; }
[ -n "${SSH_AUTH_SOCK:-}" ] || { echo "SSH_AUTH_SOCK is not set; load the Stratum 0 key into an ssh-agent first" >&2; exit 1; }

# The genome build/import needs the build Galaxy and is not part of this path.
export REFERENCE_DATA_ONLY=true

# Set by Actions, so the defaults here are for running by hand.
GIT_COMMIT="${GITHUB_SHA:-$(git rev-parse HEAD)}"
# GITHUB_RUN_ID is stable across re-runs, GITHUB_RUN_ATTEMPT increments per attempt.
BUILD_NUMBER="${GITHUB_RUN_ID:-$$}-${GITHUB_RUN_ATTEMPT:-1}"
JOB_NAME="${GITHUB_WORKFLOW:-idc-reference-data}"
# jenkins.sh's scratch dir, removed on exit. Must NOT be $GITHUB_WORKSPACE (the checkout).
WORKSPACE="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/idc-publish.XXXXXX")"
export GIT_COMMIT BUILD_NUMBER JOB_NAME WORKSPACE

cd "$(dirname "${BASH_SOURCE[0]}")/.."
# Functions only (main is guarded); jenkins.sh installs its cleanup trap.
. ./.ci/jenkins.sh

function main_reference_data() {
    load_repo_configs
    detect_changes
    set_repo_vars
    if $PUBLISH; then
        log "Publishing reference data for commit ${GIT_COMMIT}"
    else
        log "PUBLISH=false: importing into a transaction that will be aborted (rehearsal)"
    fi
    do_import_remote
    clean_workspace
}

main_reference_data
