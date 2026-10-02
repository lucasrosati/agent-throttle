#!/usr/bin/env python3
"""Portable hardware and slot formulas, using fake readers rather than the runner's hardware."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(os.environ.get('THROTTLE_ROOT', Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(ROOT / 'lib'))
from agent_throttle import machine  # noqa: E402

n = 0


def ok(name, cond):
    global n
    assert cond, name
    n += 1
    print('ok  ' + name)


ok('two slots on 15 cores and 24 GB', machine.slot_count(15, 24 * 2**30) == 2)
ok('one slot on 10 cores and 16 GB', machine.slot_count(10, 16 * 2**30) == 1)
ok('one core does not divide by zero', machine.slot_count(1, 24 * 2**30) == 1)
ok('missing hardware conservatively uses one slot', machine.slot_count(None, None) == 1)
with patch.object(machine.platform, 'system', return_value='Darwin'), patch.object(machine, 'command') as command:
    command.side_effect = ['9\n', '17179869184\n']
    ok('performance cores and live RAM read on macOS', machine.cores() == 9 and machine.ram_bytes() == 16 * 2**30)
    command.side_effect = [None, '12\n']
    ok('all logical CPUs fallback', machine.cores() == 12)
    command.side_effect = [None, None]
    ok('failed core read remains unknown', machine.cores() is None)
with patch.object(machine.platform, 'system', return_value='Linux'), patch.object(machine, 'command', return_value='6\n'), \
        patch.object(machine, 'read_text', return_value='MemTotal: 8388608 kB\nMemAvailable: 2097152 kB\n'):
    ok('Linux live cores and RAM', machine.cores() == 6 and machine.ram_bytes() == 8 * 2**30)
    pct, free = machine.memory()
    ok('Linux available memory is used', pct == 25 and free == 2048)
with patch.object(machine, 'read_text', side_effect=OSError), patch.object(machine.platform, 'system', return_value='Linux'):
    ok('failed memory read remains unknown', machine.memory() is None)
print(f'{n}/{n} cases')
