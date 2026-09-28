#!/usr/bin/env python3
"""Cases for throttle-report, throttle-logrotate, throttle-load and throttle-clean, with synthetic logs in a temporary
directory (never the real logs). Usage: python3 tests/tools_cases.py (rc=0 = every case passed)."""
import datetime as dt
import os
import platform
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), 'bin')
TMP = tempfile.mkdtemp(prefix='tools-cases-')
LOGS = os.path.join(TMP, 'logs')
os.makedirs(LOGS)
CFG = os.path.join(TMP, 'config.toml')
with open(CFG, 'w') as fh:
    fh.write(f'[logs]\ndir = "{LOGS}"\n[load]\nmax_agents = 5\n[clean]\nprocess_regex = "no-such-process-at-all"\n')
ENV = dict(os.environ, AGENT_THROTTLE_CONFIG=CFG, XDG_STATE_HOME=os.path.join(TMP, 'state'))

fails = n = 0


def ok(name, cond):
    global fails, n
    n += 1
    print(f'ok   {name}' if cond else f'FAILED {name}')
    fails += 0 if cond else 1


def run(*args, stdin=None, cwd=None):
    return subprocess.run([os.path.join(BIN, args[0]), *args[1:]], capture_output=True, text=True, env=ENV,
                          stdin=stdin, cwd=cwd)


# ---------------------------------------------------------------- throttle-report
# Week 2026-W10 = Monday 2026-03-02 to Sunday 2026-03-08.
t0 = dt.datetime(2026, 3, 3, 9, 0)
load = ['date\tfree_pct\tpressure\tswap_mb\tagent_sessions\ttest_procs\tsemaphore_busy']
sess = [2, 3, 4, 6, 6, 3, 3, 2]  # 6 -> 3 with red pressure = one dead-session event (3 processes)
press = ['green', 'green', 'yellow', 'red', 'red', 'yellow', 'green', 'green']
for i, (s, p) in enumerate(zip(sess, press, strict=True)):
    t = t0 + dt.timedelta(minutes=5 * i)
    load.append(f'{t:%Y-%m-%d %H:%M:%S}\t{40 - 3 * i}\t{p}\t{1000 + 400 * i}\t{s}\t1\t{"yes" if i == 3 else "no"}')
with open(os.path.join(LOGS, 'load.log'), 'w') as fh:
    fh.write('\n'.join(load) + '\n')
sem = ['date\twait_s\tduration_s\trc\tcwd\tcommand\tnote',
       '2026-03-03 09:10:00\t0\t30\t0\t/Users/you/code/app\tnpx jest --ci --maxWorkers=4\t',
       '2026-03-03 14:10:00\t10\t45\t0\t/Users/you/code/app\tnpx jest --ci --maxWorkers=4\t',
       '2026-03-04 10:00:00\t200\t1200\t124\t/Users/you/code/app/.worktrees/x\tpnpm run validate:local\ttimeout',
       '2026-03-04 11:00:00\t\t300\t0\t/Users/you/code/app\tci PR #1 abcdef12\tci',
       '2026-02-20 10:00:00\t999\t10\t0\t/Users/you/code/app\tnpx jest --ci --maxWorkers=4\t']  # another week
with open(os.path.join(LOGS, 'semaphore.log'), 'w') as fh:
    fh.write('\n'.join(sem) + '\n')
guard = ['date\tcwd\trule\tcommand']
guard += [f'2026-02-2{i} 10:00:00\t/Users/you/code/app\tjest-no-workers\tnpx jest' for i in range(3, 8)]  # previous week: 5
guard += ['2026-03-03 10:00:00\t/Users/you/code/app\twatch-blocked\tnpx vitest',
          '2026-03-05 10:00:00\t/Users/you/code/app\tjest-no-workers\tnpx jest']
with open(os.path.join(LOGS, 'guard-blocks.log'), 'w') as fh:
    fh.write('\n'.join(guard) + '\n')

r = run('throttle-report', '--week', '2026-10', '--now', '2026-03-09 08:00:00', '--stdout', '--no-gh')
out = r.stdout
ok('report runs', r.returncode == 0 and out.startswith('# Machine metrics, week 2026-10 (2026-03-02 to 2026-03-08)'))
ok('one dead-session event with 3 processes', '| Dead sessions | 0 | 3 (1 event(s)) | missed |' in out)
ok('p95 wait over target is missed (200 s)', '| p95 of the wait for the solo slot | < 3.0 min | 3.3 min (n=3) | missed |' in out)
ok('lowest free memory', '| Lowest free memory | >= 20% | 19% |' in out)
ok('red with high load counted', '| Red pressure with >= 4 agent processes | 0 min | 10 min | missed |' in out)
ok('guard blocks falling vs previous week', '| Guard blocks | falling | 2 (previous week: 5) | ok |' in out)
ok('timeout listed with repo and tool', '~/code/app · validate:local, 20.0 min' in out.replace('/Users/you', '~')
   or 'validate:local, 20.0 min' in out)
ok('solo-ci runs counted apart', '- **solo-ci:** 1 run(s), 0 not green.' in out)
ok('duration table: morning and afternoon medians', '| 30 s (n=1) | 45 s (n=1) | +50% |' in out)
ok('run from another week left out', '999' not in out)
r = run('throttle-report', '--week', '2026-10', '--now', '2026-03-09 08:00:00', '--no-gh', '--out', os.path.join(TMP, 'rep'))
path = os.path.join(TMP, 'rep', 'week-2026-10.md')
ok('report written to week-YYYY-WW.md', r.returncode == 0 and os.path.isfile(path))
r = run('throttle-report', '--week', '2026-10', '--now', '2026-03-09 08:00:00', '--no-gh', '--out', os.path.join(TMP, 'rep'))
ok('same content: unchanged', r.stdout.strip().endswith('unchanged'))
r = run('throttle-report', '--week', '2026-20', '--now', '2026-05-20 08:00:00', '--stdout', '--no-gh')
ok('empty week: no data, no crash', r.returncode == 0 and 'No samples this week.' in r.stdout)
r = run('throttle-report', '--last-week', '--now', '2026-03-09 08:00:00', '--stdout', '--no-gh')
ok('--last-week on a Monday = the week that just ended', 'week 2026-10 ' in r.stdout)

# ---------------------------------------------------------------- throttle-logrotate
rot = os.path.join(TMP, 'rot')
os.makedirs(rot)
lines = ['date\twait_s\n', '2026-01-01 10:00:00\told\n', 'garbage without date\n', '2026-03-01 10:00:00\tnew\n']
with open(os.path.join(rot, 'semaphore.log'), 'w') as fh:
    fh.writelines(lines)
r = run('throttle-logrotate', '--dir', rot, '--days', '30', '--today', '2026-03-10', '--dry-run')
with open(os.path.join(rot, 'semaphore.log')) as fh:
    ok('dry run changes nothing', fh.readlines() == lines and 'would remove 1 of 4' in r.stdout)
r = run('throttle-logrotate', '--dir', rot, '--days', '30', '--today', '2026-03-10')
with open(os.path.join(rot, 'semaphore.log')) as fh:
    ok('old line removed; header, unknown and recent lines kept', fh.readlines() == [lines[0], lines[2], lines[3]])
ok('missing logs reported, no crash', r.returncode == 0 and 'load.log: does not exist' in r.stdout)

# ---------------------------------------------------------------- throttle-load
if platform.system() == 'Darwin':
    r = run('throttle-load', '--log')
    with open(os.path.join(LOGS, 'load.log')) as fh:
        last = fh.read().splitlines()[-1].split('\t')
    ok('load --log appends a 7-column line', r.returncode == 0 and len(last) == 7 and last[1].isdigit()
       and last[2] in ('green', 'yellow', 'red', 'unknown') and last[6] in ('yes', 'no'))
    r = run('throttle-load')
    ok('load prints a verdict', r.returncode == 0 and '== VERDICT: ' in r.stdout and 'up to 5' in r.stdout)
else:
    r = run('throttle-load')
    ok('load on Linux: clear message, exit 3', r.returncode == 3 and 'macOS only' in r.stderr)

# ---------------------------------------------------------------- throttle-clean
repo = os.path.join(TMP, 'repo')
os.makedirs(os.path.join(repo, '.worktrees', 'wt', '.next', 'cache'))
os.makedirs(os.path.join(repo, '.worktrees', 'wt', 'src'))
os.makedirs(os.path.join(repo, '.next'))  # the main checkout is not touched
subprocess.run(['git', 'init', '-q', repo], check=True)
with open(os.devnull) as devnull:
    r = run('throttle-clean', stdin=devnull, cwd=os.path.join(repo, '.worktrees'))
ok('clean removes artifacts inside worktree folders only', r.returncode == 0
   and not os.path.exists(os.path.join(repo, '.worktrees', 'wt', '.next'))
   and os.path.isdir(os.path.join(repo, '.worktrees', 'wt', 'src')) and os.path.isdir(os.path.join(repo, '.next')))
ok('clean without matching processes says none', 'none' in r.stdout)
r = run('throttle-clean', cwd=TMP)
ok('clean outside a repository: exit 1', r.returncode == 1)

print(f'{n - fails}/{n} cases')
sys.exit(1 if fails else 0)
