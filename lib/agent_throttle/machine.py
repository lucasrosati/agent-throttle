"""Live, portable hardware readers. Unknown values stay unknown; callers choose conservative fallbacks."""
import platform
import subprocess
import sys
from pathlib import Path


def command(*args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=3)
        return result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def read_text(path):
    return Path(path).read_text(encoding='utf-8')


def positive(value):
    try:
        n = int(value)
        return n if n > 0 else None
    except (ValueError, TypeError):
        return None


def cores():
    if platform.system() == 'Darwin':
        return positive(command('sysctl', '-n', 'hw.perflevel0.logicalcpu')) or positive(command('sysctl', '-n', 'hw.ncpu'))
    return positive(command('nproc'))


def meminfo():
    try:
        return {line.split(':')[0]: int(line.split()[1]) * 1024 for line in read_text('/proc/meminfo').splitlines()}
    except (OSError, ValueError, IndexError):
        return None


def ram_bytes():
    if platform.system() == 'Darwin':
        return positive(command('sysctl', '-n', 'hw.memsize'))
    info = meminfo()
    return positive(info.get('MemTotal')) if info else None


def memory():
    """(available percentage, available MiB), or None on any invalid/missing signal."""
    if platform.system() == 'Darwin':
        total = ram_bytes()
        try:
            pct = int(command('sysctl', '-n', 'kern.memorystatus_level'))
        except (ValueError, TypeError):
            return None
        return (pct, total / 2**20 * pct / 100) if total and 0 <= pct <= 100 else None
    info = meminfo()
    if not info or not positive(info.get('MemTotal')) or 'MemAvailable' not in info:
        return None
    total, available = info['MemTotal'], info['MemAvailable']
    return (100 * available / total, available / 2**20) if 0 <= available <= total else None


def slot_count(c, ram):
    if not c or not ram:
        return 1
    return max(1, min(c // max(1, c // 2), int(ram // 2**30 // 10)))


def settings(core_override='auto', slot_override='auto'):
    c = positive(core_override) if core_override != 'auto' else cores()
    s = positive(slot_override) if slot_override != 'auto' else slot_count(c, ram_bytes())
    return c or 1, s or 1


if __name__ == '__main__':
    c, s = settings(*sys.argv[1:3])
    print(c, s)
