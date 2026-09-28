#!/usr/bin/env bash
# shellcheck disable=SC2016,SC2034,SC2329 # checks are strings passed to eval: variables expand there
# Test battery for bin/solo. Usage: bash tests/semaphore_cases.sh (rc=0 = every case passed; takes ~25 s).
# Runs solo with a config that puts the lock and the log in a temporary directory (never the real lock or log) and a
# 1 s poll. Mutant: SOLO_PATH=<modified copy of bin/solo, next to a throttle-config> bash tests/semaphore_cases.sh.
set -u
here=$(cd "$(dirname "$0")" && pwd)
SOLO=${SOLO_PATH:-$here/../bin/solo}
T=$(mktemp -d "${TMPDIR:-/tmp}/semaphore-cases.XXXXXX")
cleanup() { pkill -f "^sleep (3[12]|60|77)$" 2>/dev/null; rm -rf "$T"; }
trap cleanup EXIT
cat > "$T/config.toml" <<EOF
[semaphore]
lock_dir = "$T/lock"
poll_s = 1
[logs]
dir = "$T/logs"
EOF
export AGENT_THROTTLE_CONFIG=$T/config.toml
unset SOLO_TIMEOUT
LOG=$T/logs/semaphore.log
cd "$T" || exit 2
fails=0; n=0
ok() { n=$((n + 1)); if eval "$2"; then echo "ok   $1"; else echo "FAILED $1"; fails=$((fails + 1)); fi; }
col() { awk -F'\t' -v l="$1" -v c="$2" 'NR == l + 1 { print $c }' "$LOG"; }   # line l (after the header), column c

"$SOLO" sh -c 'exit 3' > o1 2>&1; rc=$?
ok 'the command exit code is returned' '[ $rc = 3 ]'
ok 'TSV header with 7 columns' '[ "$(head -1 "$LOG")" = "$(printf "date\twait_s\tduration_s\trc\tcwd\tcommand\tnote")" ]'
ok 'normal line: rc=3 and empty note' '[ "$(col 1 4)" = 3 ] && [ -z "$(col 1 7)" ]'

t0=$(date +%s); SOLO_TIMEOUT=2 "$SOLO" sh -c 'sleep 31 & sleep 32' > o2 2>&1; rc=$?; el=$(( $(date +%s) - t0 )); sleep 1
ok 'timeout returns rc=124' '[ $rc = 124 ]'
ok 'timeout happens close to the limit (< 6 s)' '[ $el -lt 6 ]'
ok 'timeout kills child and grandchild' '[ "$(pgrep -f "^sleep 3[12]$" | wc -l | tr -d " ")" = 0 ]'
ok 'timeout releases the slot' '[ ! -d "$T/lock" ]'
ok 'timeout log line: rc=124 and note=timeout' '[ "$(col 2 4)" = 124 ] && [ "$(col 2 7)" = timeout ]'

SOLO_TIMEOUT=77 "$SOLO" true > o3 2>&1; sleep 1
ok 'no watchdog left after a normal run' '[ "$(pgrep -f "^sleep 77$" | wc -l | tr -d " ")" = 0 ]'

PLAYWRIGHT_HTML_OPEN=always "$SOLO" sh -c 'echo "PWO=$PLAYWRIGHT_HTML_OPEN"' > o4 2>&1
ok 'child gets PLAYWRIGHT_HTML_OPEN=never (even with always outside)' 'grep -q "^PWO=never$" o4'

SOLO_TIMEOUT=abc "$SOLO" true > o5 2>&1; rc=$?
ok 'invalid SOLO_TIMEOUT falls back to the configured value and runs' '[ $rc = 0 ] && grep -q "using 1200" o5'

( "$SOLO" sleep 4 > o6a 2>&1 & ); sleep 0.5; SOLO_TIMEOUT=2 "$SOLO" sleep 1 > o6b 2>&1; rc=$?; sleep 4
ok 'waiting for the slot does not count toward the timeout' '[ $rc = 0 ] && grep -q "wait [3-9]s" o6b'

( SOLO_TIMEOUT=3 "$SOLO" sleep 60 > o7a 2>&1 & ); sleep 1; kill -9 "$(cat "$T/lock/pid")"; sleep 0.5
"$SOLO" sh -c 'sleep 4; echo finished-normally' > o7b 2>&1; rc=$?
ok 'orphan watchdog (kill -9 on solo) does not kill the next solo' '[ $rc = 0 ] && grep -q finished-normally o7b'
ok 'the orphan child of the killed solo was reaped' 'grep -q "orphan lock of PID" o7b && [ "$(pgrep -f "^sleep 60$" | wc -l | tr -d " ")" = 0 ]'

ok 'no secret in the log' '! grep -q simulatedpass "$LOG"'   # (no case above uses it; regression guard)
"$SOLO" env DB_URL=postgresql://u:simulatedpass@h/x true > o8 2>&1
"$SOLO" true --api-token simulatedpass > o9 2>&1
ok 'VAR=value, --*token* values and URL credentials are masked' '! grep -q simulatedpass "$LOG" && grep -q "DB_URL=\*\*\*" "$LOG" && grep -q -- "--api-token \*\*\*" "$LOG"'

mkdir "$T/lock"; touch -t 200001010000 "$T/lock"
"$SOLO" true > o10 2>&1; rc=$?
ok 'a lock without owner older than stale_lock_s is removed (portable mtime)' '[ $rc = 0 ] && grep -q "lock without owner removed" o10'

"$SOLO" > o12 2>&1; rc=$?
ok 'no command: usage and exit 64' '[ $rc = 64 ] && grep -q usage o12'

echo "$((n - fails))/$n cases"
exit $((fails > 0))
