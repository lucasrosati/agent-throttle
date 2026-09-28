# Rules for agents

The hook enforces what it can see in one command. The rules explain the why to the agents and cover what no hook can
see: whether to launch another agent, what to do when the queue is long, cleaning up at the end. You need both.

## Where the rules go

| Agent | File | Block to paste |
|---|---|---|
| Claude Code, every project on the machine | `~/.claude/CLAUDE.md` | [`templates/claude-md-rules.md`](../templates/claude-md-rules.md) |
| Codex CLI | `~/.codex/AGENTS.md` | [`templates/agents-md-rules.md`](../templates/agents-md-rules.md) |

`install.sh` copies both templates to `~/.local/share/agent-throttle/templates/` but does not edit your instruction
files. If you changed `[limits]` or `max_agents`, update the numbers in the block you paste.

**Codex has no Claude Code hook**, so for Codex the rules are the only protection. The Codex block says so explicitly,
which makes agents take it more seriously.

## What the rules say, and why

- **Check `throttle-load` before launching an agent.** Launching into a machine that is already swapping is how
  sessions die. The verdict is cheap (under a second) and binary.
- **Heavy validation only through `solo`.** The list is explicit (full suites, including a folder that is the suite;
  project-wide type-check; whole-repository lint; scripts like `validate:local`) because agents otherwise decide
  "heavy" by feel.
- **Read the real exit code on the last line**, `[solo] rc=N`. Agents often pipe output through `tail`, which hides the
  command's exit code; `solo` prints it last.
- **Literal worker counts.** `50%` and `auto` depend on the machine and on what else is running; a literal number can
  be reasoned about.
- **Nothing that does not end.** A watch process inside `solo` holds the slot until the timeout; outside it, it leaks
  memory until someone notices.
- **Iterate on the file you touched.** The full suite is for the end, once, through `solo`.
- **`solo-ci` after 10 minutes of waiting.** A long queue is a sign that the machine is the bottleneck; the CI runner is
  idle.
- **Do not work around a block.** A blocked command comes with the rule and the allowed form. Agents that wrap commands
  in variables or scripts to get past the hook defeat the point.
- **Clean up.** Background shells, watchers and dev servers left behind are the slow leak that turns an ok verdict into
  a red one by the afternoon.

## Rules for the team vs rules for your machine

Keep the two apart:

- **A repository's `CLAUDE.md` or `AGENTS.md` (versioned) carries only what is true for everyone**: how to run a
  single test file with a literal worker count, which script is the full validation. Not `solo`, not paths under your
  home folder: your teammates may not have them.
- **Machine rules for one repository stay outside it.** Keep them in a file under your home folder and bring them in
  with a symlink named `CLAUDE.local.md` at the root of the checkout, listed in `.git/info/exclude`. Git worktrees
  created under `.claude/worktrees/` read the checkout's `CLAUDE.local.md` too. Prefer the symlink over an `@import` of
  a file outside the project: see [the lesson on external imports](lessons/claude-md-external-imports.md).

```bash
cd ~/code/app
ln -s ~/agent-rules/app.md CLAUDE.local.md
echo CLAUDE.local.md >> .git/info/exclude
```

## Checking that an agent sees the rules

```bash
cd ~/code/app
claude -p "Which command do you use for a full test run on this machine? Quote the rule."
```

The answer should quote the `solo` rule. If it does not, the file is not being loaded: check the path, the symlink and
the lesson above.
