"""Lower worker flags without evaluating command text. The CLI returns NUL-delimited argv."""
import os
import re
import sys
from pathlib import Path


def budget(cores, occupied):
    return max(1, min((cores + 1) // 2, cores // max(1, occupied)))


def runner(argv):
    """Find an executable runner, not a word inside an arbitrary command's arguments."""
    words = list(argv)
    for _ in range(8):
        if not words:
            return None
        head = words[0].rsplit('/', 1)[-1]
        if head in ('jest', 'vitest', 'pytest', 'py.test', 'playwright'):
            return ('pytest' if head == 'py.test' else head) if head != 'playwright' or 'test' in words[1:] else None
        if head == 'node':
            for arg in words[1:]:
                if re.search(r'/(?:jest|jest-cli)/bin/jest\.js$|/\.bin/jest$', arg):
                    return 'jest'
                if re.search(r'/(?:@playwright/test|playwright(?:-core)?)/cli\.js$|/\.bin/playwright$', arg):
                    return 'playwright' if 'test' in words else None
                if arg.endswith('/vitest/vitest.mjs'):
                    return 'vitest'
            return None
        if re.fullmatch(r'python(?:\d+(?:\.\d+)?)?', head):
            return 'pytest' if '-m' in words and words[words.index('-m') + 1:][:1] in (['pytest'], ['py.test']) else None
        if head in ('npm', 'pnpm', 'yarn') and ('run' in words or head == 'npm' and words[1:2] in (['test'], ['t'], ['tst'])):
            return 'forwarded' if '--' in words else None
        if head in ('npx', 'env', 'uv', 'poetry', 'pnpm', 'yarn', 'npm'):
            words = words[1:]
            while words and (words[0] in ('exec', 'dlx', 'run') or words[0].startswith('-') or
                             re.match(r'^[A-Za-z_]\w*=', words[0])):
                takes = words[0] in ('--package', '-p', '-C', '--dir', '--directory', '--project', '--env-file')
                words = words[2:] if takes else words[1:]
            continue
        return None
    return None


def rewrite(argv, w, cores, wpw):
    kind = runner(argv)
    if not kind:
        return list(argv), []
    cap = wpw if kind == 'playwright' else w
    flags = {'--maxWorkers'} if kind in ('jest', 'vitest') else {'--workers'} if kind == 'playwright' else \
        {'-n', '--numprocesses'} if kind == 'pytest' else {'--maxWorkers', '--workers'}
    if kind == 'jest':
        flags.add('-w')
    if kind == 'playwright':
        flags.add('-j')
    out, notes, seen, i = [], [], False, 0

    def lowered(flag, value):
        limit = wpw if flag == '--workers' else cap
        match = re.fullmatch(r'(\d+)(%?)', value.strip())
        n = (max(1, cores * int(match[1]) // 100) if match[2] else int(match[1])) if match else None
        if n is None or n > limit:
            notes.append(f'{flag} {value}->{limit}')
            return str(limit)
        return value

    start = argv.index('--') + 1 if kind == 'forwarded' else 1
    while i < len(argv):
        arg = argv[i]
        name, eq, val = arg.partition('=')
        if i >= start and name in flags and eq:
            out.append(name + '=' + lowered(name, val))
            seen = True
        elif i >= start and name in flags and i + 1 < len(argv):
            out.extend((arg, lowered(arg, argv[i + 1])))
            seen = True
            i += 1
        elif i >= start and kind in ('jest', 'playwright', 'pytest') and re.fullmatch(
                r'(-w|-j|-n)\d+%?', arg) and arg[:2] in flags:
            out.append(arg[:2] + lowered(arg[:2], arg[2:]))
            seen = True
        else:
            out.append(arg)
            if kind == 'jest' and arg in ('--runInBand', '-i'):
                seen = True
        i += 1
    if not seen and kind in ('jest', 'playwright'):
        flag = '--maxWorkers' if kind == 'jest' else '--workers'
        out.append(f'{flag}={cap}')
        notes.append(f'injected {flag}={cap}')
    return out, notes


if __name__ == '__main__':
    w, c, pw = (int(x) for x in sys.argv[1:4])
    args, changes = rewrite(sys.argv[4:], w, c, pw)
    Path(os.environ['SOLO_SLOT_DIR'], 'argvnote').write_text('; '.join(changes), encoding='utf-8')
    sys.stdout.buffer.write(b''.join(arg.encode() + b'\0' for arg in args))
