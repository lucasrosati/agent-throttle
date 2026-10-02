# Metrics

The limits are guesses until the machine says otherwise. agent-throttle keeps three logs and turns them into a weekly
report, so you can tell whether the ceilings hold, whether the queue is too long, and which rules agents keep hitting.

## Logs

All in `[logs] dir` (default `~/.local/state/agent-throttle/`), tab-separated with a header line, one line per event.
`throttle-logrotate` keeps the last `retention_days` (default 60); launchd runs it daily.

### semaphore.log (solo and solo-ci)

| Column | Meaning |
|---|---|
| `date` | end of the run, `YYYY-MM-DD HH:MM:SS` |
| `wait_s` | seconds waiting for the slot (empty for solo-ci) |
| `duration_s` | seconds running |
| `rc` | exit code (124 = timeout) |
| `cwd` | folder |
| `command` | the command, with secrets masked |
| `note` | `timeout`, `ci`, or empty |
| `slot` | acquired slot/total, such as `2/2` |
| `w` | worker budget fixed at acquisition |
| `w_adjust` | flags lowered or injected, including preload changes |
| `origin` | nearest recognized parent: `claude`, `codex`, or `manual` |
| `peak_mb` | peak sampled RSS of the child process tree, every 2 seconds; fast commands may record 0 |
| `wait_reason` | memory gate reason or `slots busy`, empty without waiting |

The first seven columns are unchanged. Readers accept seven-column, extended and mixed logs; existing headers and rows
are not converted. `solo-ci` continues to write its original seven columns.

### guard-blocks.log (throttle-guard)

`date`, `cwd`, `rule` (for example `jest-no-workers`, `vitest-suite-outside-semaphore`, `watch-blocked`) and
`command` (first 120 characters, secrets masked). `--check` runs are not logged. Set `[guard] log_blocks = false` to
turn it off.

### load.log (throttle-load --log, every 5 min)

| Column | Meaning |
|---|---|
| `free_pct` | free memory percentage (macOS sysctl or Linux MemAvailable) |
| `pressure` | `green`, `yellow`, `red`, `unknown` (macOS pressure or Linux PSI) |
| `swap_mb` | swap in use |
| `agent_sessions` | processes named like `[load] agent_processes` (idle sessions count too) |
| `test_procs` | processes matching `[load] test_process_regex` |
| `semaphore_busy` | `yes` if any slot directory exists |
| `codex_agents` | Codex subset of `agent_sessions`, with helper processes excluded |
| `slots` | busy/total slots, such as `2/2` |
| `workers` | sum of stored W across occupied slots; legacy locks without W contribute 0 |

`date` remains the first column. `agent_sessions` remains the total across all configured agent names; do not add
`codex_agents` to it. Old seven-column rows remain readable. Missing signals are logged empty/unknown and never imply a
safe launch verdict. A fresh sample within 30 minutes is required for the swap-growth verdict.

A sleeping machine takes no samples; the report accounts for the gaps.

## Weekly report

```bash
throttle-report                 # current ISO week, so far, written to <logs>/reports/week-YYYY-WW.md
throttle-report --last-week     # the week that ended on Sunday (what launchd runs on Monday at 08:00)
throttle-report --stdout        # print instead of writing
```

### Targets

| Target | Goal | Why |
|---|---|---|
| Dead sessions | 0 | the failure this project exists to prevent |
| p95 of the wait for the slot | below `wait_p95_target_s` (3 min) | above it, agents sit idle; consider `solo-ci` or fewer agents |
| Lowest free memory | at least `min_free_target_pct` (20%) | the margin that keeps swap from growing |
| Red pressure with many agent processes | 0 min | the number that confirms or refutes `max_agents` |
| Guard blocks | falling week over week | agents are learning the rules (or the rules are in their instructions) |

**Dead sessions** are inferred: two consecutive samples (at most 15 minutes apart) where the agent process count drops
by 2 or more while swap is above `max_swap_mb` or pressure is red. Closing sessions by hand while the machine is
swapping also matches, so the report lists every event for you to check.

### Sections

- **Memory and agent processes:** minutes in red and yellow pressure, the lowest free memory and what was running then,
  swap from first to last sample with the peak and the largest rise in a day, the peak of agent processes, and a table
  of time and pressure by process count.
- **solo:** wait for the slot (median, p95, max), runs and failures, timeouts, solo-ci runs, and the median duration by
  repository and suite, morning against afternoon (thermal throttling shows up here).
- **Guard blocks:** by rule, this week against the previous one.
- **Pull requests** (optional): with `[report] repos_dir` set and `gh` logged in, the pull requests opened and merged
  in the week per repository found there.

## Reading the numbers

- Red minutes with many agents and a low free percentage: lower `max_agents`, or the inside worker ceiling.
- Long waits and no red: the machine has room; the queue is the bottleneck. Keep the ceilings, lean on `solo-ci`, or
  make the suites faster (the cold-worktree case in [why.md](why.md) is usually the biggest win).
- Many `*-no-workers` blocks: an instruction file is missing the rules, or a `package.json` script needs a literal
  worker count.
- A timeout in the report: something hung (watch mode through a script, a server waiting for input) or a suite outgrew
  `timeout_s`.
