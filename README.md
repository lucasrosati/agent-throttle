# agent-throttle

Run several coding agents (Claude Code, Codex) on one machine without taking it down. Heavy validation in hardware-derived slots,
literal worker ceilings enforced by a hook, a memory check before each new agent, and the metrics to tune all of it.

[Leia em português](README.pt-BR.md)

## Quickstart

```bash
git clone https://github.com/lucasrosati/agent-throttle && ./agent-throttle/install.sh
solo sleep 3                          # runs through the machine-wide semaphore: [solo] rc=0 in 3s (wait 0s)
throttle-guard --check 'npx jest'     # blocked (jest-no-workers): Jest needs --maxWorkers=N (<=2 outside solo).
```

The first line installs the commands into `~/.local/bin` and adds the hook to `~/.claude/settings.json` (backup first,
your other hooks kept). Run `solo sleep 3` in more terminals than the available slots to see the excess wait. The third line shows the
check that Claude Code now runs before every Bash command. Then paste the rules for your agents
([below](#rules-for-your-agents)).

## Status

- **Version 0.1.0**, the first public release.
- **Tested on macOS and Linux** (the guard and the semaphore): every test runs in CI on `ubuntu-latest` and
  `macos-latest`.
- **Next release (Unreleased):** hardware-derived slots and workers, a memory gate, PID + start-time locks and a portable launch verdict.
- **Scheduled jobs:** launchd on macOS; Linux timers remain a separate contribution.
- **Runtime defaults:** slots and inside Jest/Playwright ceilings are derived from cores and RAM at runtime.
  Use the weekly report to tune your configuration.

## The problem

Agents write code in parallel just fine: a session uses well under 1 GB. Validation is what takes a machine down. Three
agents that finished at the same time each ran the full Jest suite with the default workers (one per core), and a
16 GB laptop went past 27 GB of memory; all three sessions died before opening a pull request. No agent did anything
wrong by its own instructions: each one ran the tests before finishing. The coordination has to live on the machine.
The full story and the measurements behind every default are in [docs/why.md](docs/why.md).

## What you get

| Command | What it does |
|---|---|
| `solo <command>` | Runs in an available hardware-derived slot shared by all agents and terminals. Lowers worker counts per slot, gates extra slots on memory, handles timeout and dead owners, and logs each run. |
| `solo-ci` | When the queue is long: pushes the branch (never forced, never `main`) and waits for the pull request CI instead. |
| `throttle-guard` | Claude Code `PreToolUse` hook. Blocks Jest, Vitest, pytest and Playwright runs without a literal worker count or above the ceiling, full suites outside `solo` (also when the path you pass IS the suite), watch modes, interactive runners, and project-wide `tsc`. Understands `npx`, `pnpm`, `uv run`, `package.json` scripts and `bash -c`. |
| `throttle-load` | Memory snapshot with a verdict before launching another agent: "ok to launch" or "DO NOT launch". Uses live memory, pressure, swap growth, thermal signals and agent counts. With `--log`, one sample for the metrics. macOS and Linux. |
| `throttle-clean` | End of day: prunes worktrees, deletes build output inside them, offers to kill leftover test processes. |
| `throttle-report` | Weekly Markdown report: dead sessions, memory pressure, wait for the slot, durations, timeouts, guard blocks by rule. |
| `throttle-logrotate` | Keeps the last 60 days of logs. |
| `throttle-config` | Shows the effective configuration. |

Defaults for worker ceilings, inside `solo` / outside:

| Runner | Inside `solo` | Outside |
|---|---|---|
| Jest `--maxWorkers` | ceil(C/2) | 2 |
| Vitest `--maxWorkers` | 4 | 2 |
| pytest `-n` | 4 | 2 |
| Playwright `--workers` | min(ceil(C/2), 4) | 1 |

Inside ceilings are derived from cores and RAM at runtime; explicit `[limits]` pairs override them. C is the
performance-core count on macOS (logical CPUs as fallback), or `nproc` on Linux. `solo` fixes
W = min(ceil(C/2), floor(C/occupied_slots)) at acquisition and only lowers flags. Playwright is capped at 4 by default.
Slots = max(1, min(floor(C/max(1, floor(C/2))), floor(RAM_GB/10))); override slots/cores through config or `SOLO_SLOTS` /
`SOLO_CORES`. Extra slots need at least 25% free memory and the recent suite peak p90; slot 1 bypasses this gate.

Agents coding at once (`max_agents`) default to
`floor((RAM in GB - 6) / 2)`, between 1 and 12. This is a memory budget; set an integer for your separate quota ceiling.

## Install

Requirements: macOS or Linux, bash, git, Python 3.11 or newer. `gh` for `solo-ci` and the optional pull request count.

```bash
git clone https://github.com/lucasrosati/agent-throttle
cd agent-throttle
./install.sh                 # options: --prefix DIR, --python PATH, --settings FILE, --no-hook, --launchd, --force
```

`install.sh` can run again at any time (it updates what it installed). It:

- copies the files to `~/.local/share/agent-throttle` and links the commands into `~/.local/bin` (an existing command
  with the same name is left alone unless `--force`);
- pins the Python it found, because launchd and hooks do not read your shell profile;
- creates `~/.config/agent-throttle/config.toml` from the example if you have none;
- merges the hook into `~/.claude/settings.json`: backup to `settings.json.bak-agent-throttle-<date>`, your other hooks
  and settings kept, JSON validated after writing, nothing written if the file is not valid JSON;
- with `--launchd` (macOS), asks and then loads three jobs: a memory sample every 5 minutes, the weekly report on
  Monday at 08:00, the daily log rotation.

`./uninstall.sh` removes the hook (with a backup), the jobs, the links and the installed files. The config and the
logs stay unless you add `--purge`.

## Configure

`throttle-config path` shows where the config lives; `throttle-config show` prints the effective values. The
[example config](config/config.example.toml) documents every key and ends with a section on how to calibrate the
numbers for your hardware with `throttle-load` and a suite measured through `solo`. A wrong value prints a warning and
falls back to the default: a typo never turns a tool off.

## Rules for your agents

The hook covers what it can see in one command. Paste the rules so agents also know when to launch another agent, how
to read `solo` output and what to do when the queue is long:

- Claude Code: [templates/claude-md-rules.md](templates/claude-md-rules.md) into `~/.claude/CLAUDE.md`.
- Codex: [templates/agents-md-rules.md](templates/agents-md-rules.md) into `~/.codex/AGENTS.md`. Codex does not run
  Claude Code hooks, so for Codex the rules are the only protection.

More in [docs/rules-for-agents.md](docs/rules-for-agents.md), including how to keep per-repository machine rules out
of the repository.

## Platform support

| | macOS | Linux |
|---|---|---|
| `solo`, `solo-ci`, `throttle-guard`, `throttle-config`, `throttle-clean`, `throttle-logrotate` | yes | yes |
| `throttle-report` | yes | yes |
| `throttle-load` | yes | yes, with conservative signal fallbacks |
| scheduled jobs | launchd | not yet (systemd timers welcome) |

Built for Claude Code 2.1 (the hook) and the Codex CLI 0.15 (rules only, no hook). CI runs every test on
`ubuntu-latest` and `macos-latest`.

## Known limitations

- Codex has no hook here: rules only.
- The guard does not read Jest `projects` or `testRegex`-only configs, nor config values that are not string literals;
  `node --test`, `python -m unittest` and `make` are not recognized. See
  [docs/how-it-works.md](docs/how-it-works.md#known-gaps).
- The Node preload covers Jest/Playwright in npm/pnpm/yarn scripts. `cross-env NODE_OPTIONS=...` replaces it; the guard
  still requires literal flags. Node is optional for non-Node commands.
- Linux memory pressure uses PSI (`some.avg10` >= 1% yellow, `full.avg10` >= 10% red, configurable). Missing PSI or usable
  hot/critical thermal trips closes the launch verdict. These signals are not interchangeable with macOS pressure.
- A launch verdict needs a swap sample within 30 minutes: `throttle-load --log`. Total swap above RAM/8 only warns unless
  you explicitly retain the legacy `max_swap_mb` limit. Scheduled jobs remain macOS-only.

## Docs

- [Why](docs/why.md): the incident and the measurements.
- [How it works](docs/how-it-works.md): the semaphore, the hook's rules, the files.
- [Rules for agents](docs/rules-for-agents.md).
- [Metrics](docs/metrics.md): the logs and the weekly report.
- Lessons: [repositories inside synced folders](docs/lessons/icloud-synced-folders.md),
  [`@import` from outside the project in CLAUDE.md](docs/lessons/claude-md-external-imports.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Linux scheduled jobs and measurements from other hardware are welcome contributions.

## License

[MIT](LICENSE)
