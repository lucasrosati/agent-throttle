"""Admission check for additional semaphore slots; empty reason means admitted."""
import math
import sys
from collections import deque
from pathlib import Path

if __package__:
    from . import machine
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from agent_throttle import machine


def required_mb(log, ram):
    samples = []
    try:
        with open(log, encoding='utf-8', errors='replace') as fh:
            rows = deque((line for line in fh if not line.startswith('date\t')), maxlen=50)
        for row in rows:
            fields = row.rstrip('\n').split('\t')
            if len(fields) >= 12 and fields[11].isdigit():
                samples.append(int(fields[11]))
    except FileNotFoundError:
        pass
    if len(samples) < 5:
        return ram / 2**20 / 4
    return sorted(samples)[math.ceil(0.9 * len(samples)) - 1]


def reason(log, pct, override):
    available = machine.memory()
    ram = machine.ram_bytes()
    if available is None or not ram:
        return 'memory: read failed'
    level, free = available
    try:
        need = required_mb(log, ram) if override == 'auto' else float(override)
    except (OSError, ValueError):
        return 'memory: history or threshold read failed'
    if level < pct:
        return f'memory: free {level:g}% < {pct:g}%'
    if free < need:
        return f'memory: free {free:g} MB < peak p90 {need:g} MB'
    return ''


if __name__ == '__main__':
    print(reason(sys.argv[1], int(sys.argv[2]), sys.argv[3]))
