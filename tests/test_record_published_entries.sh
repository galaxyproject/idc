#!/usr/bin/env bash
# Tests record_published_entries and output_published_entries from .ci/jenkins.sh
# against temporary upper/lower directories, with a local stand-in for exec_on
# (which normally runs each command on the Stratum 0 over ssh).
#
#   bash tests/test_record_published_entries.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

tmp="$(mktemp -d)"
trap 'chmod -R u+rw "$tmp"; rm -rf "$tmp"' EXIT
failures=0

function log() { :; }
function log_error() { echo "ERROR: $*" >&2; }
EXEC_ON_FAIL=
function exec_on() {
    # Like ssh: join the arguments into one command line for a shell to parse.
    if [ -n "$EXEC_ON_FAIL" ] && [ "$1" = grep ]; then
        return "$EXEC_ON_FAIL"
    fi
    bash -c "$*"
}
eval "$(sed -n -e '/^function record_published_entries() {/,/^}/p' \
    -e '/^function output_published_entries() {/,/^}/p' .ci/jenkins.sh)"

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

OVERLAYFS_UPPER="$tmp/upper"
OVERLAYFS_LOWER="$tmp/lower"
mkdir -p "$OVERLAYFS_UPPER/config" "$OVERLAYFS_LOWER/config"
T=$'\t'

# A .loc the transaction created: every row is new, comments and blanks are not rows.
printf '%s\n' "#value${T}name" "fresh${T}q" "  # indented comment${T}x" "" "   " "crlf${T}row"$'\r' \
    > "$OVERLAYFS_UPPER/config/new.loc"
# An edited row (same value, new path) plus an unchanged one.
printf '%s\n' "#c" "3.0.1${T}3.0.1${T}old${T}/data/3.0.1" "3.1.0${T}3.1.0${T}mOTUs${T}/old" \
    > "$OVERLAYFS_LOWER/config/edited.loc"
printf '%s\n' "#c" "3.0.1${T}3.0.1${T}old${T}/data/3.0.1" "3.1.0${T}3.1.0${T}mOTUs${T}/new" \
    > "$OVERLAYFS_UPPER/config/edited.loc"
# Copied up but unchanged.
printf '%s\n' "same${T}row" > "$OVERLAYFS_LOWER/config/same.loc"
cp "$OVERLAYFS_LOWER/config/same.loc" "$OVERLAYFS_UPPER/config/same.loc"
# Not a .loc.
touch "$OVERLAYFS_UPPER/config/tool_data_table_conf.xml"

record_published_entries > /dev/null
# compgen -G does not promise an order across files, so compare sorted.
check "added and changed rows, whole" \
    "edited.loc${T}3.1.0${T}3.1.0${T}mOTUs${T}/new"$'\n'"new.loc${T}crlf${T}row"$'\n'"new.loc${T}fresh${T}q" \
    "$(LC_ALL=C sort <<< "$PUBLISHED_ENTRIES")"

# Handed to Actions only through $GITHUB_OUTPUT, as a multi-line output.
unset GITHUB_OUTPUT
check "no GITHUB_OUTPUT (Jenkins): a no-op" "0" "$(output_published_entries; echo $?)"
GITHUB_OUTPUT="$tmp/output"
output_published_entries
output="$(cat "$GITHUB_OUTPUT")"
delimiter="$(head -n1 <<< "$output")"; delimiter="${delimiter#published_entries<<}"
check "heredoc output" "published_entries<<${delimiter}"$'\n'"${PUBLISHED_ENTRIES}"$'\n'"${delimiter}" "$output"
check "random delimiter" "PUBLISHED_ENTRIES_" "${delimiter%%[0-9]*}"

# Nothing new: empty entries, no output.
rm "$OVERLAYFS_UPPER/config/new.loc" "$OVERLAYFS_UPPER/config/edited.loc" "$GITHUB_OUTPUT"
record_published_entries > /dev/null
check "unchanged .loc files add nothing" "" "$PUBLISHED_ENTRIES"
output_published_entries
check "empty entries: no output" "no" "$([ -e "$GITHUB_OUTPUT" ] && echo yes || echo no)"

# A comparison that fails must not pass for "no new rows".
for code in 2 255; do
    if EXEC_ON_FAIL="$code" record_published_entries > /dev/null 2>&1; then
        check "exec_on grep exit $code fails" "failure" "success"
    else
        check "exec_on grep exit $code fails" "failure" "failure"
    fi
done
if [ "$(id -u)" -ne 0 ]; then  # root reads anything
    chmod 000 "$OVERLAYFS_LOWER/config/same.loc"
    if record_published_entries > /dev/null 2>&1; then
        check "unreadable lower .loc (grep exit 2) fails" "failure" "success"
    else
        check "unreadable lower .loc (grep exit 2) fails" "failure" "failure"
    fi
    chmod 644 "$OVERLAYFS_LOWER/config/same.loc"
fi

[ "$failures" -eq 0 ] || { echo "$failures failure(s)"; exit 1; }
