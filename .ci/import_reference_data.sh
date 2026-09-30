#!/usr/bin/env bash
# Stage 3 of the IDC reference-data pipeline: import the reference-data bundles
# built on Galaxy (Stage 2) onto CVMFS.
#
# For each request under data-managers/, this locates the build's workflow
# invocation (via its history idc-<dm>-<version>), resolves the bundle
# dataset(s) from that invocation's named outputs, and imports each with
# galaxy-import-data-bundle - recording record/<dm>/<version> for idempotency
# (already-imported builds are skipped).
#
# It must run INSIDE a CVMFS transaction, i.e. where ${CVMFS_ROOT} is writable
# (the Jenkins job opens the transaction; see .ci/jenkins.sh). The Python
# environment must have bioblend + our scripts, and galaxy-import-data-bundle
# (from galaxy-maintenance-scripts) must be on PATH or given via IMPORT_CMD.
#
# Environment:
#   GALAXY_URL          Galaxy the bundles were built on (default test.galaxyproject.org)
#   EPHEMERIS_API_KEY   API key for GALAXY_URL (required)
#   CVMFS_ROOT          CVMFS repo root (default /cvmfs/idc.galaxyproject.org)
#   PYTHON              Python interpreter (default python3)
#   IMPORT_CMD          galaxy-import-data-bundle executable (default: on PATH)
set -euo pipefail

: "${GALAXY_URL:=https://test.galaxyproject.org}"
: "${CVMFS_ROOT:=/cvmfs/idc.galaxyproject.org}"
: "${PYTHON:=python3}"
: "${IMPORT_CMD:=galaxy-import-data-bundle}"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

shopt -s nullglob
found=false
for req in "$REPO_DIR"/data-managers/*/*.yml "$REPO_DIR"/data-managers/*/*.yaml; do
    found=true
    dm="$(basename "$(dirname "$req")")"
    version="$(basename "$req")"; version="${version%.*}"
    echo "== reference data: ${dm}/${version} =="
    # Skip data that already exists in the Galaxy data table (the build stage
    # skips it too, so there is no history to import). --print-new emits the
    # request only if its data is not present.
    if [ -z "$("$PYTHON" "$REPO_DIR/scripts/check_data_exists.py" "$req" --print-new --reference-galaxy "$GALAXY_URL")" ]; then
        echo "   already exists on $GALAXY_URL; skipping"
        continue
    fi
    "$PYTHON" "$REPO_DIR/scripts/import_bundles.py" \
        --galaxy-url "$GALAXY_URL" \
        --history-name "idc-${dm}-${version}" \
        --dm "$dm" --version "$version" --request "$req" \
        --cvmfs-root "$CVMFS_ROOT" \
        --import-cmd "$IMPORT_CMD"
done
$found || echo "No reference-data requests under data-managers/."
