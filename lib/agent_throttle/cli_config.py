"""throttle-config: show the effective configuration, one key, or shell lines for the bash tools."""
import argparse
import json
import shutil
import sys
from pathlib import Path

from . import config

EXAMPLE = Path(__file__).resolve().parent.parent.parent / 'config' / 'config.example.toml'


def main(argv=None):
    ap = argparse.ArgumentParser(prog='throttle-config', description=__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('path', help='path of the config file (it may not exist yet)')
    sub.add_parser('show', help='effective configuration as JSON (defaults + file)')
    g = sub.add_parser('get', help='one value, by dotted key (e.g. limits.jest)')
    g.add_argument('key')
    sub.add_parser('shell', help='AT_* lines for `eval` in bash (used by solo, solo-ci, throttle-load, throttle-clean)')
    sub.add_parser('init', help='write the example config to the config path, if no file is there yet')
    a = ap.parse_args(argv)

    if a.cmd == 'path':
        print(config.config_path())
        return 0
    if a.cmd == 'init':
        path = config.config_path()
        if path.exists():
            print(f'{path}: already exists, left as is')
            return 0
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(EXAMPLE, path)
        print(f'{path}: created from the example')
        return 0
    cfg = config.load()
    if a.cmd == 'show':
        print(json.dumps(cfg, indent=2))
    elif a.cmd == 'get':
        try:
            value = config.get(cfg, a.key)
        except (KeyError, TypeError):
            print(f'unknown key: {a.key}', file=sys.stderr)
            return 2
        print(json.dumps(value) if isinstance(value, (list, dict, bool)) else value)
    elif a.cmd == 'shell':
        print('\n'.join(config.shell_lines(cfg)))
    return 0
