#!/usr/bin/env python3
"""Owner identity and legacy locks. Processes and locks belong only to this test."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(os.environ.get('THROTTLE_ROOT', Path(__file__).resolve().parent.parent))
n = 0


def ok(name, cond):
    global n
    assert cond, name
    n += 1
    print('ok  ' + name, flush=True)


with tempfile.TemporaryDirectory(prefix='identity-') as t:
    tmp = Path(t)
    lock = tmp / 'lock'
    cfg = tmp / 'config.toml'
    cfg.write_text(f'[semaphore]\nslots=1\nlock_dir="{lock}"\npoll_s=1\n[logs]\ndir="{tmp}/logs"\n')
    env = dict(os.environ, AGENT_THROTTLE_CONFIG=str(cfg), AT_PYTHON=sys.executable)
    sleeper = subprocess.Popen(['sleep', '30'])
    try:
        lock.mkdir()
        (lock / 'pid').write_text(str(sleeper.pid))
        (lock / 'pid_start').write_text('Mon Jan  1 00:00:00 2001\n')
        (lock / 'child').write_text(str(sleeper.pid))
        (lock / 'child_start').write_text('different start\n')
        r = subprocess.run([str(ROOT / 'bin/solo'), 'true'], env=env, text=True, capture_output=True, timeout=5)
        ok('reused PID with a different start time is a dead owner and is not killed', r.returncode == 0
           and not lock.exists() and sleeper.poll() is None)
        lock.mkdir()
        (lock / 'pid').write_text(str(sleeper.pid))
        p = subprocess.Popen([str(ROOT / 'bin/solo'), 'true'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            time.sleep(0.8)
            ok('lock without start-time file falls back to kill -0', p.poll() is None and lock.exists())
        finally:
            p.terminate()
            p.communicate(timeout=3)
        (lock / 'pid').unlink()
        os.utime(lock, (1, 1))
        r = subprocess.run([str(ROOT / 'bin/solo'), 'true'], env=env, text=True, capture_output=True, timeout=5)
        ok('lock without pid older than 60 s is stale', r.returncode == 0 and not lock.exists())
        r = subprocess.run([str(ROOT / 'bin/solo'), 'sh', '-c',
                            'ps -p "$PPID" -o lstart= > "$SOLO_SLOT_DIR/observed"; '
                            'cmp "$SOLO_SLOT_DIR/pid_start" "$SOLO_SLOT_DIR/observed"'],
                           env=env, text=True, capture_output=True, timeout=5)
        ok('owner start-time matches process', r.returncode == 0)
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=3)
print(f'{n}/{n} cases')
