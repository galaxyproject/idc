#!/usr/bin/env bash
# Tests how do_import_remote (.ci/jenkins.sh) ends a transaction: publish,
# abort, and what reaches $GITHUB_OUTPUT. Each scenario runs in its own bash
# process that sources the real jenkins.sh (main is guarded), so its traps are
# the real ones; only the Stratum 0 side is stubbed.
#
#   bash tests/test_do_import_remote.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ "${1:-}" = --scenario ]; then
    scenario="$2" tmp="$3"
    export WORKSPACE="$tmp/workspace" BUILD_NUMBER=1 REFERENCE_DATA_ONLY=true
    mkdir -p "$WORKSPACE"
    # shellcheck source=.ci/jenkins.sh
    . ./.ci/jenkins.sh
    REPO=idc.galaxyproject.org
    OVERLAYFS_UPPER="$tmp/upper"
    OVERLAYFS_LOWER="$tmp/lower"
    GITHUB_OUTPUT="$tmp/output"
    PUBLISH=true
    PUBLISH_FAILS=false
    GREP_EXIT=
    case "$scenario" in
        publish_ok) ;;
        publish_fails) PUBLISH_FAILS=true ;;
        rehearsal) PUBLISH=false ;;
        record_fails) GREP_EXIT=2 ;;
        *) echo "unknown scenario $scenario" >&2; exit 2 ;;
    esac

    function call() { echo "$*" >> "$tmp/calls"; }
    function exec_on() {
        case "$*" in
            "cvmfs_server publish "*) call publish; ! $PUBLISH_FAILS ;;
            "cvmfs_server abort "*) call abort ;;
            grep\ *) if [ -n "$GREP_EXIT" ]; then return "$GREP_EXIT"; fi; bash -c "$*" ;;
            *) bash -c "$*" ;;
        esac
    }
    function start_ssh_control() { SSH_MASTER_UP=true; }
    function stop_ssh_control() { SSH_MASTER_UP=false; }
    function create_remote_workdir() { :; }
    function setup_remote_python() { :; }
    function setup_remote_ephemeris() { :; }
    function setup_galaxy_maintenance_scripts() { :; }
    function has_reference_data_requests() { return 0; }
    function begin_transaction() { call begin; CVMFS_TRANSACTION_UP=true; }
    function update_tool_data_table_conf() { :; }
    function import_reference_data_bundles() { :; }
    function check_for_repo_changes() { :; }
    function post_install() { :; }

    do_import_remote
    exit 0
fi

failures=0
function check() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" == "$actual" ]; then
        echo "ok: $name"
    else
        echo "FAIL: $name"
        echo "  expected: $(printf '%q' "$expected")"
        echo "  actual:   $(printf '%q' "$actual")"
        failures=$((failures + 1))
    fi
}

# Runs a scenario; sets $rc, $calls (one per line) and $output ($GITHUB_OUTPUT).
function run() {
    local tmp
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/upper/config" "$tmp/lower/config"
    printf 'new\trow\n' > "$tmp/upper/config/x.loc"
    rc=0
    bash "${BASH_SOURCE[0]}" --scenario "$1" "$tmp" > "$tmp/log" 2>&1 || rc=$?
    calls="$(cat "$tmp/calls" 2>/dev/null || true)"
    output="$(cat "$tmp/output" 2>/dev/null || true)"
    if [ -e "$tmp/workspace" ]; then
        echo "FAIL: $1: the trap did not clean up \$WORKSPACE"
        failures=$((failures + 1))
    fi
    last_log="$(tail -n 5 "$tmp/log")"
    rm -rf "$tmp"
}

run publish_ok
check "publish ok: exit 0" 0 "$rc"
check "publish ok: published, no abort" "begin"$'\n'"publish" "$calls"
check "publish ok: output written" "published_entries<<" "$(head -n1 <<< "$output" | sed 's/[A-Z_]*[0-9]*$//')"
check "publish ok: output holds the row" "x.loc"$'\t'"new"$'\t'"row" "$(sed -n 2p <<< "$output")"

run publish_fails
check "publish fails: non-zero exit" 1 "$rc"
check "publish fails: exactly one abort" "begin"$'\n'"publish"$'\n'"abort" "$calls"
check "publish fails: no output" "" "$output"
check "publish fails: says so" "yes" "$(grep -q 'Publishing the transaction on idc.galaxyproject.org failed' <<< "$last_log" && echo yes || echo no)"

run rehearsal
check "rehearsal: exit 0" 0 "$rc"
check "rehearsal: one abort, no publish" "begin"$'\n'"abort" "$calls"
check "rehearsal: no output" "" "$output"

run record_fails
check "record fails: non-zero exit" 1 "$rc"
check "record fails: aborted by the trap, never published" "begin"$'\n'"abort" "$calls"
check "record fails: no output" "" "$output"

[ "$failures" -eq 0 ] || { echo "$failures failure(s)"; exit 1; }
