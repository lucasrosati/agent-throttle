<!--
agent-throttle: rules block for Codex. Paste it into your global AGENTS.md (for the Codex CLI, ~/.codex/AGENTS.md).
Codex does not run the Claude Code hook, so for Codex these rules are the only protection: keep them explicit.
If you changed [limits] or max_agents in your config, update the numbers below (`throttle-config get limits`).
-->
## Machine resources (agent-throttle, mandatory)

Several coding agents share this machine (Codex, Claude Code and any other runner). Codex agents do NOT go through
the hook that blocks heavy validation in Claude Code: these rules are the only protection. They exist because full
test suites started by parallel agents at the same time once took a 16 GB laptop past 27 GB and killed every session.

- **Heavy validation only through `solo <command>`** (a semaphore: hardware-derived slots shared by the machine; when slots are busy it waits). Heavy means: a full test suite (jest, vitest, pytest, playwright, go test, cargo test...), including a
  folder that IS the suite (`pytest tests` when `tests` is the `testpaths`, `vitest run src` when `src/**` is the
  `include`, `jest src` when `src` is the `rootDir`/`roots`/`testMatch` prefix, `playwright test e2e` when `e2e` is the
  `testDir`, or the repository root); a project-wide type-check (`tsc --noEmit`, mypy, pyright); a whole-repository
  lint; scripts like `validate:local` and `typecheck`.
  Example: `solo pnpm run typecheck 2>&1 | tail -n 80`. The real exit code is the last line, `[solo] rc=N`. Each run
  has a 20-minute timeout: rc=124 means it was hit and the command's process tree was killed.
- **Literal worker counts**, with a ceiling inside/outside `solo`: Jest `--maxWorkers` ceil(C/2)/2, Vitest `--maxWorkers` 4/2 (even with an explicit file), pytest `-n` 4/2,
  Playwright `--workers` min(ceil(C/2), 4)/1. Explicit config overrides these; solo only lowers counts to the slot budget. Never `auto`, `50%` or a variable.
- **Nothing that does not end:** no watch mode (`vitest` without `run`, `jest --watch`), no `playwright test --ui` or
  `--debug`, no `PWDEBUG`, no `playwright show-report`. The full e2e suite only inside `solo`.
- **Iterate on the test file you touched**, with the outside ceiling; mutation checks run on the spec, never on the suite.
- **Stay within the effective `load.max_agents` ceiling** (`throttle-config get load.max_agents`, counting every tool), and only if `throttle-load` says "ok to
  launch" before each new agent or subagent (free memory, pressure, swap growth, thermal status and read failures all affect the verdict).
- **If `solo` has been waiting for more than 10 minutes**, use `solo-ci` (pushes the branch without force and waits for
  the pull request CI) as the final validation, and say so in your report.
- **Before finishing**, kill the background shells, watchers and dev servers you started; do not start a dev server
  unless asked.
