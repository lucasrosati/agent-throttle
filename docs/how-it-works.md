# How it works

```
                 agents (Claude Code, Codex, terminals)
                         |
          Bash command   |   (Claude Code only)
                         v
                  throttle-guard  ---- blocked (exit 2 + reason) ---> guard-blocks.log
                         |
                     allowed
                         |
        heavy? --> solo <command>  ---- one at a time on the machine ---> semaphore.log
                         |
      launchd (macOS) -- throttle-load --log every 5 min ----------------> load.log
                     -- throttle-report every Monday ----------------> week-YYYY-WW.md
                     -- throttle-logrotate every day
```

## solo: the semaphore

`solo <command>` runs the command only when no other `solo` is running anywhere on the machine, and returns the
command's exit code.

- **The lock is a directory** (`[semaphore] lock_dir`, default `/tmp/agent-throttle.lock`) created with `mkdir`, which
  is atomic. The path is fixed on purpose: agent sandboxes may set their own `$TMPDIR`, and every process must see the
  same lock.
- **Inside the lock:** `pid` (the owner), `cmd` (folder and command, shown to whoever waits), `child` and `child_start`
  (the command's PID and its start time).
- **Waiting.** While the lock exists, `solo` prints who holds it and polls every `poll_s` seconds.
- **Dead owners.** If the owner PID no longer exists (killed with `kill -9`, terminal closed), the next `solo` kills the
  orphan command's process tree and takes the slot. The start time is compared before killing, so a reused PID is
  never touched. A lock with no `pid` file for longer than `stale_lock_s` (the owner died between `mkdir` and writing
  the pid) is removed too.
- **Timeout.** A watchdog kills the whole process tree after `timeout_s` (default 20 min, `SOLO_TIMEOUT` overrides),
  counted from the start of the command: waiting for the slot does not count. The exit code is then 124 and the log
  line gets the note `timeout`. An orphan watchdog (its `solo` was killed) only acts if its `solo` is alive and still
  owns the lock, so it never touches another run.
- **Signals.** Ctrl+C or TERM on `solo` kills the command's tree and releases the lock.
- **Environment.** `[semaphore.env]` is exported to the command; by default `PLAYWRIGHT_HTML_OPEN=never`.
- **Log.** One TSV line per run in `semaphore.log`, written while the slot is still held, so two runs never interleave.
  Values of `VAR=...`, of `--*token*`/`--*secret*`/`--*pass*`/`--*key*` options and credentials in URLs are replaced by
  `***`.
- **No Python dependency for the lock.** Settings come from `throttle-config shell`; if that fails, `solo` prints a
  warning and uses its built-in defaults.

Portability: macOS and Linux (bash 3.2+, `pgrep`, `ps -o lstart`, GNU or BSD `stat`).

### solo-ci

When the local queue is long, `solo-ci` pushes the current branch (never forced, never `main`/`master`, never with
uncommitted tracked changes) and waits for the pull request checks with `gh pr checks --watch --fail-fast`. A green
result with fewer checks than `min_checks` (or `git config agent-throttle.minchecks N` per repository) exits 3: a
workflow that never started does not show up as failed. Exit codes: 0 green, 1 failed, 2 refused before pushing, 3 too
few checks, 4 no open pull request, 5 push rejected, 124 timeout.

## throttle-guard: the hook

Claude Code runs `throttle-guard` before every Bash command (a `PreToolUse` hook with the `Bash` matcher). The hook
reads the command and its working directory as JSON on stdin; exit 2 blocks the command and the reason on stderr goes
back to the agent, which usually fixes the command right away.

1. **Fast path.** A command without any trigger word (test, jest, tsc, check, lint, e2e, playwright...) exits in bash
   without starting Python.
2. **Tokenizing.** Heredoc bodies are dropped, the text is split with `shlex` (quotes respected), redirections are
   removed token by token, and the command is cut into segments at `;`, `&&`, `||`, `|` and `&`. Text that only
   mentions a runner (`git commit -m "fix jest"`, `echo`, a heredoc) passes.
3. **Unwrapping.** Each segment is unwrapped until the real program shows up: `VAR=value`, `solo` (which marks the
   segment as inside the semaphore), `env`, `time`, `nice`, `npx`, `pnpm exec`/`dlx`, `npm exec`, `yarn`, `uv run`,
   `poetry run`, `python -m pytest`, `node <runner cli>`, `.venv/bin/...`, and `bash|sh|zsh -c "..."` (checked
   inside). `cd dir &&` is followed, so relative paths are resolved from the right folder.
4. **Scripts.** `npm test`, `pnpm test:e2e`, `yarn check` and the like are resolved from the `package.json` of that
   folder: the script body goes through the same rules, with the extra arguments appended the way npm does (npm only
   after `--`, pnpm with or without it) and npm's `pre`/`post` scripts. A test script that cannot be resolved
   (`--filter`, `-r`, unknown folder) only runs inside `solo`.
5. **Rules per runner** (ceilings from `[limits]`, inside/outside `solo`):

| Runner | Workers | Full suite outside `solo` | Always blocked |
|---|---|---|---|
| Jest | `--maxWorkers=N` or `-w N` or `--runInBand`, literal, <= ceiling | blocked; a path, `--testPathPattern`, `--changedSince`, `--findRelatedTests`, `-o` count as a target | `--watch`, `--watchAll` |
| Vitest | `--maxWorkers=N` or `--no-file-parallelism`, literal, even with a file | blocked; a path, `--changed` or `related` count as a target | watch (no `run`), `watch`, `dev`, `-w` |
| pytest | `-n N` literal when present (`auto`/`logical` blocked); no `-n` = 1 process | blocked; a path, `file::test` or `--lf` count; `-k` and `-m` do not | |
| Playwright | `--workers=N` or `-j N`, literal, required | blocked; a path, `--last-failed`, `--only-changed` count | `--ui`, `--debug`, `PWDEBUG`, `PWTEST_WATCH`, `show-report`, `codegen`, `open` |
| tsc | | project-wide (`-p`, `-b`, or no file argument) only inside `solo`; `tsc file.ts` passes | |
| scripts | | `[guard] heavy_scripts` (default `validate:local`, `typecheck`) only inside `solo` | |

6. **A path that IS the suite does not count as a target.** Passing the folder that contains every test is the same as
   running the full suite, so the guard reads the runner's config to know which folders those are:
   - pytest: the rootdir and every `testpaths` entry (from `pytest.ini`, `pyproject.toml`, `tox.ini` or `setup.cfg`,
     found walking up from the folder, or `-c`), globs included;
   - Vitest: the root and the fixed prefix of every `include` inside a `test:` block (the `include` of `coverage` and
     `optimizeDeps` does not count);
   - Jest: `rootDir`, `roots` and the fixed prefix of every `testMatch` (from `jest.config.*` or the `jest` key of
     `package.json`, found walking up, or `--config`), also for the value of `--testPathPattern`;
   - Playwright: the config folder and `testDir` (also `path.join(__dirname, ...)`).

   Any ancestor of those folders is also the suite. When the folder cannot be known (`$VAR`, `pnpm --filter`), any path
   counts as a target.
7. **Log.** Every block becomes a line in `guard-blocks.log` (command truncated to 120 characters, secrets masked).
   Failing to write the log never changes the decision. Invalid input fails open (the command passes).

`throttle-guard --check '<command>' [--cwd DIR]` runs the same rules by hand, without logging.

### Known gaps

- `vitest run test` is a name filter that may match every file; it counts as a target.
- Jest configured only with `testRegex`, and Jest `projects`, are not read.
- Config values that are not string literals (computed paths, imported constants) are not read.
- `node --test`, `python -m unittest` and `make` targets are not recognized.
- Custom `heavy_scripts` names must contain one of the fast-path trigger words.
- Codex does not run Claude Code hooks: for Codex the rules in `AGENTS.md` are the only protection.

## throttle-load: the launch gate

`throttle-load` prints free memory, swap, agent processes, the largest apps, test processes by worktree and who holds
`solo`, and ends with a verdict: "ok to launch" or "DO NOT launch" (free memory below `min_free_pct` or swap above
`max_swap_mb`). Agents read the verdict before starting another agent or a subagent that writes code.

`throttle-load --log` appends one sample to `load.log`; launchd runs it every 5 minutes. On macOS it reads
`kern.memorystatus_level` (the free percentage that `memory_pressure` prints), `kern.memorystatus_vm_pressure_level`
and `vm.swapusage`, and counts processes with one `ps`. It takes well under a second. Linux is not supported yet in
v0.1.0.

## throttle-clean

From any folder of a repository: `git worktree prune`, deletes build artifacts (`.next`, `coverage` by default) inside
the worktree folders (`.claude/worktrees`, `.worktrees` by default, never the main checkout), lists leftover test
processes and offers to kill them (only when run from a terminal).

## Configuration

Everything that depends on the machine lives in `config.toml` (`throttle-config path` shows where; `throttle-config
show` prints the effective values). Bash tools read it through `throttle-config shell`; Python tools import it. A wrong
value prints a warning and the default is used: a typo never disables a tool. See
[`config.example.toml`](../config/config.example.toml), including how to calibrate the numbers for your hardware.

## Files

| Path | What |
|---|---|
| `~/.local/bin/` | command links (install `--prefix`) |
| `~/.local/share/agent-throttle/` | installed files (`bin/`, `lib/`, templates) |
| `~/.config/agent-throttle/config.toml` | configuration |
| `~/.local/state/agent-throttle/` | `semaphore.log`, `guard-blocks.log`, `load.log`, `reports/` |
| `~/Library/LaunchAgents/dev.agent-throttle.*.plist` | launchd jobs (optional, macOS) |
| `~/Library/Logs/agent-throttle/` | output of the launchd jobs |

The XDG variables (`XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`) are respected.
