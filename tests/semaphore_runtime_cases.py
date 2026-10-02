#!/usr/bin/env python3
"""Real multi-slot admission with fake hardware; bounded processes and private temporary locks."""
import os
import signal
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


with tempfile.TemporaryDirectory(prefix='slot-cases-') as t:
    tmp = Path(t)
    (tmp / 'sitecustomize.py').write_text('import platform\nplatform.system = lambda: "Darwin"\n')
    (tmp / 'bin').mkdir()
    reader = tmp / 'bin/sysctl'
    reader.write_text('#!/bin/sh\ncase "$2" in\n'
                      'hw.perflevel0.logicalcpu|hw.ncpu) cat "$FAKE_DIR/cores" ;;\n'
                      'hw.memsize) cat "$FAKE_DIR/ram" ;;\n'
                      'kern.memorystatus_level) cat "$FAKE_DIR/free" ;;\n'
                      '*) exit 1 ;;\nesac\n')
    reader.chmod(0o755)
    (tmp / 'cores').write_text('15')
    (tmp / 'ram').write_text(str(24 * 2**30))
    (tmp / 'free').write_text('50')
    config = tmp / 'config.toml'
    config.write_text(f'[semaphore]\nlock_dir="{tmp}/lock"\npoll_s=1\n[logs]\ndir="{tmp}/logs"\n')
    env = dict(os.environ, AGENT_THROTTLE_CONFIG=str(config), PYTHONPATH=t, FAKE_DIR=t,
               PATH=str(tmp / 'bin') + os.pathsep + os.environ['PATH'], AT_PYTHON=sys.executable, READY=str(tmp / 'ready'))
    for key in ('SOLO_SLOTS', 'SOLO_CORES', 'SOLO_GATE_MB', 'SOLO_GATE_PCT', 'SOLO_TIMEOUT'):
        env.pop(key, None)
    processes = []

    def run(*args, extra=None):
        return subprocess.run([str(ROOT / 'bin/solo'), *args], env=dict(env, **(extra or {})),
                              text=True, capture_output=True, timeout=12)

    def hold():
        (tmp / 'ready').unlink(missing_ok=True)
        p = subprocess.Popen([str(ROOT / 'bin/solo'), 'sh', '-c',
                              'printf "%s" "$SOLO_W" > "$READY"; sleep 3'], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        processes.append(p)
        deadline = time.monotonic() + 5
        while not (tmp / 'ready').exists() and p.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (tmp / 'ready').exists(), 'holder did not start'
        return p

    try:
        p = hold()
        ok('W is 8 alone on 15 cores', (tmp / 'ready').read_text() == '8')
        r = run('sh', '-c', 'echo W=$SOLO_W slot=$SOLO_SLOT')
        ok('two slots on 15 cores and 24 GB; W is 7 in pair', r.returncode == 0 and 'W=7 slot=2' in r.stdout)
        p.communicate(timeout=8)
        (tmp / 'free').write_text('1')
        p = hold()
        r = run('sh', '-c', 'echo slot=$SOLO_SLOT')
        ok('slot 2 waits when memory gate closed', r.returncode == 0 and 'memory: free 1%' in r.stdout and 'slot=1' in r.stdout)
        p.communicate(timeout=8)
        (tmp / 'free').unlink()
        p = hold()
        r = run('sh', '-c', 'echo slot=$SOLO_SLOT')
        ok('failed memory read closes the gate; slot 1 still admitted', r.returncode == 0 and 'read failed' in r.stdout
           and 'slot=1' in r.stdout)
        p.communicate(timeout=8)
        (tmp / 'free').write_text('25')
        p = hold()
        r = run('sh', '-c', 'echo slot=$SOLO_SLOT')
        ok('slot 2 admitted with no history uses 25 percent of RAM', r.returncode == 0 and 'slot=2' in r.stdout)
        p.communicate(timeout=8)
        (tmp / 'cores').write_text('10')
        (tmp / 'ram').write_text(str(16 * 2**30))
        p = hold()
        r = run('sh', '-c', 'echo slot=$SOLO_SLOT')
        ok('one slot on 10 cores and 16 GB waits', r.returncode == 0 and 'slots busy' in r.stdout and 'slot=1' in r.stdout)
        p.communicate(timeout=8)
        r = run('sh', '-c', 'echo slot=$SOLO_SLOT W=$SOLO_W', extra={'SOLO_CORES': '15', 'SOLO_SLOTS': '1'})
        ok('env overrides cores and slots', r.returncode == 0 and 'slot=1 W=8' in r.stdout)
    finally:
        for p in processes:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.communicate(timeout=7)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.communicate()
print(f'{n}/{n} cases')
