# Changelog

All notable changes to this project are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/)
and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-09-28

First public version.

### Added

- `solo`: machine-wide semaphore for heavy validation, with timeout, cleanup of dead owners and orphan commands, the
  command's real exit code, environment exports and a TSV log with secrets masked. macOS and Linux.
- `solo-ci`: final validation through the pull request CI (no force push, never `main`/`master`, minimum check count).
- `throttle-guard`: Claude Code `PreToolUse` hook with literal worker ceilings for Jest, Vitest, pytest and Playwright
  (higher inside `solo`), full suites only inside `solo` (including a path that is the whole suite, read from the
  runner's config), watch and interactive modes always blocked, project-wide `tsc` and heavy scripts only inside
  `solo`, `package.json` scripts resolved. `--check` for manual use.
- `throttle-load` (macOS): launch verdict from free memory and swap; `--log` for samples every 5 minutes.
- `throttle-clean`, `throttle-report` (weekly Markdown report), `throttle-logrotate`, `throttle-config`.
- `config.toml` with documented defaults, `max_agents` derived from total RAM, and a calibration guide.
- `install.sh` / `uninstall.sh`: idempotent, hook merged into Claude Code settings with a backup, optional launchd jobs.
- Rule templates for `~/.claude/CLAUDE.md` and Codex `AGENTS.md`.
- Tests: guard (255 cases), config and hook (31), semaphore (18), solo-ci (6), tools (22), install (32), leak check
  (12), and 11 mutants with an unmodified control. CI on Ubuntu and macOS, shellcheck, ruff, public leak check.
