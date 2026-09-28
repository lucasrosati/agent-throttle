# Why agent-throttle exists

## The incident

A 16 GB laptop with 10 cores and no fan. Three coding agents, each in its own git worktree of the same monorepo,
finished their changes at about the same time, and each one ran the full Jest suite to validate its work. Jest's
default is one worker per core minus one, so every suite opened 9 workers. Together the three suites went past 27 GB of
memory, the machine swapped hard, and all three agent sessions died before opening a pull request. The work had to be
done again.

Two things were clear after that day:

- **Writing code in parallel is cheap; validating in parallel is not.** An agent session uses 0.3 to 0.8 GB. What takes
  the machine down is validation: full test suites, project-wide type-checks and e2e runs, several at the same time.
- **The agents followed their instructions.** Each one ran "the tests" before finishing, as it was told to. No single
  agent can see the others, so the coordination has to live on the machine, not in each agent's prompt.

## Diagnosis: measured, not guessed

All numbers below come from the same laptop, one suite at a time, with nothing else heavy running, using the median of
alternated runs (A B A B). They are the reason for each default in [`config.example.toml`](../config/config.example.toml).

### Workers: few are enough when only one suite runs

The web package of the monorepo (~190 suites, ~2,000 tests, Jest with ts-jest), warm cache:

| Workers | Time | Peak memory |
|---|---|---|
| 2 | 31.7 s | 3.7 GB |
| 4 | 21.5 s | 4.0 GB |

The peak barely moves with the worker count while the time drops by a third. On the API package (~340 suites of
lighter tests), 4 workers ran in 15.1 s against 16.1 s with 2, which is within the noise. So: when the machine
guarantees that only ONE suite runs at a time, 4 workers cost about 0.3 GB and pay off. When several agents iterate at
the same time, each gets 2. That is the `[4, 2]` ceiling (inside the semaphore, outside it).

### Vitest opens one worker per core, even for three files

On two small Vitest suites, the default opened 9 workers. With 2 workers the peak fell almost by half (0.73 to 0.39 GB
on one, 0.84 to 0.47 GB on the other) and the time did not change in any meaningful way. The guard requires a literal
`--maxWorkers` even when a single file is passed.

### Playwright: each worker is a browser

A synthetic suite (8 specs, headless Chromium, no app server) used about 0.58 GB per worker, linearly:

| Workers | Time | Peak memory |
|---|---|---|
| 1 | 23.4 s | 0.75 GB |
| 2 | 11.6 s | 1.32 GB |
| 4 | 6.2 s | 2.48 GB |
| default (5) | 6.1 s | 3.01 GB |

Above 4 there is no gain. A real e2e run also carries the app server (and often a production build), so the ceiling is
lower: 2 inside the semaphore, 1 outside.

### Cold worktrees are the worst case

A fresh worktree starts with an empty transform cache. The same web suite took about 135 s and peaked at about 6 GB
cold (ts-jest with type-checking), against about 22 s warm. Two attempts:

- **Sharing Jest's `cacheDirectory` between worktrees did not help.** The cache key includes the project root, so each
  worktree created its own entry: zero reuse.
- **`isolatedModules` did.** Transpiling without type-checking (the type-check moved to a separate `tsc` step, which
  runs through the semaphore) brought the cold run to about 33 s and the peak to 3.0 to 3.8 GB.

### What did not work

`workerIdleMemoryLimit=1GB` (Jest recycles a worker when it grows) cut the peak by a third with 2 workers, but made the
suite 80% to 218% slower, because every recycled worker compiles everything again. Rejected.

### Thermal throttling

A laptop without a fan slows down under sustained load: the same suite went from 22 s to 34 s after about 40 minutes of
back-to-back runs. Compare measurements only within the same session, alternating the variants.

### Commands that never end

- **Watch mode** (`vitest` without `run`, `jest --watch`) never ends. Inside a semaphore it would hold the slot forever,
  so the guard blocks it everywhere.
- **Playwright's html reporter**, after a failure with stdin attached to a terminal, serves the report and waits for
  Ctrl+C. `solo` exports `PLAYWRIGHT_HTML_OPEN=never` to every command it runs.

## What changed

| Problem | Tool |
|---|---|
| Several heavy validations at once | `solo`: one at a time on the machine, with a timeout |
| Default worker counts sized for an idle machine | `throttle-guard`: literal ceilings, higher inside `solo` than outside |
| Launching one more agent into a machine already swapping | `throttle-load`: a verdict before each launch |
| Not knowing whether the limits hold | logs of every run, block and memory sample, and a weekly report |

With this in place (and `isolatedModules` in the suite), the same laptop went from at most 3 agents coding at once to 5,
with full suites of 20 to 30 s and peaks around 3.5 GB. The weekly report is how you check that such a ceiling keeps
holding: see [metrics.md](metrics.md).

## The guard was checked against real traffic

Before it was trusted, the guard was replayed against about 8,800 Bash commands taken from real agent transcripts,
comparing its decisions before and after each change. The replay found two bugs that unit cases had missed:

- `npx jest --ci --maxWorkers=2 2>&1 | tail -3` passed as if `2>` were a file target. Redirections are now removed after
  tokenizing, token by token.
- A regular expression on the raw text broke the tokenization of commands with `<` or `>` inside quotes.

Both are cases in [`tests/guard_cases.py`](../tests/guard_cases.py) now.
