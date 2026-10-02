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
W='THROTTLE_ROOT=$COPY "$PYTHON_BIN" "$REPO/tests/worker_cases.py"'
C='THROTTLE_ROOT=$COPY "$PYTHON_BIN" "$REPO/tests/config_cases.py"'
B='THROTTLE_ROOT=$COPY "$PYTHON_BIN" "$REPO/tests/gate_cases.py"'
I='THROTTLE_ROOT=$COPY "$PYTHON_BIN" "$REPO/tests/semaphore_runtime_cases.py"'
D='THROTTLE_ROOT=$COPY "$PYTHON_BIN" "$REPO/tests/identity_cases.py"'
L='THROTTLE_ROOT=$COPY "$PYTHON_BIN" "$REPO/tests/load_cases.py"'
R='THROTTLE_ROOT=$COPY "$PYTHON_BIN" "$REPO/tests/log_cases.py"'
GUARD=lib/agent_throttle/guard.py

# control: an unmodified copy must pass both batteries, or a "killed" below would prove nothing
COPY=$T/control; mkdir -p "$COPY"; cp -R "$REPO/bin" "$REPO/lib" "$REPO/config" "$COPY/"
control_ok=1
for battery in "$G" "$S" "$M" "$W" "$B" "$C" "$I" "$D" "$L" "$R"; do
  if ! COPY=$COPY eval "$battery" >> "$T/control.out" 2>&1; then control_ok=0; fi
done
if [ "$control_ok" = 1 ]; then
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
  '  watchdog_safe || exit 0' '  :' "$S"
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

check workers-fixed lib/agent_throttle/workers.py \
  'return max(1, min((cores + 1) // 2, cores // max(1, occupied)))' 'return 8' "$W"
check workers-raised lib/agent_throttle/workers.py \
  'if n is None or n > limit:' 'if True:' "$W"
check workers-unlimited lib/agent_throttle/workers.py \
  'if n is None or n > limit:' 'if False:' "$W"
check workers-no-injection lib/agent_throttle/workers.py \
  "if not seen and kind in ('jest', 'playwright'):" 'if False:' "$W"
check playwright-uncapped lib/agent_throttle/workers.py \
  "cap = wpw if kind == 'playwright' else w" 'cap = w' "$W"
check preload-no-cap lib/agent_throttle/workers-preload.cjs \
  'if (n === null || n > w)' 'if (false)' "$W"
check preload-no-injection lib/agent_throttle/workers-preload.cjs \
  'if (!seen)' 'if (false)' "$W"

check derived-limits-fixed lib/agent_throttle/config.py \
  "cfg['limits']['jest'] = [(c + 1) // 2, 2]" "cfg['limits']['jest'] = [4, 2]" "$C"
check explicit-limits-ignored lib/agent_throttle/config.py \
  '_merge(cfg, data)' '_merge(cfg, {k: v for k, v in data.items() if k != "limits"})' "$C"

check gate-percent-ignored lib/agent_throttle/gate.py \
  'if level < pct:' 'if False:' "$B"
check gate-mb-ignored lib/agent_throttle/gate.py \
  'if free < need:' 'if False:' "$B"
check gate-read-fails-open lib/agent_throttle/gate.py \
  "return 'memory: read failed'" "return ''" "$B"
check gate-history-ignored lib/agent_throttle/gate.py \
  'if len(samples) < 5:' 'if True:' "$B"
check gate-history-unbounded lib/agent_throttle/gate.py \
  'maxlen=50' 'maxlen=500' "$B"

check additional-slot-gate-bypassed bin/solo \
  'if [ "$k" -gt 1 ]; then' 'if false; then' "$I"
check slot-one-gated bin/solo \
  'if [ "$k" -gt 1 ]; then' 'if true; then' "$I"
check solo-workers-fixed bin/solo \
  'W=$(( C / active ))' 'W=8' "$I"

check owner-start-ignored bin/solo \
  '[ "$current" = "$stored" ]' 'return 0' "$D"
check legacy-lock-stolen bin/solo \
  '[ ! -f "$1/pid_start" ] && return 0' '[ ! -f "$1/pid_start" ] && return 1' "$D"
check stale-lock-kept bin/solo \
  '-gt "$STALE"' '-gt 9999999999' "$D"

check load-swap-delta-ignored lib/agent_throttle/load.py \
  "elif delta > settings['swap_delta_mb']:" 'elif False:' "$L"
check load-yellow-ignored lib/agent_throttle/load.py \
  "if pressure == 'yellow' and delta is not None and delta > 0:" 'if False:' "$L"
check load-red-ignored lib/agent_throttle/load.py \
  "if pressure == 'red':" 'if False:' "$L"
check load-thermal-ignored lib/agent_throttle/load.py \
  "if signals['thermal']:" 'if False:' "$L"
check load-read-fails-open lib/agent_throttle/load.py \
  'if missing:' 'if False:' "$L"
check load-free-ignored lib/agent_throttle/load.py \
  "if free is not None and free < settings['min_free_pct']:" 'if False:' "$L"
check load-agents-ignored lib/agent_throttle/load.py \
  "if total >= settings['max_agents']:" 'if False:' "$L"
check load-helpers-counted lib/agent_throttle/load.py \
  'excluded.intersection(tokens)' 'False' "$L"
check load-cores-fixed lib/agent_throttle/load.py \
  "cores=machine.cores() if override == 'auto' else machine.positive(override)" 'cores=4' "$L"
check load-swap-warning-ignored lib/agent_throttle/load.py \
  "if swap is not None and ram and swap > ram / 2**20 / settings['swap_warn_ram_divisor']:" 'if False:' "$L"
check load-window-last-sample lib/agent_throttle/load.py \
  'return current - previous' 'return 0' "$L"

check new-log-fields-dropped lib/agent_throttle/report.py \
  'rows.append([ts(parts[0])] + parts[1:])' 'rows.append([ts(parts[0])] + parts[1:7])' "$R"
check sampler-disabled bin/solo \
  'measured=$(tree_rss_mb "$child")' 'measured=0' "$I"
check wait-reason-unlogged bin/solo \
  '"$(sanitize "$WAIT_REASON")"' '""' "$I"
check origin-missing bin/solo \
  '"$(origin)"' '""' "$I"
check load-extra-columns-missing lib/agent_throttle/load.py \
  ", codex, f'{busy}/{slots}', workers]" ']' "$L"

check gate-too-few-samples lib/agent_throttle/gate.py \
  'if len(samples) < 5:' 'if len(samples) < 4:' "$B"

echo "$killed killed, $survived survived, $broken broken"
[ $survived = 0 ] && [ $broken = 0 ]
