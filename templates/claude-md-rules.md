<!--
agent-throttle: rules block for Claude Code. Paste it into ~/.claude/CLAUDE.md (every project on this machine).
The throttle-guard hook enforces the worker and suite rules; this block explains them and covers what a hook cannot
see (launching agents, cleaning up, what to do when the queue is long). If you changed [limits] or max_agents in your
config, update the numbers below (`throttle-config get limits`, `throttle-config get load.max_agents`).
-->
## Machine resources (agent-throttle)

Several coding agents share this machine. Heavy validation runs one at a time and test parallelism is capped.

- **Before launching an agent or a subagent that writes code**, run `throttle-load` and launch only on "ok to launch"
  (free memory below 25% or swap above 3 GB means do not launch). Up to 5 agents coding at once on this machine.
- **Heavy validation only through `solo <command>`** (one at a time on the machine; if the slot is taken it waits):
  a full test suite, including a folder that IS the suite (the pytest `testpaths`, the prefix of the Vitest `include`,
  the Jest `rootDir`/`roots`/`testMatch` prefix, the Playwright `testDir`, or the repository root); a project-wide
  type-check (`tsc --noEmit`, mypy, pyright); a whole-repository lint; scripts like `validate:local` and `typecheck`.
  Example: `solo pnpm run typecheck 2>&1 | tail -n 80`. The real exit code is the last line, `[solo] rc=N`. Each run
  has a 20-minute timeout: rc=124 means it was hit and the command's process tree was killed.
- **Literal worker counts**, with a ceiling inside/outside `solo`: Jest and Vitest `--maxWorkers` 4/2 (Vitest even with
  an explicit file), pytest `-n` 4/2, Playwright `--workers` 2/1. Never `auto`, `50%` or a variable.
- **Nothing that does not end:** no watch mode (`vitest` without `run`, `jest --watch`), no `playwright test --ui` or
  `--debug`, no `PWDEBUG`, no `playwright show-report`.
- **Iterate on the file you touched**, with the outside ceiling: `npx jest path/to/file.test.ts --maxWorkers=2`.
  Mutation checks run on the spec, never on the whole suite.
- **If `solo` has been waiting for more than 10 minutes**, use `solo-ci` (pushes the branch without force and waits for
  the pull request CI) as the final validation, and say so in your report.
- **When throttle-guard blocks a command**, read the reason: it names the rule and the way to run it within the limits.
  Do not work around it.
- **Before ending the session**, kill the background shells, watchers and dev servers you started.
