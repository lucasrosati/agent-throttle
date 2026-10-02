"""Configuration: built-in defaults, overridden by the TOML file.

The file lives at $AGENT_THROTTLE_CONFIG, or ${XDG_CONFIG_HOME:-~/.config}/agent-throttle/config.toml. A missing file
is normal (defaults apply). A broken file, an unknown key or a value of the wrong type prints a warning on stderr and
the default is used for what could not be read: the tools never stop working because of the config.
"""
import copy
import os
import re
import shlex
import sys
from pathlib import Path

import tomllib

from . import machine

DEFAULTS = {
    'semaphore': {
        'aliases': [],                        # extra command names the guard treats as "inside the semaphore"
        'lock_dir': '/tmp/agent-throttle.lock',  # fixed path: an agent sandbox may change $TMPDIR
        'timeout_s': 1200,
        'poll_s': 15,
        'stale_lock_s': 60,
        'slots': 'auto',
        'cores': 'auto',
        'playwright_max_workers': 4,
        'env': {'PLAYWRIGHT_HTML_OPEN': 'never'},
        'ci': {'timeout_s': 3600, 'interval_s': 30, 'min_checks': 0},
    },
    'limits': {  # [inside the semaphore, outside]; Jest/Playwright defaults are resolved at load time
        'jest': [4, 2],
        'vitest': [4, 2],
        'pytest': [4, 2],
        'playwright': [2, 1],
    },
    'guard': {
        'heavy_scripts': ['validate:local', 'typecheck'],
        'log_blocks': True,
    },
    'load': {
        'min_free_pct': 25,
        'max_swap_mb': 3072,
        'max_agents': 'auto',                 # 'auto' = derived from total RAM, see max_agents_for()
        'agent_processes': ['claude', 'codex'],
        'test_process_regex': 'jest|vitest|pytest|playwright|(^|[/ ])tsc( |$)',
    },
    'clean': {
        'worktree_dirs': ['.claude/worktrees', '.worktrees'],
        'artifact_dirs': ['.next', 'coverage'],
        'process_regex': 'jest|vitest|tsc --noEmit',
    },
    'logs': {
        'dir': '',                            # '' = ${XDG_STATE_HOME:-~/.local/state}/agent-throttle
        'retention_days': 60,
    },
    'report': {
        'output_dir': '',                     # '' = <logs.dir>/reports
        'repos_dir': '',                      # '' = no pull request count from GitHub
        'wait_p95_target_s': 180,
        'min_free_target_pct': 20,
        'high_sessions': 4,
    },
}

LOG_FILES = {'semaphore': 'semaphore.log', 'guard': 'guard-blocks.log', 'load': 'load.log'}


def warn(msg):
    print(f'[agent-throttle] config: {msg}', file=sys.stderr)


def config_path():
    env = os.environ.get('AGENT_THROTTLE_CONFIG')
    if env:
        return Path(env).expanduser()
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return Path(base) / 'agent-throttle' / 'config.toml'


def total_ram_gb():
    try:
        return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 2 ** 30
    except (ValueError, OSError, AttributeError):
        return None


def max_agents_for(ram_gb):
    """Agents coding at once for a machine with `ram_gb` of RAM: floor((RAM - 6) / 2), between 1 and 12.

    6 GB are reserved for the OS and desktop apps (~2 GB) plus ONE heavy validation inside the semaphore (full test
    suites peaked at 3.5 to 6 GB in our measurements). Each coding agent gets 2 GB: the agent session itself (0.3 to
    0.8 GB) plus what it starts (language servers, dev servers, single-file test runs with 2 workers). A 16 GB machine
    gets 5, which is what held up in daily use. Calibrate with your own numbers: see config.example.toml.
    """
    if not ram_gb:
        return 3
    return max(1, min(12, int((ram_gb - 6) // 2)))


def _same_type(default, value):
    if isinstance(default, bool) or isinstance(value, bool):
        return isinstance(default, bool) and isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int)
    return isinstance(value, type(default))


def _valid(path, default, value):
    if path in ('load.max_agents', 'semaphore.slots', 'semaphore.cores'):
        return value == 'auto' or (isinstance(value, int) and not isinstance(value, bool) and value > 0)
    if path == 'semaphore.playwright_max_workers':
        return isinstance(value, int) and not isinstance(value, bool) and value > 0
    if path.startswith('limits.'):
        return (isinstance(value, list) and len(value) == 2 and
                all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in value))
    if path == 'semaphore.env':
        return isinstance(value, dict) and all(re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', k) and isinstance(v, str)
                                               for k, v in value.items())
    if isinstance(default, list):
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    if isinstance(default, int) and not isinstance(default, bool):
        return _same_type(default, value) and value >= 0
    return _same_type(default, value)


def _merge(base, data, prefix=''):
    for key, value in data.items():
        path = f'{prefix}{key}'
        if key not in base:
            if prefix == 'limits.':  # a new runner name is harmless; the guard only reads the ones it knows
                base[key] = value
                continue
            warn(f'unknown key "{path}" (ignored)')
            continue
        if isinstance(base[key], dict) and path != 'semaphore.env':
            if isinstance(value, dict):
                _merge(base[key], value, path + '.')
            else:
                warn(f'"{path}" must be a table (ignored)')
            continue
        if _valid(path, base[key], value):
            base[key] = value
        else:
            warn(f'invalid value for "{path}": {value!r} (using {base[key]!r})')


def load(path=None):
    """Effective configuration (a new dict each call)."""
    cfg = copy.deepcopy(DEFAULTS)
    path = Path(path) if path else config_path()
    cfg['_file'] = str(path)
    cfg['_loaded'] = False
    data = {}
    if path.is_file():
        try:
            with open(path, 'rb') as fh:
                data = tomllib.load(fh)
            cfg['_loaded'] = True
        except (OSError, tomllib.TOMLDecodeError) as exc:
            warn(f'could not read {path}: {exc} (using defaults)')
    sem = data.get('semaphore', {})
    sem = sem if isinstance(sem, dict) else {}
    core_override = sem.get('cores', 'auto')
    if not _valid('semaphore.cores', 'auto', core_override):
        core_override = 'auto'
    c, _ = machine.settings(os.environ.get('SOLO_CORES', core_override))
    pw = sem.get('playwright_max_workers', 4)
    if not _valid('semaphore.playwright_max_workers', 4, pw):
        pw = 4
    cfg['limits']['jest'] = [(c + 1) // 2, 2]
    cfg['limits']['playwright'] = [min((c + 1) // 2, pw), 1]
    _merge(cfg, data)
    state = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    logs_dir = cfg['logs']['dir'] or os.path.join(state, 'agent-throttle')
    cfg['logs']['dir'] = os.path.expanduser(logs_dir)
    cfg['report']['output_dir'] = os.path.expanduser(cfg['report']['output_dir'] or os.path.join(cfg['logs']['dir'], 'reports'))
    cfg['report']['repos_dir'] = os.path.expanduser(cfg['report']['repos_dir']) if cfg['report']['repos_dir'] else ''
    cfg['semaphore']['lock_dir'] = os.path.expanduser(cfg['semaphore']['lock_dir'])
    if cfg['load']['max_agents'] == 'auto':
        cfg['load']['max_agents'] = max_agents_for(total_ram_gb())
    return cfg


def log_path(cfg, name):
    return os.path.join(cfg['logs']['dir'], LOG_FILES[name])


def semaphore_names(cfg):
    return {'solo'} | set(cfg['semaphore']['aliases'])


def get(cfg, dotted):
    node = cfg
    for part in dotted.split('.'):
        node = node[part]
    return node


def shell_lines(cfg):
    """`AT_<SECTION>_<KEY>=value` lines (lists joined by spaces) and `export NAME=value` for semaphore.env, quoted
    for `eval` in bash."""
    out = [f'AT_CONFIG_FILE={shlex.quote(cfg["_file"])}', f'AT_CONFIG_LOADED={int(cfg["_loaded"])}']

    def walk(node, prefix):
        for key, value in node.items():
            if key.startswith('_'):
                continue
            name = re.sub(r'[^A-Za-z0-9]', '_', f'{prefix}_{key}').upper()
            if prefix == 'AT_SEMAPHORE' and key == 'env':
                out.extend(f'export {k}={shlex.quote(v)}' for k, v in value.items())
            elif isinstance(value, dict):
                walk(value, name)
            elif isinstance(value, list):
                out.append(f'{name}={shlex.quote(" ".join(str(v) for v in value))}')
            elif isinstance(value, bool):
                out.append(f'{name}={int(value)}')
            else:
                out.append(f'{name}={shlex.quote(str(value))}')

    walk(cfg, 'AT')
    for name in LOG_FILES:
        out.append(f'AT_LOG_{name.upper()}={shlex.quote(log_path(cfg, name))}')
    return out
