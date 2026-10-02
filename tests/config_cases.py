#!/usr/bin/env python3
"""Config cases: defaults, TOML overrides seen by the guard, fallbacks, RAM formula, and the hook end to end through
bin/throttle-guard. Usage: python3 tests/config_cases.py (rc=0 = every case passed)."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT_DIR = os.environ.get('THROTTLE_ROOT', os.path.dirname(HERE))
LIB = os.path.join(ROOT_DIR, 'lib')
sys.path.insert(0, LIB)
TMP = tempfile.mkdtemp(prefix='config-cases-')
os.environ['XDG_STATE_HOME'] = os.path.join(TMP, 'state')
os.environ['AGENT_THROTTLE_CONFIG'] = os.path.join(TMP, 'none.toml')

os.environ['SOLO_CORES'] = '7'
from agent_throttle import config  # noqa: E402

fails = n = 0


def ok(name, cond):
    global fails, n
    n += 1
    if cond:
        print(f'ok   {name}')
    else:
        fails += 1
        print(f'FAILED {name}')


def write_cfg(text):
    path = os.path.join(TMP, f'c{n}.toml')
    with open(path, 'w') as fh:
        fh.write(text)
    os.environ['AGENT_THROTTLE_CONFIG'] = path
    return path


def fresh_guard():
    spec = importlib.util.spec_from_file_location(f'guard{n}', os.path.join(LIB, 'agent_throttle', 'guard.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rule(g, cmd, cwd=None):
    hit = g.check_rule(cmd, cwd=cwd)
    return hit[0] if hit else 'ok'


# ---- defaults and formula
cfg = config.load()
ok('no file: defaults, not loaded', cfg['limits']['jest'] == [4, 2] and cfg['_loaded'] is False)
ok('logs dir follows XDG_STATE_HOME', cfg['logs']['dir'] == os.path.join(TMP, 'state', 'agent-throttle'))
ok('report dir defaults to <logs>/reports', cfg['report']['output_dir'] == os.path.join(cfg['logs']['dir'], 'reports'))
ok('max_agents formula: 8->1, 16->5, 24->9, 32->12, 64->12',
   [config.max_agents_for(g) for g in (8, 16, 24, 32, 64)] == [1, 5, 9, 12, 12])
ok('max_agents formula: tiny or unknown RAM', config.max_agents_for(4) == 1 and config.max_agents_for(None) == 3)
ok('auto max_agents resolved to a number', isinstance(cfg['load']['max_agents'], int))

# ---- runtime defaults and explicit overrides
os.environ['SOLO_CORES'] = '15'
dynamic = config.load()
ok('derived Jest and Playwright ceilings', dynamic['limits']['jest'] == [8, 2] and dynamic['limits']['playwright'] == [4, 1])
ok('new slot and worker keys default to documented values', dynamic['semaphore']['slots'] == 'auto'
   and dynamic['semaphore']['cores'] == 'auto' and dynamic['semaphore']['playwright_max_workers'] == 4)
g = fresh_guard()
ok('derived guard limit passes and blocks at boundary', rule(g, 'solo jest --maxWorkers=8') == 'ok'
   and rule(g, 'solo jest --maxWorkers=9') == 'jest-over-limit'
   and rule(g, 'solo playwright test --workers=4') == 'ok'
   and rule(g, 'solo playwright test --workers=5') == 'playwright-over-limit')
ok('load thresholds default to documented values', dynamic['load']['swap_window_min'] == 30
   and dynamic['load']['swap_delta_mb'] == 512 and dynamic['load']['swap_warn_ram_divisor'] == 8
   and dynamic['load']['min_free_pct'] == 25 and dynamic['_max_swap_explicit'] is False)
os.environ['SOLO_CORES'] = '7'

# ---- the guard reads limits, aliases and heavy scripts from the file
write_cfg('[limits]\njest = [6, 3]\nplaywright = [1, 1]\n[semaphore]\naliases = ["valida"]\n'
          '[guard]\nheavy_scripts = ["ci:full"]\n[load]\nmax_agents = 7\n')
g = fresh_guard()
ok('jest outside limit from config (3 allowed)', rule(g, 'npx jest src/a.spec.ts --maxWorkers=3') == 'ok')
ok('jest outside limit from config (4 blocked)', rule(g, 'npx jest src/a.spec.ts --maxWorkers=4') == 'jest-over-limit')
ok('jest inside limit from config (6 allowed)', rule(g, 'solo npx jest --ci --maxWorkers=6') == 'ok')
ok('playwright inside limit from config (2 blocked)', rule(g, 'solo npx playwright test --workers=2') == 'playwright-over-limit')
ok('alias counts as inside the semaphore', rule(g, 'valida npx jest --ci --maxWorkers=4') == 'ok')
ok('solo still counts with aliases set', rule(g, '~/.local/bin/solo npx jest --ci --maxWorkers=4') == 'ok')
ok('custom heavy script blocked outside', rule(g, 'pnpm run ci:full') == 'heavy-script-outside-semaphore')
ok('default heavy script replaced by the list', rule(g, 'pnpm run typecheck') == 'ok')
ok('message names the configured ceilings', '<=6 inside solo' in (g.check('npx jest src/a.spec.ts --maxWorkers=9') or ''))
ok('explicit max_agents kept', config.load()['load']['max_agents'] == 7)

# ---- fallbacks: broken values warn and keep the default
write_cfg('[limits]\njest = [4]\nvitest = "2"\n[semaphore]\ntimeout_s = -5\npoll_s = true\n[nope]\nx = 1\n')
res = subprocess.run([sys.executable, os.path.join(ROOT_DIR, 'bin', 'throttle-config'), 'show'], capture_output=True, text=True)
shown = json.loads(res.stdout)
ok('invalid limits fall back', shown['limits']['jest'] == [4, 2] and shown['limits']['vitest'] == [4, 2])
ok('negative/boolean numbers fall back', shown['semaphore']['timeout_s'] == 1200 and shown['semaphore']['poll_s'] == 15)
ok('warnings on stderr name the keys', all(k in res.stderr for k in ('limits.jest', 'semaphore.timeout_s', '"nope"')))
write_cfg('this is = = not toml')
res = subprocess.run([sys.executable, os.path.join(ROOT_DIR, 'bin', 'throttle-config'), 'get', 'limits.jest'],
                     capture_output=True, text=True)
ok('broken file: defaults and a warning', res.stdout.strip() == '[4, 2]' and 'could not read' in res.stderr)

# ---- shell lines are safe to eval
write_cfg('[semaphore]\nlock_dir = "/tmp/a b;touch /tmp/pwned-$USER"\n[semaphore.env]\nFOO = "x y $(id)"\n')
res = subprocess.run([sys.executable, os.path.join(ROOT_DIR, 'bin', 'throttle-config'), 'shell'], capture_output=True, text=True)
out = subprocess.run(['bash', '-c', res.stdout + '\nprintf "%s|%s" "$AT_SEMAPHORE_LOCK_DIR" "$FOO"'],
                     capture_output=True, text=True).stdout
ok('shell output quotes values (no command substitution)', out == '/tmp/a b;touch /tmp/pwned-$USER|x y $(id)')

# ---- the hook end to end (JSON on stdin, exit 2, block log) and the manual --check
os.environ['AGENT_THROTTLE_CONFIG'] = os.path.join(TMP, 'none.toml')
hook = os.path.join(ROOT_DIR, 'bin', 'throttle-guard')
env = dict(os.environ, AT_PYTHON=sys.executable)
blocks = os.path.join(TMP, 'state', 'agent-throttle', 'guard-blocks.log')


def run_hook(cmd):
    payload = json.dumps({'tool_name': 'Bash', 'tool_input': {'command': cmd}, 'cwd': TMP})
    return subprocess.run([hook], input=payload, capture_output=True, text=True, env=env)


r = run_hook('npx jest --ci TOKEN=abc')
ok('hook blocks with exit 2 and a reason on stderr', r.returncode == 2 and 'jest-no-workers' in r.stderr)
with open(blocks) as fh:
    lines = fh.read().splitlines()
ok('block log: header + one line, 4 columns', lines[0] == 'date\tcwd\trule\tcommand' and len(lines[1].split('\t')) == 4)
ok('block log masks VAR=value', 'TOKEN=***' in lines[1] and 'abc' not in lines[1])
r = run_hook('ls -la')
ok('hook fast path lets unrelated commands through', r.returncode == 0 and r.stderr == '')
r = run_hook('npx jest src/a.spec.ts --maxWorkers=2')
ok('hook lets a targeted run through', r.returncode == 0)
r = subprocess.run([hook], input='not json', capture_output=True, text=True, env=env)
ok('hook with invalid JSON fails open', r.returncode == 0)
r = subprocess.run([hook, '--check', 'npx vitest'], capture_output=True, text=True, env=env)
ok('--check prints the rule and exits 2', r.returncode == 2 and r.stdout.startswith('blocked (watch-blocked)'))
r = subprocess.run([hook, '--check', 'solo npx vitest run --maxWorkers=4'], capture_output=True, text=True, env=env)
ok('--check ok exits 0', r.returncode == 0 and r.stdout.strip() == 'ok')
with open(blocks) as fh:
    ok('--check does not write the block log', len(fh.read().splitlines()) == 2)
write_cfg('[guard]\nlog_blocks = false\n')
env['AGENT_THROTTLE_CONFIG'] = os.environ['AGENT_THROTTLE_CONFIG']
run_hook('npx jest --ci')
with open(blocks) as fh:
    ok('log_blocks = false writes nothing', len(fh.read().splitlines()) == 2)

print(f'{n - fails}/{n} cases')
sys.exit(1 if fails else 0)
