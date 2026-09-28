"""throttle-report: weekly Markdown report of the machine metrics.

Reads load.log (throttle-load --log, every 5 min through launchd), semaphore.log (solo and solo-ci) and
guard-blocks.log, optionally counts the week's pull requests on GitHub (gh, repositories under [report] repos_dir),
and writes <output_dir>/week-YYYY-WW.md (ISO week). Without --week: the current ISO week (partial, up to now).
--last-week: the week that just ended (what the launchd job runs on Monday morning).
"""
import argparse
import datetime as dt
import json
import os
import re
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config

DROP_MIN = 2                          # agent processes fewer between two consecutive samples = sudden drop
GAP_MAX = dt.timedelta(minutes=15)    # samples further apart are not "consecutive" (machine asleep, job stopped)
SAMPLE = dt.timedelta(minutes=5)
TOOLS = ('validate:local', 'typecheck', 'vitest', 'jest', 'pytest', 'tsc', 'lint', 'playwright')


# ---------- reading the logs ----------

def ts(text):
    return dt.datetime.strptime(text.strip()[:19], '%Y-%m-%d %H:%M:%S')


def read_tsv(path, ncols):
    rows = []
    try:
        lines = Path(path).read_text(encoding='utf-8', errors='replace').splitlines()
    except FileNotFoundError:
        return rows
    for line in lines:
        if not line or line.startswith('date\t'):
            continue
        parts = line.split('\t')
        if len(parts) < ncols:
            continue
        try:
            rows.append([ts(parts[0])] + parts[1:ncols - 1] + ['\t'.join(parts[ncols - 1:])])
        except ValueError:
            continue
    return rows


def read_load(path):
    out = []
    for d, free, pressure, swap, sess, tests, busy in read_tsv(path, 7):
        try:
            out.append(dict(t=d, free=int(free), pressure=pressure, swap=int(float(swap)), sess=int(sess),
                            tests=int(tests), busy=busy.strip() == 'yes'))
        except ValueError:
            continue
    return out


def read_semaphore(path):
    out = []
    for d, wait, dur, rc, cwd, cmd, note in read_tsv(path, 7):
        try:
            out.append(dict(t=d, wait=int(wait) if wait.strip().isdigit() else None, dur=int(dur), rc=int(rc),
                            cwd=cwd, cmd=cmd, note=note.strip()))
        except ValueError:
            continue
    return out


def read_guard(path):
    return [dict(t=d, cwd=cwd, rule=rule, cmd=cmd) for d, cwd, rule, cmd in read_tsv(path, 4)]


# ---------- classification ----------

def known_repos(repos_dir):
    names = set()
    if not repos_dir:
        return names
    root = Path(repos_dir)
    for d in list(root.glob('*')) + list(root.glob('*/*')):
        if (d / '.git').exists():
            names.add(d.name)
            if d.parent != root:
                names.add(d.parent.name)
    return names


def repo_suite(cwd, cmd, repos):
    """(repository, "subfolder · tool", workers) of one semaphore run."""
    path = re.sub(r'/(\.claude/worktrees|\.worktrees)/[^/]+', '', cwd)
    parts = [p for p in path.split('/') if p]
    repo, sub = None, ''
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] in repos:
            repo = parts[i]
            rest = [p for p in parts[i + 1:] if not p.startswith('.')]
            sub = rest[0] if rest else ''
            break
    if repo is None:  # no repos_dir: the folder itself, with the home folder shortened
        home = os.path.expanduser('~')
        repo = '~' + path[len(home):] if path.startswith(home) else (path or '?')
    m = re.search(r'(?:^|&&|;)\s*cd\s+([\w.-]+)', cmd)  # `cd web && ...` inside the command also names the package
    if not sub and m and not m.group(1).startswith('.'):
        sub = m.group(1)
    tool = next((t for t in TOOLS if t in cmd), cmd.split()[0].rsplit('/', 1)[-1] if cmd.split() else '?')
    w = re.search(r'--maxWorkers[= ](\d+)|(?:^|\s)-n\s+(\d+)|--workers[= ](\d+)', cmd)
    workers = next((g for g in (w.groups() if w else ()) if g), '')
    return repo, (f'{sub} · {tool}' if sub else tool), workers


# ---------- numbers ----------

def median(xs):
    return statistics.median(xs) if xs else None


def pct(xs, p):
    """Nearest-rank percentile (p95 of a few points = the largest of them, no interpolation)."""
    if not xs:
        return None
    s = sorted(xs)
    k = max(0, min(len(s) - 1, -(-p * len(s) // 100) - 1))
    return s[int(k)]


def when(t):
    return t.strftime('%a %Y-%m-%d %H:%M')


def fmt_s(x):
    if x is None:
        return 'no data'
    x = float(x)
    return f'{x:.0f} s' if x < 120 else f'{x / 60:.1f} min'


def fmt_n(x, digits=1):
    return 'no data' if x is None else f'{x:.{digits}f}'


def week_bounds(label):
    y, w = (int(p) for p in re.split(r'-W?', label.upper()))
    start = dt.datetime.combine(dt.date.fromisocalendar(y, w, 1), dt.time())
    return start, start + dt.timedelta(days=7), f'{y}-{w:02d}'


def dead_sessions(samples, swap_high):
    events = []
    for a, b in zip(samples, samples[1:], strict=False):
        if b['t'] - a['t'] > GAP_MAX:
            continue
        drop = a['sess'] - b['sess']
        if drop >= DROP_MIN and (max(a['swap'], b['swap']) >= swap_high or 'red' in (a['pressure'], b['pressure'])):
            events.append((a, b, drop))
    return events


def weight(samples, i):
    """Minutes that sample i stands for: until the next one if consecutive, else one sampling interval."""
    nxt = samples[i + 1]['t'] - samples[i]['t'] if i + 1 < len(samples) else SAMPLE
    return (nxt if nxt <= GAP_MAX else SAMPLE).total_seconds() / 60


def minutes(samples, cond):
    return sum(weight(samples, i) for i, s in enumerate(samples) if cond(s))


# ---------- GitHub ----------

def gh_repos(repos_dir):
    seen = {}
    root = Path(repos_dir)
    for d in sorted(list(root.glob('*')) + list(root.glob('*/*'))):
        if not (d / '.git').exists():
            continue
        try:
            url = subprocess.run(['git', '-C', str(d), 'remote', 'get-url', 'origin'], capture_output=True,
                                 text=True, timeout=10).stdout.strip()
        except Exception:
            continue
        m = re.search(r'github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$', url)
        if m:
            seen.setdefault(m.group(1), d.relative_to(root).as_posix())
    return seen


def gh_prs(slug, start, end):
    q = f'updated:>={start:%Y-%m-%d}'
    try:
        r = subprocess.run(['gh', 'pr', 'list', '--repo', slug, '--state', 'all', '--search', q, '--limit', '200',
                            '--json', 'number,createdAt,mergedAt'], capture_output=True, text=True, timeout=60)
    except Exception as exc:
        return None, None, f'error: {exc.__class__.__name__}'
    if r.returncode != 0:
        return None, None, 'error: ' + (r.stderr.strip().splitlines() or ['?'])[-1][:80]

    def inside(v):
        if not v:
            return False
        t = dt.datetime.fromisoformat(v.replace('Z', '+00:00')).astimezone().replace(tzinfo=None)
        return start <= t < end
    prs = json.loads(r.stdout or '[]')
    return sum(inside(p['createdAt']) for p in prs), sum(inside(p['mergedAt']) for p in prs), ''


# ---------- report ----------

def status(ok):
    return 'no data' if ok is None else ('ok' if ok else 'missed')


def build(a, cfg):
    rc = cfg['report']
    max_agents, high, swap_high = cfg['load']['max_agents'], rc['high_sessions'], cfg['load']['max_swap_mb']
    start, end, label = week_bounds(a.week)
    prev_start = start - dt.timedelta(days=7)
    repos = known_repos(a.repos_dir)
    ld = [s for s in read_load(a.load_log) if start <= s['t'] < end]
    se = [v for v in read_semaphore(a.semaphore_log) if start <= v['t'] < end]
    gd_all = read_guard(a.guard_log)
    gd = [g for g in gd_all if start <= g['t'] < end]
    gd_prev = [g for g in gd_all if prev_start <= g['t'] < start]

    deaths = dead_sessions(ld, swap_high)
    n_dead = sum(d for _, _, d in deaths)
    red = minutes(ld, lambda s: s['pressure'] == 'red')
    yellow = minutes(ld, lambda s: s['pressure'] == 'yellow')
    lowest = min(ld, key=lambda s: s['free']) if ld else None
    swap_max = max(ld, key=lambda s: s['swap']) if ld else None
    by_day = defaultdict(list)
    for s in ld:
        by_day[s['t'].date()].append(s['swap'])
    day_rise = max(((d, max(v) - v[0]) for d, v in by_day.items()), key=lambda x: x[1], default=None)
    over_swap = sum(1 for s in ld if s['swap'] >= swap_high)
    total_min = minutes(ld, lambda s: True)
    high_min = minutes(ld, lambda s: s['sess'] >= high)
    high_pct = 100 * high_min / total_min if total_min else None
    red_high = minutes(ld, lambda s: s['sess'] >= high and s['pressure'] == 'red')
    peak = max(ld, key=lambda s: (s['sess'], -s['t'].timestamp())) if ld else None
    by_level = defaultdict(lambda: [0.0, 0.0, 0.0])      # sessions -> [total min, red min, yellow min]
    for i, s in enumerate(ld):
        m = weight(ld, i)
        by_level[s['sess']][0] += m
        by_level[s['sess']][1] += m if s['pressure'] == 'red' else 0
        by_level[s['sess']][2] += m if s['pressure'] == 'yellow' else 0
    expected = int((min(end, a.now) - start).total_seconds() // SAMPLE.total_seconds()) if a.now > start else 0

    runs = [v for v in se if v['note'] != 'ci']
    ci_runs = [v for v in se if v['note'] == 'ci']
    waits = [v['wait'] for v in runs if v['wait'] is not None]
    p50, p95 = median(waits), pct(waits, 95)
    groups = defaultdict(lambda: {'morning': [], 'afternoon': []})
    for v in runs:
        if v['rc'] != 0:
            continue
        repo, suite, _ = repo_suite(v['cwd'], v['cmd'], repos)
        groups[(repo, suite)]['morning' if v['t'].hour < 12 else 'afternoon'].append(v['dur'])
    failed = sum(1 for v in runs if v['rc'] != 0)
    timeouts = [v for v in runs if v['note'] == 'timeout']

    rules = Counter(g['rule'] for g in gd)
    rules_prev = Counter(g['rule'] for g in gd_prev)
    falling = None if not gd_prev else len(gd) < len(gd_prev)

    L = []
    w = L.append
    w(f'# Machine metrics, week {label} ({start:%Y-%m-%d} to {(end - dt.timedelta(days=1)):%Y-%m-%d})')
    w('')
    w(f'Generated by `throttle-report` on {a.now:%Y-%m-%d %H:%M}. Sources: load.log ({len(ld)} samples of {expected} '
      f'possible every 5 min; a sleeping machine takes no samples), semaphore.log ({len(runs)} runs through solo, '
      f'{len(ci_runs)} through solo-ci), guard-blocks.log ({len(gd)} blocks).')
    w('')
    w('## Targets')
    w('')
    w('| Target | Goal | This week | Status |')
    w('|---|---|---|---|')
    w(f'| Dead sessions | 0 | {n_dead} ({len(deaths)} event(s)) | {status(n_dead == 0 if ld else None)} |')
    w(f'| p95 of the wait for the solo slot | < {fmt_s(rc["wait_p95_target_s"])} | {fmt_s(p95)} (n={len(waits)}) | '
      f'{status(p95 < rc["wait_p95_target_s"] if p95 is not None else None)} |')
    w(f'| Lowest free memory | >= {rc["min_free_target_pct"]}% | {str(lowest["free"]) + "%" if lowest else "no data"} | '
      f'{status(lowest["free"] >= rc["min_free_target_pct"] if lowest else None)} |')
    w(f'| Red pressure with >= {high} agent processes | 0 min | '
      f'{fmt_n(red_high, 0) + " min" if ld else "no data"} | {status(red_high == 0 if ld else None)} |')
    w(f'| Guard blocks | falling | {len(gd)} (previous week: {len(gd_prev) if gd_prev else "no data"}) | {status(falling)} |')
    w('')
    w('## Memory and agent processes (`throttle-load --log`)')
    w('')
    if not ld:
        w('No samples this week.')
    else:
        w(f'- **Dead sessions:** {n_dead}. Rule: {DROP_MIN} or more agent processes fewer between two consecutive samples '
          f'(up to {int(GAP_MAX.total_seconds() // 60)} min apart) with swap >= {swap_high} MB or red pressure in one of '
          'them. Closing sessions by hand while swap is high also counts: check the events.')
        for x, y, _drop in deaths:
            w(f'  - {when(x["t"])} -> {y["t"]:%H:%M}: {x["sess"]} -> {y["sess"]} processes, swap '
              f'{fmt_n(max(x["swap"], y["swap"]) / 1024)} GB, pressure {x["pressure"]} -> {y["pressure"]}')
        w(f'- **Pressure:** {fmt_n(red, 0)} min red, {fmt_n(yellow, 0)} min yellow.')
        w(f'- **Lowest free memory:** {lowest["free"]}% on {when(lowest["t"])} ({lowest["sess"]} agent processes, '
          f'{lowest["tests"]} test processes, solo {"busy" if lowest["busy"] else "idle"}).')
        w(f'- **Swap:** {fmt_n(ld[0]["swap"] / 1024)} GB in the first sample, {fmt_n(ld[-1]["swap"] / 1024)} GB in the '
          f'last (peak {fmt_n(swap_max["swap"] / 1024)} GB on {when(swap_max["t"])}); largest rise in a day: '
          f'+{fmt_n(day_rise[1] / 1024)} GB on {day_rise[0]:%Y-%m-%d}; {over_swap} sample(s) with swap >= {swap_high} MB.')
        w(f'- **Agent processes:** at most {peak["sess"]} at once (first on {when(peak["t"])}), median '
          f'{fmt_n(median([s["sess"] for s in ld]), 0)}; {fmt_n(high_pct, 0)}% of the sampled time with >= {high} '
          f'({fmt_n(high_min, 0)} of {fmt_n(total_min, 0)} min). Idle sessions count too; the configured ceiling for '
          f'agents coding at once is {max_agents}.')
        w(f'- **Pressure with >= {high} agent processes:** {fmt_n(red_high, 0)} of the {fmt_n(red, 0)} min in red. This '
          'is the number that confirms (or refutes) the max_agents setting.')
        w('')
        w('| Agent processes | Time | % of time | Min red | Min yellow |')
        w('|---|---|---|---|---|')
        for n in sorted(by_level):
            t_, r_, y_ = by_level[n]
            w(f'| {n} | {fmt_n(t_ / 60)} h | {fmt_n(100 * t_ / total_min, 0)}% | {fmt_n(r_, 0)} | {fmt_n(y_, 0)} |')
    w('')
    w('## solo')
    w('')
    w(f'- **Wait for the slot:** median {fmt_s(p50)}, p95 {fmt_s(p95)}, max {fmt_s(max(waits) if waits else None)} '
      f'(n={len(waits)}).')
    w(f'- **Runs:** {len(runs)}, {failed} with rc other than 0 (left out of the durations below).')
    w(f'- **Timeouts** (semaphore.timeout_s; process tree killed, rc=124): {len(timeouts)}.')
    for v in timeouts:
        repo, suite, _ = repo_suite(v['cwd'], v['cmd'], repos)
        w(f'  - {when(v["t"])}: {repo} · {suite}, {fmt_s(v["dur"])}')
    w(f'- **solo-ci:** {len(ci_runs)} run(s), {sum(1 for v in ci_runs if v["rc"] != 0)} not green.')
    w('')
    if groups:
        w('Duration by repository and suite (median, rc=0 only; morning = before noon). Thermal throttling: afternoons '
          'after a long battery of runs tend to be slower on laptops without a fan.')
        w('')
        w('| Repository | Suite | Morning | Afternoon | Afternoon/morning |')
        w('|---|---|---|---|---|')
        for (repo, suite), g in sorted(groups.items()):
            m, t = median(g['morning']), median(g['afternoon'])
            rel = f'{(t / m - 1) * 100:+.0f}%' if m and t else ''
            w(f'| {repo} | {suite} | {fmt_s(m)} (n={len(g["morning"])}) | {fmt_s(t)} (n={len(g["afternoon"])}) | {rel} |')
        w('')
    w('## Guard blocks')
    w('')
    if not rules and not rules_prev:
        w('No blocks this week nor the previous one.')
    else:
        w('| Rule | This week | Previous week |')
        w('|---|---|---|')
        for rule in sorted(set(rules) | set(rules_prev), key=lambda r: (-rules[r], r)):
            w(f'| `{rule}` | {rules[rule]} | {rules_prev[rule] if gd_prev else "no data"} |')
        w(f'| **total** | **{len(gd)}** | **{len(gd_prev) if gd_prev else "no data"}** |')
    w('')
    if a.repos_dir and not a.no_gh:
        w(f'## Pull requests per repository ({a.repos_dir}, GitHub)')
        w('')
        slugs = gh_repos(a.repos_dir)
        with ThreadPoolExecutor(max_workers=6) as ex:
            res = dict(zip(slugs, ex.map(lambda s: gh_prs(s, start, end), slugs), strict=True))
        w('| Repository | Folder | Opened this week | Merged this week |')
        w('|---|---|---|---|')
        tot_o = tot_m = 0
        for slug in sorted(slugs, key=lambda s: (-(res[s][1] or 0), -(res[s][0] or 0), s)):
            opened, merged, err = res[slug]
            if err:
                w(f'| {slug} | {slugs[slug]} | {err} | |')
                continue
            if not opened and not merged:
                continue
            tot_o, tot_m = tot_o + opened, tot_m + merged
            w(f'| {slug} | {slugs[slug]} | {opened} | {merged} |')
        w(f'| **total** | | **{tot_o}** | **{tot_m}** |')
        w('')
    return '\n'.join(L), label


def main(argv=None):
    cfg = config.load()
    ap = argparse.ArgumentParser(prog='throttle-report', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--week', help='ISO week, YYYY-WW')
    ap.add_argument('--last-week', action='store_true')
    ap.add_argument('--out', type=Path, default=Path(cfg['report']['output_dir']))
    ap.add_argument('--stdout', action='store_true', help='print the report instead of writing the file')
    ap.add_argument('--no-gh', action='store_true', help='skip the pull request count')
    ap.add_argument('--repos-dir', default=cfg['report']['repos_dir'])
    ap.add_argument('--load-log', default=config.log_path(cfg, 'load'))
    ap.add_argument('--semaphore-log', default=config.log_path(cfg, 'semaphore'))
    ap.add_argument('--guard-log', default=config.log_path(cfg, 'guard'))
    ap.add_argument('--now', help='YYYY-MM-DD HH:MM:SS (tests)')
    a = ap.parse_args(argv)
    a.now = ts(a.now) if a.now else dt.datetime.now().replace(microsecond=0)
    if not a.week:
        y, wk, _ = (a.now - dt.timedelta(days=7 if a.last_week else 0)).isocalendar()
        a.week = f'{y}-{wk:02d}'
    text, label = build(a, cfg)
    if a.stdout:
        print(text)
        return 0
    a.out.mkdir(parents=True, exist_ok=True)
    out = a.out / f'week-{label}.md'
    if out.exists() and out.read_text(encoding='utf-8') == text:
        print(f'{out}: unchanged')
        return 0
    out.write_text(text, encoding='utf-8')
    print(out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
