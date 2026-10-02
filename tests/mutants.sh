#!/usr/bin/env bash
# shellcheck disable=SC2016 # the code snippets and battery commands are literal on purpose
# Mutation check: each mutant breaks one behavior in a COPY of the code, and the matching battery must fail.
# A mutant whose replacement text is not found is an error too (a mutation that does not apply proves nothing).
# Usage: bash tests/mutants.sh (rc=0 = every mutant was killed; takes ~2 min, the solo mutants run one at a time).
set -u
here=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$here/.." && pwd)
PYTHON_BIN=${PYTHON:-python3}
T=$(mktemp -d "${TMPDIR:-/tmp}/mutants.XXXXXX"); trap 'rm -rf "$T"' EXIT
killed=0; survived=0; broken=0

# mutate <file> <old> <new>: replace every occurrence; exit 3 if <old> is not there
mutate() {
  "$PYTHON_BIN" - "$1" "$2" "$3" <<'EOF'
import sys
path, old, new = sys.argv[1:4]
text = open(path).read()
if old not in text:
    sys.exit(3)
open(path, 'w').write(text.replace(old, new))
EOF
}

check() { # check <name> <file relative to the copy> <old> <new> <battery command using $COPY>
  local name=$1 rel=$2 old=$3 new=$4 cmd=$5
  COPY=$T/$name; rm -rf "$COPY"; mkdir -p "$COPY"
  cp -R "$REPO/bin" "$REPO/lib" "$REPO/config" "$COPY/"
  if ! mutate "$COPY/$rel" "$old" "$new"; then
    echo "BROKEN   $name (replacement text not found in $rel)"; broken=$((broken + 1)); return
  fi
  if COPY=$COPY eval "$cmd" > "$T/$name.out" 2>&1; then
    echo "SURVIVED $name"; survived=$((survived + 1))
  else
    echo "killed   $name ($(tail -1 "$T/$name.out"))"; killed=$((killed + 1))
  fi
}

G='GUARD_PATH=$COPY/lib/agent_throttle/guard.py "$PYTHON_BIN" "$REPO/tests/guard_cases.py"'
S='SOLO_PATH=$COPY/bin/solo bash "$REPO/tests/semaphore_cases.sh"'
M='THROTTLE_ROOT=$COPY "$PYTHON_BIN" "$REPO/tests/machine_cases.py"'
GUARD=lib/agent_throttle/guard.py

# control: an unmodified copy must pass both batteries, or a "killed" below would prove nothing
COPY=$T/control; mkdir -p "$COPY"; cp -R "$REPO/bin" "$REPO/lib" "$REPO/config" "$COPY/"
if COPY=$COPY eval "$G" > "$T/control.out" 2>&1 && COPY=$COPY eval "$S" >> "$T/control.out" 2>&1; then
  echo "control  unmodified copy passes"
else
  echo "CONTROL FAILED: the unmodified copy does not pass"; tail -5 "$T/control.out"; exit 2
fi

check covers-false $GUARD \
  '    return any(d == target or d.startswith(prefix) for d in dirs)' '    return False' "$G"
check jest-config-ignored $GUARD \
  '        base_dir, conf = jest_config(cwd, cfg)' '        base_dir, conf = cwd, {}' "$G"
check playwright-testdir-ignored $GUARD \
  "                dirs = [os.path.join(base_dir, m.group(2)) for m in re.finditer(rx, text)]" '                dirs = []' "$G"
check redirects-kept $GUARD \
  '    return drop_redirects(list(lex))' '    return list(lex)' "$G"
check semaphore-prefix-ignored $GUARD \
  '                in_sem = True' '                in_sem = False' "$G"
check jest-limits-swapped $GUARD \
  "    limit = LIMITS['jest'][0 if in_sem else 1]" "    limit = LIMITS['jest'][1 if in_sem else 0]" "$G"
check scripts-not-resolved $GUARD \
  '            if resolved and depth < 3:' '            if False:' "$G"
check watchdog-without-owner-check bin/solo \
  '  kill -0 $$ 2>/dev/null && [ "$(cat "$LOCK/pid" 2>/dev/null)" = "$$" ] || exit 0' '  :' "$S"
check kill-only-the-child bin/solo \
  '  pids=$(descendants "$1")' '  pids=$1' "$S"
check log-not-sanitized bin/solo \
  '"$(sanitize "$*")"' '"$*"' "$S"
check wait-counted-as-run bin/solo \
  'start=$(date +%s)' 'start=$t0' "$S"

check slots-fixed lib/agent_throttle/machine.py \
  'return max(1, min(c // max(1, c // 2), int(ram // 2**30 // 10)))' 'return 1' "$M"
check performance-cores-ignored lib/agent_throttle/machine.py \
  "positive(command('sysctl', '-n', 'hw.perflevel0.logicalcpu'))" 'None' "$M"
check linux-memory-unavailable lib/agent_throttle/machine.py \
  "available = info['MemTotal'], info['MemAvailable']" "available = info['MemTotal'], 0" "$M"

echo "$killed killed, $survived survived, $broken broken"
[ $survived = 0 ] && [ $broken = 0 ]
