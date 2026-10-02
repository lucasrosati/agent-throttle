#!/usr/bin/env python3
"""Worker caps and preload behavior, with synthetic runner executables."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get('THROTTLE_ROOT', Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(ROOT / 'lib'))
from agent_throttle import workers  # noqa: E402

n = 0


def ok(name, cond):
    global n
    assert cond, name
    n += 1
    print('ok  ' + name)


def rewrite(argv, w=8):
    return workers.rewrite(argv, w, 15, min(w, 4))[0]


ok('W is 8 alone and 7 in pair on 15 cores', workers.budget(15, 1) == 8 and workers.budget(15, 2) == 7)
ok('maxWorkers only lowered, never raised', rewrite(['jest', '--maxWorkers=12']) == ['jest', '--maxWorkers=8']
   and rewrite(['jest', '--maxWorkers=3']) == ['jest', '--maxWorkers=3'])
ok('percent and auto become W', rewrite(['jest', '--maxWorkers=80%'])[-1] == '--maxWorkers=8'
   and rewrite(['jest', '--maxWorkers=auto'])[-1] == '--maxWorkers=8'
   and rewrite(['jest', '--maxWorkers=$VAR'])[-1] == '--maxWorkers=8')
ok('lower percentage is preserved', rewrite(['jest', '--maxWorkers=50%'])[-1] == '--maxWorkers=50%')
ok('maxWorkers injected for bare jest and workers for bare playwright', rewrite(['jest']) == ['jest', '--maxWorkers=8']
   and rewrite(['playwright', 'test']) == ['playwright', 'test', '--workers=4'])
ok('playwright capped at 4', rewrite(['playwright', 'test', '-j', '12'])[-1] == '4')
ok('lower playwright workers preserved', rewrite(['playwright', 'test', '--workers=2'])[-1] == '--workers=2')
ok('short flags lowered', rewrite(['jest', '-w12'])[-1] == '-w8'
   and rewrite(['playwright', 'test', '-j12'])[-1] == '-j4')
ok('pytest flags lowered', rewrite(['python3', '-m', 'pytest', '-n12'])[-1] == '-n8'
   and rewrite(['pytest', '--numprocesses', 'auto'])[-1] == '8')
ok('Vitest flag lowered', rewrite(['vitest', 'run', '--maxWorkers', '12'])[-1] == '8')
ok('runInBand preserved without injection', rewrite(['jest', '--runInBand']) == ['jest', '--runInBand'])
ok('unrelated flags and quoted argv unchanged', rewrite(['echo', 'jest', '--workers=99', 'a b']) ==
   ['echo', 'jest', '--workers=99', 'a b'])
ok('npm forwarded runner flag lowered', rewrite(['npm', 'run', 'test', '--', '--maxWorkers=12'])[-1] == '--maxWorkers=8')
if shutil.which('node'):
    with tempfile.TemporaryDirectory(prefix='workers-') as t:
        env = dict(os.environ, SOLO_W='8', SOLO_W_PW='4', SOLO_CORES='15', SOLO_SLOT_DIR=t)
        env['NODE_OPTIONS'] = '--require="' + str(ROOT / 'lib/agent_throttle/workers-preload.cjs') + '"'
        for rel, args, want in [('node_modules/jest/bin/jest.js', [], '--maxWorkers=8'),
                                ('node_modules/@playwright/test/cli.js', ['test', '-j', '12'], 'test -j 4'),
                                ('node_modules/jest/bin/jest.js', ['--maxWorkers=3'], '--maxWorkers=3'),
                                ('node_modules/jest/bin/jest.js', ['--maxWorkers=80%'], '--maxWorkers=8')]:
            path = Path(t) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('console.log(process.argv.slice(2).join(" "))\n')
            r = subprocess.run(['node', str(path), *args], capture_output=True, text=True, env=env, timeout=5)
            ok('preload ' + want, r.returncode == 0 and r.stdout.strip() == want)
        if shutil.which('npm'):
            Path(t, 'package.json').write_text('{"scripts":{"test":"node node_modules/jest/bin/jest.js"}}')
            r = subprocess.run(['npm', 'run', '--silent', 'test'], cwd=t, env=env, capture_output=True, text=True, timeout=10)
            ok('npm script covered by preload', r.returncode == 0 and r.stdout.strip() == '--maxWorkers=8')
else:
    print('skip preload (Node is optional; required in CI)')
print(f'{n}/{n} cases')
