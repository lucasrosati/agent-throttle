#!/usr/bin/env python3
"""Add or remove the throttle-guard hook in a Claude Code settings.json, leaving everything else as it was.

usage: settings_merge.py add|remove --settings PATH --command COMMAND

- Our hook is any PreToolUse hook whose command's first word is named `throttle-guard`. `add` keeps exactly one,
  with the given command, under a "Bash" matcher (an old one from another prefix is replaced); `remove` drops ours
  and only the groups that became empty because of it.
- Before any change the file is copied to <settings>.bak-agent-throttle-YYYYmmdd-HHMMSS (with -1, -2... if that
  name is taken: a backup is never overwritten). No change, no backup.
- The new file is written atomically and read back to confirm it is valid JSON.
- A settings file that is not valid JSON (or has an unexpected shape) is left untouched: exit 3.
"""
import argparse
import datetime
import json
import os
import shlex
import shutil
import sys
import tempfile

NAME = 'throttle-guard'


def is_ours(hook):
    if not isinstance(hook, dict) or hook.get('type') != 'command':
        return False
    try:
        first = shlex.split(str(hook.get('command', '')))[0]
    except (ValueError, IndexError):
        return False
    return os.path.basename(first) == NAME


def strip_ours(pre):
    """PreToolUse list without our hooks, dropping groups that only had ours. Returns (new_list, removed_count)."""
    out, removed = [], 0
    for group in pre:
        hooks = group.get('hooks') if isinstance(group, dict) else None
        if not isinstance(hooks, list):
            out.append(group)
            continue
        kept = [h for h in hooks if not is_ours(h)]
        removed += len(hooks) - len(kept)
        if kept or not hooks:
            out.append(dict(group, hooks=kept) if len(kept) != len(hooks) else group)
    return out, removed


def write(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    mode = os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o644
    fd, tmp = tempfile.mkstemp(dir=directory, prefix='.settings-')
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write('\n')
    os.chmod(tmp, mode)
    with open(tmp, encoding='utf-8') as fh:
        json.load(fh)  # validate before replacing
    os.replace(tmp, path)
    with open(path, encoding='utf-8') as fh:
        json.load(fh)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('action', choices=('add', 'remove'))
    ap.add_argument('--settings', required=True)
    ap.add_argument('--command', help='hook command (required for add)')
    a = ap.parse_args(argv)
    if a.action == 'add' and not a.command:
        ap.error('--command is required for add')

    exists = os.path.exists(a.settings)
    if exists:
        try:
            with open(a.settings, encoding='utf-8') as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f'{a.settings}: not valid JSON ({exc}); left untouched', file=sys.stderr)
            return 3
    else:
        data = {}
    hooks = data.get('hooks', {}) if isinstance(data, dict) else None
    pre = hooks.get('PreToolUse', []) if isinstance(hooks, dict) else None
    if not isinstance(pre, list):
        print(f'{a.settings}: unexpected shape (settings, "hooks" or "PreToolUse" of the wrong type); left untouched',
              file=sys.stderr)
        return 3

    ours = [(g, h) for g in pre if isinstance(g, dict) and isinstance(g.get('hooks'), list)
            for h in g['hooks'] if is_ours(h)]
    if a.action == 'add':
        if len(ours) == 1 and ours[0][1].get('command') == a.command and ours[0][0].get('matcher') == 'Bash':
            print('hook already present')
            return 0
        new_pre, removed = strip_ours(pre)
        new_pre.append({'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': a.command}]})
        status = 'hook replaced' if removed else 'hook added'
    else:
        if not ours:
            print('hook not present')
            return 0
        new_pre, _ = strip_ours(pre)
        status = 'hook removed'

    new = dict(data)
    new_hooks = dict(hooks)
    if new_pre:
        new_hooks['PreToolUse'] = new_pre
    else:
        new_hooks.pop('PreToolUse', None)
    if new_hooks:
        new['hooks'] = new_hooks
    else:
        new.pop('hooks', None)
    if exists:
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        backup, n = f'{a.settings}.bak-agent-throttle-{stamp}', 0
        while os.path.exists(backup):  # two changes in the same second must not overwrite the first backup
            n += 1
            backup = f'{a.settings}.bak-agent-throttle-{stamp}-{n}'
        shutil.copy2(a.settings, backup)
        print(f'backup: {backup}')
    write(a.settings, new)
    print(status)
    return 0


if __name__ == '__main__':
    sys.exit(main())
