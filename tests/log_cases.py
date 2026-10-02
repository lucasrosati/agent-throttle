#!/usr/bin/env python3
"""Old, extended and mixed logs preserve the original field meanings."""
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(os.environ.get('THROTTLE_ROOT', Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(ROOT / 'lib'))
from agent_throttle import report, logs  # noqa: E402

n = 0


def ok(name, cond):
    global n
    assert cond, name
    n += 1
    print('ok  ' + name)


with tempfile.TemporaryDirectory(prefix='logs-') as t:
    log = Path(t, 'semaphore.log')
    old = '2026-01-01 12:00:00\t0\t10\t124\t/home/you/app\tjest\ttimeout'
    log.write_text('date\twait_s\tduration_s\trc\tcwd\tcommand\tnote\n' + old + '\n')
    ok('0.1.0 seven-column semaphore log still parses in throttle-report', report.read_semaphore(log)[0]['note'] == 'timeout')
    with log.open('a') as fh:
        fh.write(old + '\t2/2\t7\t--maxWorkers 12->7\tcodex\t1000\tmemory: free below threshold\n')
        fh.write(old.replace('timeout', 'ci') + '\n')
    rows = report.read_semaphore(log)
    ok('extended row does not corrupt timeout note', rows[1]['note'] == 'timeout')
    ok('new rows carry slot, w and peak_mb', rows[1]['slot'] == '2/2' and rows[1]['w'] == 7 and rows[1]['peak_mb'] == 1000)
    ok('mixed old/new logs preserve solo-ci note', rows[2]['note'] == 'ci' and rows[0]['slot'] is None)
    load = Path(t, 'load.log')
    load.write_text('2026-01-01 12:00:00\t50\tgreen\t0\t3\t1\tyes\n'
                    '2026-01-01 12:05:00\t50\tgreen\t0\t3\t1\tyes\t2\t2/2\t15\n')
    rows = report.read_load(load)
    ok('old and new load rows preserve busy and total sessions', all(r['busy'] and r['sess'] == 3 for r in rows))
    ok('new load fields retained without doubling agents', rows[1]['codex_agents'] == 2 and rows[1]['slots'] == '2/2'
       and rows[1]['workers'] == 15)
    concurrent = Path(t, 'concurrent.log')
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: logs.append(concurrent, 'date\tvalue', ['2026-01-01 12:00:00', i]), range(40)))
    lines = concurrent.read_text().splitlines()
    ok('concurrent appends share one header and complete rows', len(lines) == 41
       and lines.count('date\tvalue') == 1 and all(len(line.split('\t')) == 2 for line in lines))
print(f'{n}/{n} cases')
