"""Portable launch verdict and TSV sampling for throttle-load."""
import argparse
import datetime as dt
import os
import re
import shlex
import sys
import math
from collections import defaultdict
from pathlib import Path

from . import config, machine

HEADER = 'date\tfree_pct\tpressure\tswap_mb\tagent_sessions\ttest_procs\tsemaphore_busy'


def snapshot(cfg):
    override = os.environ.get('SOLO_CORES', cfg['semaphore']['cores'])
    available = machine.memory()
    return dict(cores=machine.cores() if override == 'auto' else machine.positive(override), ram=machine.ram_bytes(),
                free_pct=available[0] if available is not None else None,
                pressure=machine.pressure(cfg['load']['psi_yellow_pct'], cfg['load']['psi_red_pct']),
                swap=machine.swap_mb(), thermal=machine.thermal(), processes=machine.processes())


def count_agents(procs, settings):
    names = set(settings['agent_processes'])
    excluded = set(settings['helper_processes'])
    parents = {p['pid']: p['name'] for p in procs}
    total = codex = 0
    for p in procs:
        try:
            tokens = shlex.split(p['args'])
        except ValueError:
            tokens = p['args'].split()
        if p['name'] not in names or excluded.intersection(tokens) or p['name'] in excluded:
            continue
        if parents.get(p['parent']) == p['name']:
            continue
        total += 1
        codex += p['name'] == 'codex'
    return total, codex


def window_delta(log, current, minutes, now):
    cutoff = now - dt.timedelta(minutes=minutes)
    try:
        with open(log, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                parts = line.rstrip('\n').split('\t')
                if len(parts) < 4:
                    continue
                try:
                    when = dt.datetime.strptime(parts[0], '%Y-%m-%d %H:%M:%S')
                    previous = float(parts[3])
                except ValueError:
                    continue
                if cutoff <= when <= now and math.isfinite(previous) and previous >= 0:
                    return current - previous
    except OSError:
        pass
    return None


def verdict(signals, cfg, delta):
    settings = cfg['load']
    reasons, warnings = [], []
    missing = [name for name, value in signals.items() if value is None]
    if missing:
        reasons.append('read failed: ' + ', '.join(sorted(missing)))
    procs = signals['processes']
    if procs is not None:
        total, _ = count_agents(procs, settings)
        if total >= settings['max_agents']:
            reasons.append(f'{total} agents >= {settings["max_agents"]}')
    free, pressure, swap = signals['free_pct'], signals['pressure'], signals['swap']
    if free is not None and free < settings['min_free_pct']:
        reasons.append(f'free {free:g}% < {settings["min_free_pct"]}%')
    if pressure == 'red':
        reasons.append('red pressure')
    if delta is None:
        reasons.append(f'no valid swap sample in the last {settings["swap_window_min"]} minutes; run throttle-load --log')
    elif delta > settings['swap_delta_mb']:
        reasons.append(f'swap +{delta:g} MB > {settings["swap_delta_mb"]} MB in {settings["swap_window_min"]} minutes')
    if pressure == 'yellow' and delta is not None and delta > 0:
        reasons.append('yellow pressure with rising swap')
    if signals['thermal']:
        reasons.append('thermal warning: ' + '; '.join(signals['thermal']))
    if swap is not None and cfg.get('_max_swap_explicit') and swap > settings['max_swap_mb']:
        reasons.append(f'swap {swap:g} MB > explicitly configured max_swap_mb {settings["max_swap_mb"]}')
    ram = signals['ram']
    if swap is not None and ram and swap > ram / 2**20 / settings['swap_warn_ram_divisor']:
        warnings.append(f'total swap {swap:g} MB above RAM/{settings["swap_warn_ram_divisor"]} (warning only)')
    return reasons, warnings


def slot_usage(cfg, signals):
    base = Path(cfg['semaphore']['lock_dir'])
    slots = [p for p in [base, *base.parent.glob(base.name + '.*')] if p.is_dir() and
             (p == base or p.name[len(base.name) + 1:].isdigit())]
    workers = 0
    for slot in slots:
        try:
            workers += max(0, int((slot / 'w').read_text().strip()))
        except (OSError, ValueError):
            pass  # legacy locks do not have w
    override = os.environ.get('SOLO_SLOTS', cfg['semaphore']['slots'])
    total = machine.slot_count(signals['cores'], signals['ram']) if override == 'auto' else machine.positive(override)
    return len(slots), total or 1, workers


def write_sample(signals, cfg, now):
    path = Path(config.log_path(cfg, 'load'))
    path.parent.mkdir(parents=True, exist_ok=True)
    total, _ = count_agents(signals['processes'], cfg['load']) if signals['processes'] is not None else (None, None)
    tests = sum(bool(re.search(cfg['load']['test_process_regex'], p['args']))
                for p in signals['processes'] if p['name'].lstrip('-') not in ('awk', 'grep', 'bash', 'sh', 'zsh')) \
        if signals['processes'] is not None else None
    busy, _, _ = slot_usage(cfg, signals)
    row = [now.strftime('%Y-%m-%d %H:%M:%S'), int(signals['free_pct']) if signals['free_pct'] is not None else None,
           signals['pressure'] or 'unknown', int(signals['swap']) if signals['swap'] is not None else None,
           total, tests, 'yes' if busy else 'no']
    with open(path, 'a', encoding='utf-8') as fh:
        if fh.tell() == 0:
            fh.write(HEADER + '\n')
        fh.write('\t'.join('' if v is None else str(v) for v in row) + '\n')


def main(argv=None):
    ap = argparse.ArgumentParser(prog='throttle-load', description=__doc__)
    ap.add_argument('--log', action='store_true', help='append a sample without printing a verdict')
    args = ap.parse_args(argv)
    cfg = config.load()
    signals = snapshot(cfg)
    now = dt.datetime.now()
    if args.log:
        write_sample(signals, cfg, now)
        return 0
    delta = window_delta(config.log_path(cfg, 'load'), signals['swap'], cfg['load']['swap_window_min'], now) \
        if signals['swap'] is not None else None
    reasons, warnings = verdict(signals, cfg, delta)
    ram = signals['ram']
    print(f'== Machine: {signals["cores"] or "?"} cores, {ram / 2**30 if ram else "?"} GB (read live)')
    print(f'== Free memory: {signals["free_pct"] if signals["free_pct"] is not None else "?"}%'
          f'  pressure: {signals["pressure"] or "unknown"}')
    print(f'== Swap: {signals["swap"] if signals["swap"] is not None else "?"} MB  delta: {delta if delta is not None else "?"} MB')
    procs = signals['processes']
    agents, codex = count_agents(procs, cfg['load']) if procs is not None else ('?', '?')
    print(f'== Agents: {agents} total ({codex} codex), up to {cfg["load"]["max_agents"]}')
    busy, slots, workers = slot_usage(cfg, signals)
    print(f'== solo: {busy}/{slots} slots, {workers} workers in use')
    apps, suites = defaultdict(int), defaultdict(int)
    for p in procs or []:
        apps[p['name']] += p['rss']
        if re.search(cfg['load']['test_process_regex'] + '|next', p['args']):
            match = re.search(r'worktrees/([^/ ]+)', p['args'])
            suites[match[1] if match else 'main'] += p['rss']
    print('== Top apps (GB):')
    for name, rss in sorted(apps.items(), key=lambda item: -item[1])[:10]:
        print(f'{rss / 2**20:6.2f}  {name}')
    print('== Test processes by worktree (GB):')
    for name, rss in sorted(suites.items(), key=lambda item: -item[1]):
        print(f'{rss / 2**20:6.2f}  {name}')
    for warning in warnings:
        print('== WARNING: ' + warning)
    print('== VERDICT: DO NOT launch a new agent (' + '; '.join(reasons) + ')' if reasons else
          f'== VERDICT: ok to launch (up to {cfg["load"]["max_agents"]} agents; heavy validation through solo)')
    return 0


if __name__ == "__main__":
    sys.exit(main())
