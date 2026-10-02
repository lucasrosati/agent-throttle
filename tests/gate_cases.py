#!/usr/bin/env python3
"""Gate boundaries and history selection, all readers simulated."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(os.environ.get('THROTTLE_ROOT', Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(ROOT / 'lib'))
from agent_throttle import gate  # noqa: E402

n = 0


def ok(name, cond):
    global n
    assert cond, name
    n += 1
    print('ok  ' + name)


with tempfile.TemporaryDirectory(prefix='gate-') as t:
    log = Path(t, 'log')
    with patch.object(gate.machine, 'ram_bytes', return_value=16 * 2**30), \
            patch.object(gate.machine, 'memory', return_value=(25, 4096)):
        ok('slot 2 admitted with no history uses 25 percent of RAM', gate.reason(log, 25, 'auto') == '')
        ok('percentage below gate blocks', 'free 25%' in gate.reason(log, 26, 'auto'))
        ok('override forces MB and blocks', '5000 MB' in gate.reason(log, 25, '5000'))
    with patch.object(gate.machine, 'ram_bytes', return_value=16 * 2**30), \
            patch.object(gate.machine, 'memory', return_value=(50, 3000)):
        ok('no history requires RAM quarter', '4096 MB' in gate.reason(log, 25, 'auto'))
        log.write_text('\n'.join('2026-01-01 00:00:00\t0\t0\t0\tcwd\tcmd\t\t1/2\t8\t\tmanual\t' + str(x) for x in range(100, 600, 100)))
        five_rows = log.read_text()
        log.write_text('\n'.join(five_rows.splitlines()[:4]))
        ok('fewer than five samples use RAM quarter', gate.required_mb(log, 16 * 2**30) == 4096)
        log.write_text(five_rows)
        ok('five samples provide peak p90', gate.required_mb(log, 16 * 2**30) == 500)
        ok('history permits smaller free MB', gate.reason(log, 25, 'auto') == '')
        log.write_text('\n'.join('2026-01-01 00:00:00\t0\t0\t0\tcwd\tcmd\t\t1/2\t8\t\tmanual\t' + str(x)
                                 for x in [999999] * 10 + [100] * 50))
        ok('only last 50 rows contribute', gate.required_mb(log, 16 * 2**30) == 100)
        log.write_text('old\t0\t0\t0\tcwd\tcmd\t\n' * 50)
        ok('old seven-column rows use no-history fallback', gate.required_mb(log, 16 * 2**30) == 4096)
    with patch.object(gate.machine, 'memory', return_value=None):
        ok('failed memory read closes the gate', 'read failed' in gate.reason(log, 25, '1'))
print(f'{n}/{n} cases')
