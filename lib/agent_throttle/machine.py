"""Live, portable hardware readers. Unknown values stay unknown; callers choose conservative fallbacks."""
import math
import platform
import re
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


def swap_mb():
    if platform.system() == 'Darwin':
        match = re.search(r'used = ([0-9.]+)M', command('sysctl', '-n', 'vm.swapusage') or '')
        try:
            value = float(match[1]) if match else None
            return value if value is not None and math.isfinite(value) and value >= 0 else None
        except ValueError:
            return None
    info = meminfo()
    if not info or not {'SwapTotal', 'SwapFree'} <= info.keys():
        return None
    used = info['SwapTotal'] - info['SwapFree']
    return used / 2**20 if 0 <= used <= info['SwapTotal'] else None


def pressure(yellow_pct=1, red_pct=10):
    if platform.system() == 'Darwin':
        return {'1': 'green', '2': 'yellow', '4': 'red'}.get(
            (command('sysctl', '-n', 'kern.memorystatus_vm_pressure_level') or '').strip())
    try:
        text = read_text('/proc/pressure/memory')
        values = {m[1]: float(m[2]) for m in re.finditer(r'^(some|full) avg10=([0-9.]+)', text, re.M)}
        if set(values) != {'some', 'full'} or any(not 0 <= v <= 100 for v in values.values()):
            return None
        if values['full'] >= red_pct:
            return 'red'
        return 'yellow' if values['some'] >= yellow_pct else 'green'
    except (OSError, ValueError):
        return None


def thermal():
    if platform.system() == 'Darwin':
        text = command('pmset', '-g', 'therm')
        if text is None or not text.strip():
            return None
        return [line.strip() for line in text.splitlines()
                if line.strip() and not re.match(r'Note: No .* recorded', line.strip())]
    warnings, signals = [], 0
    try:
        for zone in Path('/sys/class/thermal').glob('thermal_zone*'):
            temperature = int(read_text(zone / 'temp'))
            for kind in zone.glob('trip_point_*_type'):
                if read_text(kind).strip() not in ('hot', 'critical'):
                    continue
                limit = int(read_text(str(kind).replace('_type', '_temp')))
                if limit <= 0:
                    return None
                signals += 1
                if temperature >= limit:
                    warnings.append('thermal trip reached: ' + zone.name)
    except (OSError, ValueError):
        return None
    return warnings if signals else None


def processes():
    table = command('ps', '-axo', 'pid=,ppid=,rss=,comm=')
    arguments = command('ps', '-axo', 'pid=,args=')
    if not table or not arguments:
        return None
    args, out = {}, []
    try:
        for line in arguments.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2:
                args[int(parts[0])] = parts[1]
        for line in table.splitlines():
            pid, parent, rss, name = line.strip().split(None, 3)
            pid = int(pid)
            if pid not in args:
                return None
            out.append(dict(pid=pid, parent=int(parent), rss=int(rss), name=name.rsplit('/', 1)[-1], args=args[pid]))
    except (ValueError, IndexError):
        return None
    return out


if __name__ == '__main__':
    c, s = settings(*sys.argv[1:3])
    print(c, s)
