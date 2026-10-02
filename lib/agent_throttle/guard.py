#!/usr/bin/env python3
"""guard: PreToolUse(Bash) hook for Claude Code (entry point: bin/throttle-guard).

Bounded heavy validation on the machine (`solo`) and parallelism with a LITERAL number. Rules per runner
(ceiling inside `solo` / outside, from [limits] in the config; defaults below):
  - Jest (derived/2) and Vitest (4/2): workers required (--maxWorkers=N; in Vitest also --no-file-parallelism = 1), even
    with an explicit file; the full suite (no filter) only through `solo`.
  - pytest (4/2): -n/--numprocesses only literal (auto/logical blocked); without -n it runs in 1 process and passes.
    Full suite (no path, `file::test` or --lf) only through `solo`; -k and -m do NOT count as a target.
  - A path that IS the suite does not count as a target. pytest: the rootdir, a `testpaths` entry of the config
    (pytest.ini, pyproject.toml, tox.ini, setup.cfg, found walking up from the cwd) or an ancestor of them. Vitest: the
    root (cwd or --root) or the fixed prefix of an `include` inside a `test:` block of vitest/vite.config (coverage and
    optimizeDeps do not count) or an ancestor. Jest: rootDir, `roots` and the fixed prefix of `testMatch`, from
    jest.config.* or the "jest" key of package.json found walking up from the cwd (also for --testPathPattern).
    Playwright: the config directory and `testDir`. Without a known cwd (`--filter`, `-r`, `$VAR`) any path is a target.
  - Playwright (derived/1): --workers/-j required and literal; full suite only through `solo`; --ui, --debug, PWDEBUG,
    PWTEST_WATCH and the interactive commands (show-report, codegen, open) are always blocked.
  - Watch mode (jest --watch/--watchAll, vitest without run/--run, vitest watch|dev, -w/--watch) is always blocked:
    inside `solo` it would hold the slot forever.
  - Project-wide `tsc` and the heavy scripts ([guard] heavy_scripts): only through `solo`.
npm/pnpm/yarn scripts are RESOLVED from the package.json of the hook's cwd (following `cd dir &&` in the command
itself, `--prefix`/`-C`/`--dir`/`--cwd`): the script body goes through the same rules, with the extra arguments at the
end (as npm does) and npm's pre/post scripts. A test script that cannot be resolved (--filter, -r, unknown cwd) falls
back to the generic rule: blocked outside `solo`, allowed inside (`npm test` may be `node --test`, not Jest).
Recognized forms: bare binary or by path (.bin/, .venv/bin/, $VENV/), npx, pnpm exec/dlx, pnpm/yarn <bin>, npm exec,
node <runner cli>, python[3] -m pytest, uv run [options] ..., poetry/pipenv run, cross-env, env, and
`bash|sh|zsh -c "<command>"` (checked inside). Text that only MENTIONS a runner (commit -m, echo, heredoc) passes.
Each block becomes one TSV line in guard-blocks.log (date, cwd, rule, command truncated to 120 characters, without
environment variable values). Failing to write the log never changes the hook's decision.
"""
import configparser
import datetime
import glob
import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from agent_throttle import config  # noqa: E402

CFG = config.load()
LIMITS = {name: tuple(CFG['limits'][name]) for name in ('jest', 'vitest', 'pytest', 'playwright')}  # (inside, outside)
SEMAPHORE_NAMES = config.semaphore_names(CFG)
SEM = 'solo'

SEPARATORS = {';', '&&', '||', '|', '&', '|&', '(', ')', ';;'}
PREFIX_WORDS = {'time', 'nice', 'command', 'exec', 'nohup', 'caffeinate', 'env', 'builtin', 'cross-env'}
SHELLS = {'bash', 'sh', 'zsh'}
ASSIGN_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$')
HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

# ---- Jest
HARMLESS = {'--version', '-v', '--help', '-h', '--listTests', '--showConfig', '--clearCache'}
TARGETED = ('--changedSince', '--findRelatedTests', '--onlyChanged', '-o', '--lastCommit',
            '--testPathPattern', '--testPathPatterns')
VALUE_FLAGS = {
    '--maxWorkers', '-w', '--config', '-c', '--testTimeout', '--changedSince',
    '--testNamePattern', '-t', '--testPathPattern', '--testPathPatterns',
    '--testPathIgnorePatterns', '--reporters', '--coverageDirectory', '--outputFile',
    '--selectProjects', '--shard', '--seed', '--rootDir', '--roots', '--cacheDirectory',
    '--testEnvironment', '--collectCoverageFrom', '--coverageProvider', '--maxConcurrency',
    '--testRunner', '--testSequencer', '--setupFiles', '--setupFilesAfterEnv',
}
# ---- Vitest (flags checked against --help of versions 3.2, 4.1 and 5.0)
VITEST_SUBS = {'run', 'watch', 'dev', 'related', 'bench', 'list', 'init', 'typecheck'}
VITEST_HARMLESS = {'--help', '-h', '--version', '-v'}
VITEST_VALUE = {'--project', '-p', '--config', '-c', '--root', '-r', '--dir', '-t', '--testNamePattern', '--reporter',
                '--outputFile', '--pool', '--environment', '--shard', '--maxWorkers', '--minWorkers', '--mode',
                '--exclude', '--testTimeout', '--hookTimeout', '--bail', '--retry', '--api', '--browser',
                '--sequence.seed', '--diff', '--tags', '--teardownTimeout', '--maxConcurrency', '--inspect',
                '--inspectBrk', '--standalone', '--clearScreen', '--silent-level'}
# ---- pytest
PYTEST_HARMLESS = {'--help', '-h', '--version', '-V', '--collect-only', '--co', '--fixtures', '--markers',
                   '--fixtures-per-test'}
PYTEST_VALUE = {'-k', '-m', '-p', '-o', '-c', '--config-file', '--rootdir', '--deselect', '--ignore', '--ignore-glob',
                '--confcutdir', '--basetemp', '-W', '--junitxml', '--junit-xml', '--cov-report', '--cov-config',
                '--maxfail', '--durations', '--durations-min', '--tb', '-r', '--log-level', '--log-cli-level',
                '--log-file', '--capture', '--import-mode', '--timeout', '--override-ini', '--dist', '--randomly-seed',
                '--asyncio-mode', '--color', '--code-highlight', '--ds', '--envfile', '--inifile', '--debug',
                '--pdbcls', '--doctest-glob', '--log-format', '--log-date-format', '--cache-show'}
PYTEST_TARGETED = {'--lf', '--last-failed'}
# ---- Playwright (flags checked against --help of 1.62)
PW_HARMLESS = {'--help', '-h', '--version', '-V', '--list'}
PW_VALUE = {'-c', '--config', '-g', '--grep', '-G', '--grep-invert', '--project', '--reporter', '--retries',
            '--repeat-each', '--timeout', '--global-timeout', '--max-failures', '--output', '--shard', '--tsconfig',
            '--trace', '-j', '--workers', '--browser', '--ui-host', '--ui-port', '--test-list-invert', '--tag'}
PW_TARGETED_VALUE = {'--test-list'}
PW_TARGETED_FLAGS = {'--last-failed', '--only-changed'}
PW_INTERACTIVE_CMDS = {'show-report', 'codegen', 'open', 'cr', 'ff', 'wk', 'show-trace'}
# ---- tsc and scripts
TSC_HARMLESS = {'--version', '-v', '--help', '-h', '--init', '--showConfig', '--all'}
TSC_PROJECT_FLAGS = {'-p', '--project', '-b', '--build'}
TSC_FILE_RE = re.compile(r'\.(ts|tsx|mts|cts|js|jsx|mjs|cjs)$')
HEAVY_SCRIPTS = set(CFG['guard']['heavy_scripts'])
RUNNERS = {'npm', 'pnpm', 'yarn'}
TEST_SCRIPT_RE = re.compile(r'^(t|tst|test.*|.*[:-]test.*|e2e.*|.*:e2e.*|check|validate.*|verify)$')
NPM_TEST_ALIASES = {'test', 't', 'tst'}
NPM_VALUE_FLAGS = {'--prefix', '-w', '--workspace', '--loglevel', '--userconfig'}
PNPM_VALUE_FLAGS = {'-C', '--dir', '--filter', '-F', '--loglevel', '--reporter', '--workspace-root'}
YARN_VALUE_FLAGS = {'--cwd'}
UV_VALUE_FLAGS = {'--directory', '--project', '--package', '--env-file', '--with', '--with-editable',
                  '--with-requirements', '--python', '-p', '--extra', '--group', '--only-group', '--index',
                  '--default-index', '--config-file', '--cache-dir', '-C', '--config-setting'}
NPX_VALUE_FLAGS = {'-p', '--package', '-c', '--call'}
KNOWN_BINS = {'jest', 'vitest', 'playwright', 'pytest', 'py.test', 'tsc'}

BLOCKS_LOG = config.log_path(CFG, 'guard')
BLOCKS_HEADER = 'date\tcwd\trule\tcommand\n'
REDACT = [  # same rules as sanitize() in bin/solo
    (re.compile(r'(^|\s)([A-Za-z_][A-Za-z0-9_]*)=\S*'), r'\1\2=***'),
    (re.compile(r'(--?[A-Za-z0-9_-]*(?:token|secret|pass|key)[A-Za-z0-9_-]*)(=|\s+)\S+', re.I), r'\1\2***'),
    (re.compile(r'://[^/@\s]+@'), '://***@'),
]


def limit_text(runner, in_sem):
    lim_in, lim_out = LIMITS[runner]
    return f'<={lim_in} inside {SEM}' if in_sem else f'<={lim_out} outside {SEM}'


def over_limit(runner, n):
    lim_in, lim_out = LIMITS[runner]
    return f'{n} workers is above the ceiling: <={lim_in} inside {SEM}, <={lim_out} outside.'


# ---------------------------------------------------------------- tokenization

def strip_heredocs(cmd):
    out, lines, i = [], cmd.split('\n'), 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        tags = [m.group(2) for m in HEREDOC_RE.finditer(line)]
        i += 1
        for tag in tags:
            while i < len(lines) and lines[i].strip() != tag:
                i += 1
            i += 1
    return '\n'.join(out)


FD_DUP_RE = re.compile(r'(?<![\w<>])\d*>&\d+')      # 2>&1 (leaves quotes alone; removed from the text before shlex)
REDIR_ALONE_RE = re.compile(r'^(\d*>>?|&>>?|<)$')       # `>` `2>>` `<` alone: the target is the next token
REDIR_GLUED_RE = re.compile(r'^(\d*>>?|&>>?|<(?!<))\S')  # `>log` `2>/dev/null` `<in`


def drop_redirects(tokens):
    """Redirections are removed AFTER shlex (token by token): otherwise `2>` or `>log` become a positional argument,
    that is, a "target", and let a full suite through. A regex on the raw text would eat `<`/`>` inside quotes and
    break the tokenization."""
    out, skip = [], False
    for tok in tokens:
        if skip:
            skip = False
            continue
        if REDIR_ALONE_RE.match(tok):
            skip = True
            continue
        if REDIR_GLUED_RE.match(tok):
            continue
        out.append(tok)
    return out


def tokenize(cmd):
    text = FD_DUP_RE.sub(' ', strip_heredocs(cmd))
    lex = shlex.shlex(text.replace('\n', ' ; '), posix=True, punctuation_chars=';&|()')
    lex.whitespace_split = True
    return drop_redirects(list(lex))


def segments(tokens):
    seg = []
    for tok in tokens:
        if tok in SEPARATORS:
            if seg:
                yield seg
            seg = []
        else:
            seg.append(tok)
    if seg:
        yield seg


def base(word):
    return word.rsplit('/', 1)[-1]


def int_or_bad(raw):
    return int(raw) if raw.isdigit() else 'bad'


def value_after(args, i):
    return args[i + 1] if i + 1 < len(args) else ''


# ---------------------------------------------------------------- a path that IS the whole suite

GLOB_CHARS_RE = re.compile(r'[*?\[{]')
PYTEST_CFG_FILES = ('pytest.ini', '.pytest.ini', 'pyproject.toml', 'tox.ini', 'setup.cfg')  # pytest's own order
VITEST_CFG_FILES = tuple(f'{b}.config.{e}' for b in ('vitest', 'vite') for e in ('ts', 'mts', 'cts', 'js', 'mjs', 'cjs'))
_SCOPE_CACHE = {}


def covers(target, dirs):
    """`target` is one of `dirs` or an ancestor of one of them (real, absolute paths)."""
    prefix = target.rstrip('/') + '/'
    return any(d == target or d.startswith(prefix) for d in dirs)


def local_path(cwd, arg):
    """Real path of a positional argument, or None if it cannot be known (unknown cwd, $VAR, `file::test`)."""
    if not cwd or not arg or '$' in arg or '`' in arg or '::' in arg:
        return None
    return os.path.realpath(os.path.join(cwd, os.path.expanduser(arg)))


def pytest_testpaths(path):
    """testpaths of the file, [] if it configures pytest without testpaths, None if it does not configure pytest."""
    name = os.path.basename(path)
    try:
        if name == 'pyproject.toml':
            import tomllib
            with open(path, 'rb') as fh:
                pt = tomllib.load(fh).get('tool', {}).get('pytest')
            if not isinstance(pt, dict):
                return None
            opts = pt.get('ini_options', pt)  # [tool.pytest.ini_options] or [tool.pytest] (pytest 9)
            tp = opts.get('testpaths', [])
            return [tp] if isinstance(tp, str) else [str(t) for t in tp]
        cp = configparser.ConfigParser(interpolation=None)
        cp.read(path, encoding='utf-8')
        section = 'tool:pytest' if name == 'setup.cfg' else 'pytest'
        if not cp.has_section(section):
            return [] if name in ('pytest.ini', '.pytest.ini') else None  # pytest.ini counts even when empty
        return cp.get(section, 'testpaths', fallback='').split()
    except Exception:
        return None


def pytest_suite_dirs(cwd, cfg=None, rootdir=None):
    """Directories that ARE the pytest suite: the testpaths of the config (found walking up from cwd, or -c) or the
    rootdir."""
    key = ('pytest', cwd, cfg, rootdir)
    if key not in _SCOPE_CACHE:
        found = None
        if cfg:
            found = (os.path.dirname(cfg), pytest_testpaths(cfg) or [])
        else:
            d = cwd
            while found is None:
                for name in PYTEST_CFG_FILES:
                    p = os.path.join(d, name)
                    tp = pytest_testpaths(p) if os.path.isfile(p) else None
                    if tp is not None:
                        found = (d, tp)
                        break
                parent = os.path.dirname(d)
                if parent == d:
                    break
                d = parent
        root, tps = found or (cwd, [])
        root = rootdir or root
        dirs = []
        for t in tps:
            pat = os.path.join(root, t)
            dirs += glob.glob(pat, recursive=True) if GLOB_CHARS_RE.search(t) else [pat]
        _SCOPE_CACHE[key] = [os.path.realpath(d) for d in dirs] or [os.path.realpath(root)]
    return _SCOPE_CACHE[key]


def js_close(text, i):
    """Index of the closing bracket for the `{`/`[` at text[i], skipping strings and comments (len(text) if open)."""
    depth, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in '"\'`':
            i += 1
            while i < n and text[i] != c:
                i += 2 if text[i] == '\\' else 1
        elif text.startswith('//', i):
            j = text.find('\n', i)
            i = n if j < 0 else j
        elif text.startswith('/*', i):
            j = text.find('*/', i)
            i = n if j < 0 else j + 1
        elif c in '{[':
            depth += 1
        elif c in '}]':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def js_blocks(text, key, opener):
    """`key: {...}` (opener '{') or `key: [...]` (opener '[') snippets of JS/TS text."""
    rx = re.compile(r'(?<![\w$.])["\']?' + key + r'["\']?\s*:\s*' + re.escape(opener))
    return [text[m.end() - 1:js_close(text, m.end() - 1) + 1] for m in rx.finditer(text)]


def vitest_include_prefixes(text):
    """Fixed prefix (before the first glob) of each `include` inside `test:` blocks, except those of `coverage`."""
    prefixes = set()
    for block in js_blocks(text, 'test', '{'):
        for cov in js_blocks(block, 'coverage', '{'):
            block = block.replace(cov, '{}')
        for arr in js_blocks(block, 'include', '['):
            for m in re.finditer(r'(["\'`])((?:(?!\1).)*)\1', arr):
                pat = m.group(2)
                if not GLOB_CHARS_RE.search(pat) or pat.startswith('!'):
                    continue
                parts = []
                for part in re.sub(r'^\./', '', pat).split('/'):
                    if GLOB_CHARS_RE.search(part):
                        break
                    parts.append(part)
                prefixes.add('/'.join(parts))
    return prefixes


def vitest_suite_dirs(cwd, cfg=None, root=None):
    """Directories that ARE the Vitest suite: the root + the fixed prefix of each include (Vitest default = root)."""
    root = root or cwd
    key = ('vitest', root, cfg)
    if key not in _SCOPE_CACHE:
        files = [cfg] if cfg else [os.path.join(root, n) for n in VITEST_CFG_FILES[:6]]
        files = [f for f in files if os.path.isfile(f)] or \
            [f for f in (os.path.join(root, n) for n in VITEST_CFG_FILES[6:]) if os.path.isfile(f)][:1]
        files += glob.glob(os.path.join(root, 'vitest.workspace.*')) + glob.glob(os.path.join(root, 'vitest.projects.*'))
        prefixes = set()
        for f in files:
            try:
                with open(f, encoding='utf-8') as fh:
                    prefixes |= vitest_include_prefixes(fh.read())
            except Exception:
                pass
        _SCOPE_CACHE[key] = [os.path.realpath(os.path.join(root, p)) for p in sorted(prefixes or {''})]
    return _SCOPE_CACHE[key]


STR_RE = re.compile(r'(["\'`])((?:(?!\1).)*)\1')
JEST_CFG_FILES = tuple(f'jest.config.{e}' for e in ('js', 'ts', 'mjs', 'cjs', 'mts', 'cts', 'json'))
PW_CFG_FILES = tuple(f'playwright.config.{e}' for e in ('ts', 'js', 'mts', 'mjs', 'cts', 'cjs'))


def glob_prefix(pat):
    """Fixed part (before the first glob) of a pattern, without `./` or `<rootDir>/`."""
    parts = []
    for part in re.sub(r'^(<rootDir>/|\./)', '', pat).split('/'):
        if GLOB_CHARS_RE.search(part) or part in ('', '.'):
            break
        parts.append(part)
    return '/'.join(parts)


def jest_config(cwd, cfg=None):
    """(config directory, dict with rootDir/roots/testMatch) found the way Jest finds it: -c, or walking up from the cwd
    to the first jest.config.* or package.json. A JS/TS config is read with regexes (string literals only)."""
    path = cfg
    if not path:
        d = cwd
        while path is None:
            for name in JEST_CFG_FILES + ('package.json',):
                if os.path.isfile(os.path.join(d, name)):
                    path = os.path.join(d, name)
                    break
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    if not path or not os.path.isfile(path):
        return cwd, {}
    try:
        with open(path, encoding='utf-8') as fh:
            text = fh.read()
        if path.endswith('.json'):
            data = json.loads(text)
            data = (data.get('jest') or {}) if os.path.basename(path) == 'package.json' else data
            return os.path.dirname(path), data if isinstance(data, dict) else {}
        conf = {}
        m = re.search(r'(?<![\w$.])rootDir\s*:\s*(["\'`])([^"\'`]*)\1', text)
        if m:
            conf['rootDir'] = m.group(2)
        for key in ('roots', 'testMatch'):
            vals = [m.group(2) for arr in js_blocks(text, key, '[') for m in STR_RE.finditer(arr)]
            if vals:
                conf[key] = vals
        return os.path.dirname(path), conf
    except Exception:
        return os.path.dirname(path), {}


def jest_suite_dirs(cwd, cfg=None, rootdir=None):
    """Directories that ARE the Jest suite: rootDir, `roots` and the fixed prefix of each `testMatch`."""
    key = ('jest', cwd, cfg, rootdir)
    if key not in _SCOPE_CACHE:
        base_dir, conf = jest_config(cwd, cfg)
        root = rootdir or os.path.join(base_dir, str(conf.get('rootDir') or '.'))
        sub = lambda v: os.path.join(root, str(v).replace('<rootDir>', '.'))  # noqa: E731
        dirs = [root] + [sub(r) for r in conf.get('roots') or []]
        dirs += [os.path.join(root, glob_prefix(str(t))) for t in conf.get('testMatch') or [] if glob_prefix(str(t))]
        _SCOPE_CACHE[key] = sorted({os.path.realpath(d) for d in dirs})
    return _SCOPE_CACHE[key]


def playwright_suite_dirs(cwd, cfg=None):
    """Directories that ARE the Playwright suite: the config directory and each literal `testDir` in it."""
    key = ('playwright', cwd, cfg)
    if key not in _SCOPE_CACHE:
        path = cfg if cfg and os.path.isfile(cfg) else \
            next((os.path.join(cwd, n) for n in PW_CFG_FILES if os.path.isfile(os.path.join(cwd, n))), None)
        base_dir, dirs = (os.path.dirname(path), []) if path else (cwd, [])
        if path:
            try:
                with open(path, encoding='utf-8') as fh:
                    text = fh.read()
                rx = r'(?<![\w$.])testDir\s*:\s*(?:path\.(?:join|resolve)\(\s*__dirname\s*,\s*)?(["\'`])([^"\'`]+)\1'
                dirs = [os.path.join(base_dir, m.group(2)) for m in re.finditer(rx, text)]
            except Exception:
                pass
        _SCOPE_CACHE[key] = sorted({os.path.realpath(d) for d in dirs + [base_dir]})
    return _SCOPE_CACHE[key]


def wide_target(arg, cwd, dirs):
    """Short description if the positional argument covers the whole suite (root, testpaths/include or ancestor)."""
    path = local_path(cwd, arg)
    if path and covers(path, dirs):
        rel = sorted(os.path.relpath(d, os.path.realpath(cwd)) for d in dirs)
        return f'`{arg}` covers the suite: {", ".join(rel)}'
    return None


def suite_msg(what, wide, how):
    return (f"Full {what} suite only through '{SEM} <command>' (bounded heavy validation on the machine)"
            f"{' (' + wide + ')' if wide else ''}. To iterate, pass {how}.")


# ---------------------------------------------------------------- Jest

def workers(args):
    """None = not set; int = effective ceiling; 'bad' = value not literal (percentage, variable)."""
    val = None
    it = iter(range(len(args)))
    for i in it:
        a = args[i]
        if a in ('--runInBand', '-i'):
            val = 1
        elif a.startswith('--maxWorkers=') or (a.startswith('-w') and len(a) > 2 and not a.startswith('-w=')):
            raw = a.split('=', 1)[1] if '=' in a else a[2:]
            val = int_or_bad(raw)
        elif a.startswith('-w='):
            val = int_or_bad(a[3:])
        elif a in ('--maxWorkers', '-w'):
            val = int_or_bad(value_after(args, i))
            next(it, None)
    return val


JEST_PATTERN_FLAGS = ('--testPathPattern', '--testPathPatterns')


def jest_targets(args):
    """(strong_target, filters, options): strong target = --changedSince/--findRelatedTests/-o/--lastCommit; filters =
    positionals and values of --testPathPattern(s) (they may be the whole suite); options = --config/-c and --rootDir."""
    strong, filters, opts, i = False, [], {}, 0
    while i < len(args):
        a = args[i]
        name, eq, val = a.partition('=')
        if name in JEST_PATTERN_FLAGS:
            filters.append(val if eq else value_after(args, i))
            i += 1 if eq else 2
            continue
        if name in TARGETED:
            strong = True
        if name in ('--config', '-c', '--rootDir'):
            opts[name] = val if eq else value_after(args, i)
        if a in VALUE_FLAGS:
            i += 2
            continue
        if not a.startswith('-'):
            filters.append(a)  # positional = file path or pattern
        i += 1
    return strong, filters, opts


def rule_jest(args, in_sem, via_script=False, cwd=None):
    if any(a in HARMLESS for a in args):
        return None
    if any(a.split('=', 1)[0] in ('--watch', '--watchAll') and not a.endswith('=false') for a in args):
        return 'watch-blocked', (f'Jest in watch mode (--watch/--watchAll) never ends: always blocked, even inside {SEM} '
                                 '(it would hold the slot). Run the spec once.')
    limit = LIMITS['jest'][0 if in_sem else 1]
    hint = ' (with npm/pnpm/yarn test scripts, jest arguments go after `--`)' if via_script else ''
    n = workers(args)
    if n is None:
        return 'jest-no-workers', f'Jest needs --maxWorkers=N ({limit_text("jest", in_sem)}){hint}.'
    if n == 'bad':
        return 'jest-workers-not-literal', 'Jest: --maxWorkers must be a literal integer (no % and no $VAR).'
    if n > limit:
        return 'jest-over-limit', f'Jest with {over_limit("jest", n)}'
    if in_sem:
        return None
    strong, filters, opts = jest_targets(args)
    if strong:
        return None
    wide = None
    if filters and cwd:
        cfg = opts.get('--config') or opts.get('-c')
        cfg = None if not cfg or cfg.lstrip().startswith('{') else resolve_dir(cwd, cfg)
        dirs = jest_suite_dirs(os.path.realpath(cwd), cfg, resolve_dir(cwd, opts.get('--rootDir')))
        wide = next((w for w in (wide_target(f, cwd, dirs) for f in filters) if w), None)
    if not filters or wide:
        return 'jest-suite-outside-semaphore', suite_msg('Jest', wide, 'the spec path, a subfolder or '
                                                                       '--changedSince=origin/main')
    return None


# ---------------------------------------------------------------- Vitest

def rule_vitest(args, in_sem, cwd=None):
    if any(a in VITEST_HARMLESS for a in args):
        return None
    pos, flags, workers_val, is_targeted, skip, opts = [], [], None, False, False, {}
    for i, a in enumerate(args):
        if skip:
            skip = False
            continue
        name = a.split('=', 1)[0]
        if name in ('--config', '-c', '--root', '-r'):
            opts[name.lstrip('-')[0]] = a.split('=', 1)[1] if '=' in a else value_after(args, i)
        if name == '--maxWorkers':
            workers_val = int_or_bad(a.split('=', 1)[1] if '=' in a else value_after(args, i))
            skip = '=' not in a
        elif a in ('--no-file-parallelism', '--fileParallelism=false'):
            workers_val = 1 if workers_val is None else workers_val
        elif name == '--changed':
            is_targeted = True
        elif a in VITEST_VALUE:
            skip = True
        elif name in VITEST_VALUE:
            continue
        elif a.startswith('-'):
            flags.append(a)
        else:
            pos.append(a)
    sub = pos[0] if pos and pos[0] in VITEST_SUBS else None
    filters = pos[1:] if sub else pos
    if sub in ('list', 'init'):
        return None
    run_once = sub in ('run', 'bench', 'typecheck') or '--run' in flags or '--watch=false' in flags
    if sub in ('watch', 'dev') or '-w' in flags or '--watch' in flags or not run_once:
        return 'watch-blocked', (f'Vitest without `run` (or with watch/dev/--watch) stays in watch mode and never ends: '
                                 f'always blocked, even inside {SEM}. Use `vitest run ...`.')
    limit = LIMITS['vitest'][0 if in_sem else 1]
    if workers_val is None:
        return 'vitest-no-workers', (f'Vitest needs a literal --maxWorkers=N ({limit_text("vitest", in_sem)}), even with '
                                     'an explicit file, or --no-file-parallelism. Its default opens one worker per '
                                     'core minus one.')
    if workers_val == 'bad':
        return 'vitest-workers-not-literal', 'Vitest: --maxWorkers must be a literal integer (no % and no $VAR).'
    if workers_val > limit:
        return 'vitest-over-limit', f'Vitest with {over_limit("vitest", workers_val)}'
    if in_sem or is_targeted or sub == 'related':
        return None
    wide = None
    if filters and cwd:
        cfg, root = (resolve_dir(cwd, opts[k]) if k in opts else None for k in ('c', 'r'))
        dirs = vitest_suite_dirs(cwd, cfg, root)
        wide = next((w for w in (wide_target(f, cwd, dirs) for f in filters) if w), None)
    if not filters or wide:
        return 'vitest-suite-outside-semaphore', suite_msg('Vitest', wide, 'the file, a subfolder or --changed')
    return None


# ---------------------------------------------------------------- pytest

def rule_pytest(args, in_sem, cwd=None):
    if any(a in PYTEST_HARMLESS for a in args):
        return None
    n, is_targeted, skip, paths, opts = None, False, False, [], {}
    for i, a in enumerate(args):
        if skip:
            skip = False
            continue
        name = a.split('=', 1)[0]
        if name in ('-c', '--config-file', '--rootdir'):
            opts[name] = a.split('=', 1)[1] if '=' in a else value_after(args, i)
        if a in ('-n', '--numprocesses'):
            n = int_or_bad(value_after(args, i))
            skip = True
        elif a.startswith('--numprocesses='):
            n = int_or_bad(a.split('=', 1)[1])
        elif a.startswith('-n') and len(a) > 2:
            n = int_or_bad(a[2:].lstrip('='))
        elif a in PYTEST_TARGETED:
            is_targeted = True
        elif a in PYTEST_VALUE:
            skip = True
        elif a.startswith('-'):
            continue
        else:
            paths.append(a)  # path, directory or file::test
    limit = LIMITS['pytest'][0 if in_sem else 1]
    if n == 'bad':
        return 'pytest-workers-not-literal', 'pytest -n must be a literal integer (auto/logical are blocked).'
    if isinstance(n, int) and n > limit:
        return 'pytest-over-limit', f'pytest -n {n}: {over_limit("pytest", n)}'
    if in_sem or is_targeted:
        return None
    wide = None
    if paths and cwd:
        cfg = resolve_dir(cwd, opts.get('-c') or opts.get('--config-file'))
        dirs = pytest_suite_dirs(os.path.realpath(cwd), cfg, resolve_dir(cwd, opts.get('--rootdir')))
        wide = next((w for w in (wide_target(p, cwd, dirs) for p in paths) if w), None)
    if not paths or wide:
        return 'pytest-suite-outside-semaphore', suite_msg('pytest', wide, 'the file, a subfolder, `file::test` or '
                                                                           '--lf (-k and -m do not count as a target)')
    return None


# ---------------------------------------------------------------- Playwright

def rule_playwright(args, in_sem, env, cwd=None):
    if not args:
        return None
    sub, rest = args[0], args[1:]
    if sub in PW_INTERACTIVE_CMDS:
        return 'playwright-interactive', (f'`playwright {sub}` opens a server or window and waits for input: always '
                                          f'blocked (it would hold the shell and the {SEM} slot).')
    if sub != 'test':
        return None
    if any(a in PW_HARMLESS for a in rest):
        return None
    if any(a.split('=', 1)[0] in ('--ui', '--ui-host', '--ui-port', '--debug') for a in rest) or env.get('PWDEBUG'):
        return 'playwright-interactive', ('Playwright with --ui, --debug or PWDEBUG opens the UI/Inspector and waits for '
                                          'input: always blocked.')
    if env.get('PWTEST_WATCH'):
        return 'watch-blocked', 'Playwright in watch mode (PWTEST_WATCH) never ends: always blocked.'
    w, is_targeted, skip, filters, cfg = None, False, False, [], None
    for i, a in enumerate(rest):
        if skip:
            skip = False
            continue
        name = a.split('=', 1)[0]
        if name in ('-c', '--config'):
            cfg = a.split('=', 1)[1] if '=' in a else value_after(rest, i)
        if name in ('--workers', '-j'):
            w = int_or_bad(a.split('=', 1)[1] if '=' in a else value_after(rest, i))
            skip = '=' not in a
        elif a.startswith('-j') and len(a) > 2:
            w = int_or_bad(a[2:])
        elif name in PW_TARGETED_FLAGS:
            is_targeted = True
        elif name in PW_TARGETED_VALUE:
            is_targeted = True
            skip = '=' not in a
        elif a in PW_VALUE:
            skip = True
        elif a.startswith('-'):
            continue
        else:
            filters.append(a)  # positional filter (file, file:line, folder)
    limit = LIMITS['playwright'][0 if in_sem else 1]
    if w is None:
        return 'playwright-no-workers', (f'Playwright needs a literal --workers=N ({limit_text("playwright", in_sem)}); '
                                         'each worker is a browser (~0.6 GB). The default is 50% of the cores.')
    if w == 'bad':
        return 'playwright-workers-not-literal', 'Playwright: --workers must be a literal integer (no %).'
    if w > limit:
        return 'playwright-over-limit', f'Playwright with {over_limit("playwright", w)}'
    if in_sem or is_targeted:
        return None
    wide = None
    if filters and cwd:
        dirs = playwright_suite_dirs(os.path.realpath(cwd), resolve_dir(cwd, cfg))
        wide = next((w for w in (wide_target(f, cwd, dirs) for f in filters) if w), None)
    if not filters or wide:
        return 'playwright-suite-outside-semaphore', suite_msg('e2e', wide, 'the file (or file:line), a subfolder, '
                                                                            '--last-failed or --only-changed')
    return None


# ---------------------------------------------------------------- tsc and heavy scripts

def tsc_is_project(args):
    if any(a.split('=', 1)[0] in TSC_PROJECT_FLAGS for a in args):
        return True
    return not any(not a.startswith('-') and TSC_FILE_RE.search(a) for a in args)


def heavy_script(seg):
    """Name of the heavy script (validate:local/typecheck by default) called through npm/pnpm/yarn, or None."""
    if base(seg[0]) not in RUNNERS:
        return None
    for w in seg[1:]:
        if w == '--':
            break
        if w in HEAVY_SCRIPTS:
            return w
    return None


# ---------------------------------------------------------------- unwrapping the command

def skip_flags(words, value_flags):
    """Skip flags (and the value of those that take one) at the start of `words`; return (rest, values_by_flag)."""
    vals, i = {}, 0
    while i < len(words) and words[i].startswith('-') and words[i] != '--':
        a = words[i]
        name, eq, val = a.partition('=')
        if name in value_flags and not eq:
            vals[name] = value_after(words, i)
            i += 2
        else:
            if eq:
                vals[name] = val
            i += 1
    if i < len(words) and words[i] == '--':
        i += 1
    return words[i:], vals


def unwrap(words, cwd=None):
    """Strip npx/pnpm exec/npm exec/uv run/poetry run/python -m/node <cli> from the front. Return (runner, args, cwd)
    or None; cwd follows `pnpm -C/--dir`, `yarn --cwd` and `uv run --directory` (None with --filter/-r: the package is
    uncertain)."""
    for _ in range(6):
        if not words:
            return None
        w0 = base(words[0])
        if w0 == 'npx':
            words, _ = skip_flags(words[1:], NPX_VALUE_FLAGS)
        elif w0 == 'npm' and len(words) > 1 and words[1] == 'exec':
            words, _ = skip_flags(words[2:], NPX_VALUE_FLAGS)
        elif w0 in ('pnpm', 'yarn'):  # `pnpm [-C x] exec|dlx vitest ...`, `pnpm vitest ...`, `yarn --cwd x jest ...`
            rest, vals = skip_flags(words[1:], PNPM_VALUE_FLAGS | YARN_VALUE_FLAGS)  # (script_call took the scripts)
            for flag in ('-C', '--dir', '--cwd'):
                if flag in vals:
                    cwd = resolve_dir(cwd, vals[flag])
            if {'--filter', '-F'} & set(vals) or {'-r', '--recursive'} & set(words[1:]):
                cwd = None
            if rest and rest[0] in ('exec', 'dlx'):
                rest, _ = skip_flags(rest[1:], set())
            elif not rest or base(rest[0]) not in KNOWN_BINS:
                return None
            words = rest
        elif w0 == 'uv' and len(words) > 1 and words[1] == 'run':
            words, vals = skip_flags(words[2:], UV_VALUE_FLAGS)
            if '--directory' in vals:
                cwd = resolve_dir(cwd, vals['--directory'])
        elif w0 in ('poetry', 'pipenv', 'hatch', 'pdm') and len(words) > 1 and words[1] == 'run':
            words = words[2:]
        elif re.match(r'^python(\d(\.\d+)?)?$', w0):
            rest = words[1:]
            while rest and rest[0].startswith('-') and rest[0] != '-m':
                rest = rest[2:] if rest[0] in ('-X', '-W') else rest[1:]
            if len(rest) > 1 and rest[0] == '-m':
                return (('pytest', rest[2:], cwd) if rest[1] in ('pytest', 'py.test') else None)
            return None
        elif w0 == 'node':
            for i, w in enumerate(words[1:], 1):
                if re.search(r'(jest/bin/jest(\.js)?|\.bin/jest)$', w):
                    return 'jest', words[i + 1:], cwd
                if re.search(r'(vitest/vitest\.mjs|\.bin/vitest)$', w):
                    return 'vitest', words[i + 1:], cwd
                if re.search(r'(playwright(-core)?/cli\.js|\.bin/playwright)$', w):
                    return 'playwright', words[i + 1:], cwd
                if re.search(r'(typescript/bin/tsc|typescript/lib/tsc\.js|\.bin/tsc)$', w):
                    return 'tsc', words[i + 1:], cwd
            return None
        else:
            name = 'pytest' if w0 == 'py.test' else w0
            return (name, words[1:], cwd) if name in KNOWN_BINS else None
    return None


# ---------------------------------------------------------------- package.json scripts

_PKG_CACHE = {}


def read_scripts(directory):
    if not directory:
        return None
    path = os.path.join(directory, 'package.json')
    if path not in _PKG_CACHE:
        try:
            with open(path, encoding='utf-8') as fh:
                _PKG_CACHE[path] = (json.load(fh).get('scripts') or {})
        except Exception:
            _PKG_CACHE[path] = None
    return _PKG_CACHE[path]


def resolve_dir(cwd, target):
    if not target or '$' in target or '`' in target:
        return None
    target = os.path.expanduser(target)
    if os.path.isabs(target):
        return os.path.normpath(target)
    return os.path.normpath(os.path.join(cwd, target)) if cwd else None


NOT_SCRIPTS = {'exec', 'dlx', 'install', 'i', 'add', 'remove', 'create', 'why', 'list', 'ls', 'outdated', 'update', 'up',
               'link', 'unlink', 'store', 'config', 'import', 'rebuild', 'prune', 'publish', 'pack', 'audit', 'env',
               'setup', 'init', 'licenses', 'patch', 'fetch', 'deploy', 'dedupe'}


def script_call(seg, cwd):
    """If the segment calls an npm/pnpm/yarn script: (runner, name, extra_args, directory|None, resolvable).
    For pnpm/yarn, a `<name>` that is not a script but is a known binary is a binary (returns None here)."""
    w0 = base(seg[0])
    if w0 not in RUNNERS:
        return None
    rest = seg[1:]
    vf = NPM_VALUE_FLAGS if w0 == 'npm' else PNPM_VALUE_FLAGS if w0 == 'pnpm' else YARN_VALUE_FLAGS
    rest, vals = skip_flags(rest, vf)
    directory, resolvable = cwd, True
    for flag in ('--prefix', '-C', '--dir', '--cwd'):
        if flag in vals:
            directory = resolve_dir(cwd, vals[flag])
    if {'--filter', '-F', '-w', '--workspace'} & set(vals) or '-r' in seg[1:] or '--recursive' in seg[1:]:
        resolvable = False
    if not rest:
        return None
    if rest[0] in ('run', 'run-script', 'rum', 'urn'):
        rest, _ = skip_flags(rest[1:], set())
        explicit_run = True
    else:
        explicit_run = False
    if not rest:
        return None
    name, extra = rest[0], rest[1:]
    if w0 == 'npm':
        if not explicit_run and name not in NPM_TEST_ALIASES:
            return None  # npm install/ci/exec/... are not scripts
        name = 'test' if name in NPM_TEST_ALIASES and not explicit_run else name
        extra = extra[extra.index('--') + 1:] if '--' in extra else []
    else:
        if name in NOT_SCRIPTS:
            return None
        if extra and extra[0] == '--':
            extra = extra[1:]
    scripts = read_scripts(directory) if resolvable else None
    if w0 != 'npm' and not explicit_run and (scripts is None or name not in scripts) and base(name) in KNOWN_BINS:
        return None  # `pnpm vitest ...` / `yarn jest ...` = binary
    return w0, name, extra, directory if resolvable else None, scripts is not None and name in scripts


# ---------------------------------------------------------------- main rule

def check(cmd, in_sem_outer=False, depth=0, cwd=None):
    """Block message, or None if the command passes."""
    hit = check_rule(cmd, in_sem_outer, depth, cwd)
    return hit[1] if hit else None


def check_rule(cmd, in_sem_outer=False, depth=0, cwd=None, env=None):
    """(rule, message) of the first block, or None if the command passes."""
    env = dict(env or {})
    try:
        tokens = tokenize(cmd)
    except ValueError:
        # unbalanced quotes: conservative regex mode
        if re.search(r'(^|[\s;&|(])(npx\s+|pnpm\s+(exec\s+)?)?jest(\s|$)', cmd) and \
                not re.search(r'--maxWorkers|--runInBand|(^|\s)-i(\s|$)', cmd):
            return 'jest-no-workers', f'Jest needs --maxWorkers=N ({limit_text("jest", False)}).'
        return None
    for seg in segments(tokens):
        in_sem = in_sem_outer
        if base(seg[0]) == 'export':
            for a in seg[1:]:
                m = ASSIGN_RE.match(a)
                if m:
                    env[m.group(1)] = m.group(2)
            continue
        if base(seg[0]) == 'cd':
            cwd = resolve_dir(cwd, seg[1] if len(seg) > 1 else '~')
            continue
        seg_env = dict(env)
        while seg:
            w = base(seg[0])
            m = ASSIGN_RE.match(seg[0])
            if m:
                seg_env[m.group(1)] = m.group(2)
                seg = seg[1:]
            elif w in SEMAPHORE_NAMES:
                in_sem = True
                seg = seg[1:]
            elif w in PREFIX_WORDS:
                seg = seg[1:]
                while seg and seg[0].startswith('-'):
                    seg = seg[1:]
            else:
                break
        if not seg:
            continue
        if base(seg[0]) in SHELLS and '-c' in seg and depth < 3:
            i = seg.index('-c')
            if i + 1 < len(seg):
                hit = check_rule(seg[i + 1], in_sem, depth + 1, cwd, seg_env)
                if hit:
                    return hit
            continue
        script = heavy_script(seg)
        if script and not in_sem:
            return 'heavy-script-outside-semaphore', (f'`{script}` runs a heavy validation (suite/tsc/lint): only through '
                                                      f"'{SEM} <command>' (e.g. {SEM} pnpm run {script}).")
        call = script_call(seg, cwd)
        if call:
            runner, name, extra, directory, resolved = call
            if resolved and depth < 3:
                scripts = read_scripts(directory)
                bodies = [scripts[name] + ''.join(' ' + shlex.quote(a) for a in extra)]
                if runner == 'npm':
                    bodies = [scripts[p] for p in ('pre' + name,) if p in scripts] + bodies + \
                             [scripts[p] for p in ('post' + name,) if p in scripts]
                for body in bodies:
                    hit = check_rule(body, in_sem, depth + 1, directory, seg_env)
                    if hit:
                        return hit
                continue
            if not resolved and TEST_SCRIPT_RE.match(name) and not in_sem:
                where = 'package.json not found' if directory and read_scripts(directory) is None else \
                    'script missing from package.json' if directory else 'package unknown (--filter/-r/cwd)'
                return 'unresolved-script-outside-semaphore', (
                    f'`{runner} {name}` could not be resolved ({where}): a test script with no known runner only '
                    f"through '{SEM} <command>', or call the runner directly within the limits.")
            continue
        found = unwrap(seg, cwd)
        if not found:
            continue
        runner, args, run_cwd = found
        if runner == 'tsc':
            if not in_sem and not any(a in TSC_HARMLESS for a in args) and tsc_is_project(args):
                return 'tsc-project-outside-semaphore', (f"Project-wide tsc only through '{SEM} <command>' (bounded heavy "
                                                         'validation). tsc on a single file is fine.')
            continue
        via_script = depth > 0
        hit = (rule_jest(args, in_sem, via_script, run_cwd) if runner == 'jest' else
               rule_vitest(args, in_sem, run_cwd) if runner == 'vitest' else
               rule_pytest(args, in_sem, run_cwd) if runner == 'pytest' else
               rule_playwright(args, in_sem, seg_env, run_cwd) if runner == 'playwright' else None)
        if hit:
            return hit
    return None


# ---------------------------------------------------------------- hook

def clean(text, limit=None):
    text = ' '.join(str(text).split())  # tabs, newlines and repeated spaces become one space
    for rx, rep in REDACT:
        text = rx.sub(rep, text)
    return text[:limit] if limit else text


def log_block(cwd, rule, cmd):
    if not CFG['guard']['log_blocks']:
        return
    try:
        os.makedirs(os.path.dirname(BLOCKS_LOG), exist_ok=True)
        new = not os.path.exists(BLOCKS_LOG) or os.path.getsize(BLOCKS_LOG) == 0
        line = '\t'.join([datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), clean(cwd), rule, clean(cmd, 120)])
        with open(BLOCKS_LOG, 'a', encoding='utf-8') as fh:
            fh.write((BLOCKS_HEADER if new else '') + line + '\n')
    except Exception:
        pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == '--check':  # manual check: throttle-guard --check '<command>' [--cwd DIR]
        if len(argv) < 2:
            print("usage: throttle-guard --check '<command>' [--cwd DIR]", file=sys.stderr)
            return 64
        cwd = argv[3] if len(argv) > 3 and argv[2] == '--cwd' else os.getcwd()
        hit = check_rule(argv[1], cwd=cwd)
        print(f'blocked ({hit[0]}): {hit[1]}' if hit else 'ok')
        return 2 if hit else 0
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    cmd = (data.get('tool_input') or {}).get('command') or ''
    cwd = data.get('cwd') or os.getcwd()
    hit = check_rule(cmd, cwd=cwd)
    if hit:
        rule, msg = hit
        log_block(cwd, rule, cmd)
        print(f'Blocked by throttle-guard ({rule}): {msg}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
