#!/usr/bin/env bash
# Every battery, the mutants and the linters, in the same order as CI. Usage: bash tests/run_all.sh (~3 min).
# rc=0 = everything passed. Set PYTHON to test another interpreter (default: python3).
set -u
here=$(cd "$(dirname "$0")" && pwd)
cd "$here/.." || exit 2
PY=${PYTHON:-python3}
failed=""
step() { # step <name> <command...>
  local name=$1; shift
  if out=$("$@" 2>&1); then
    echo "ok     $name: $(printf '%s\n' "$out" | tail -1)"
  else
    echo "FAILED $name"; printf '%s\n' "$out" | tail -20 | sed 's/^/       /'; failed="$failed $name"
  fi
}
step guard "$PY" tests/guard_cases.py
step config "$PY" tests/config_cases.py
step identity "$PY" tests/identity_cases.py
step slots "$PY" tests/semaphore_runtime_cases.py
step gate "$PY" tests/gate_cases.py
step workers "$PY" tests/worker_cases.py
step machine "$PY" tests/machine_cases.py
step semaphore bash tests/semaphore_cases.sh
step solo-ci bash tests/solo_ci_cases.sh
step tools "$PY" tests/tools_cases.py
step install bash tests/install_cases.sh
step leak-cases bash tests/leak_cases.sh
step mutants bash tests/mutants.sh
if command -v shellcheck >/dev/null 2>&1; then
  step shellcheck shellcheck bin/solo bin/solo-ci bin/throttle-guard bin/throttle-load bin/throttle-clean install.sh uninstall.sh tests/*.sh
else
  echo "skip   shellcheck (not installed)"
fi
if command -v ruff >/dev/null 2>&1; then
  step ruff ruff check . bin/throttle-config bin/throttle-report bin/throttle-logrotate
elif command -v uvx >/dev/null 2>&1; then
  step ruff uvx ruff check . bin/throttle-config bin/throttle-report bin/throttle-logrotate
else
  echo "skip   ruff (not installed)"
fi
step leak-check "$PY" scripts/leak_check.py --history
if [ -n "$failed" ]; then echo "failed:$failed"; exit 1; fi
echo "all passed"
