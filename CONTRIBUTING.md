# Contributing

Thanks for helping. Issues and pull requests are welcome, especially:

- **Linux support for `throttle-load`** (free memory and pressure from `/proc/meminfo` and PSI in
  `/proc/pressure/memory`, swap from `/proc/swaps`) and **systemd user timers** for the three scheduled jobs.
- **Guard gaps** listed in [docs/how-it-works.md](docs/how-it-works.md#known-gaps): Jest `projects` and `testRegex`,
  `node --test`, `python -m unittest`, `make` targets.
- **A hook for Codex**, if its hook system can block shell commands the way Claude Code's `PreToolUse` does.
- Measurements from other hardware for the calibration guide (anonymized: sizes and timings, not project names).

## Running the tests

No dependencies besides bash, git and Python 3.11+. `bash tests/run_all.sh` runs everything below, as CI does.

```bash
python3 tests/guard_cases.py        # the guard's rules (255 cases)
python3 tests/config_cases.py       # config loading and the hook end to end
bash tests/semaphore_cases.sh       # solo (~25 s: real timeouts)
bash tests/solo_ci_cases.sh         # solo-ci refusals (no GitHub needed)
python3 tests/tools_cases.py        # report, logrotate, load, clean
bash tests/install_cases.sh         # install/uninstall in a temporary HOME
bash tests/leak_cases.sh            # the leak check itself
bash tests/mutants.sh               # every mutant must be killed (~2 min)
```

Every test works in temporary folders: none touches your real lock, logs, config or `~/.claude`.

Lint, as in CI:

```bash
shellcheck bin/solo bin/solo-ci bin/throttle-guard bin/throttle-load bin/throttle-clean install.sh uninstall.sh tests/*.sh
ruff check . bin/throttle-config bin/throttle-report bin/throttle-logrotate
python3 scripts/leak_check.py --history
```

## Guidelines

- **A new guard rule comes with cases** in `tests/guard_cases.py`: at least one that must be blocked and one that must
  pass, and if the rule reads a config file, a fixture in `TREES` with an invented repository.
- **A fix for a false negative or a false positive starts with the failing case.**
- **Mutants:** if you add a behavior worth protecting, add a mutant to `tests/mutants.sh` and check that it is killed.
- **Portability:** bash 3.2 (macOS) and GNU tools must both work. No `readlink -f`, no associative arrays, GNU
  `stat -c` before BSD `stat -f`.
- **Nothing personal in the repository:** use `/Users/you`, `/home/you` and `~/code/app` in examples. The public leak
  check runs in CI.
- Docs are in English; the README also has a Portuguese version, keep both in sync.
- Style: direct prose, no emojis.
