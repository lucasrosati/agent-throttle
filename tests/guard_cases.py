#!/usr/bin/env python3
"""Test battery for lib/agent_throttle/guard.py. Usage: python3 tests/guard_cases.py (rc=0 = every case passed).
Mutant: GUARD_PATH=<modified copy> python3 tests/guard_cases.py (must fail; tests/mutants.sh does it).

Each case: (command, package, expected). package = key of FIXTURES/TREES (a fake repository created in a temporary
directory) or None (unknown cwd). expected = 'ok' or the NAME OF THE RULE that must block.
The LEGACY block keeps the first generation of cases with 'ok'/'block' only.
The config is forced to the defaults (AGENT_THROTTLE_CONFIG points to a file that does not exist).
"""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT_DIR = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix='guard-cases-')
os.environ['AGENT_THROTTLE_CONFIG'] = os.path.join(TMP, 'no-config.toml')
os.environ['XDG_STATE_HOME'] = os.path.join(TMP, 'state')
sys.path.insert(0, os.path.join(ROOT_DIR, 'lib'))
spec = importlib.util.spec_from_file_location(
    'guard', os.environ.get('GUARD_PATH') or os.path.join(ROOT_DIR, 'lib', 'agent_throttle', 'guard.py'))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

FIXTURES = {  # package.json scripts of invented repositories, one per pattern seen in real projects
    'node-test-only': {'test': 'npm run build && node --test tests/rendered-html.test.mjs', 'build': 'next build'},
    'jest-chain': {'test': 'jest && npm run test:config && npm run test:node',
                   'test:config': 'node --test config/*.test.cjs', 'test:node': 'node --test test/*.test.cjs'},
    'vitest-projects': {'test': 'vitest run --project unit', 'test:watch': 'vitest --project unit',
                        'test:db': 'vitest run --project db', 'test:e2e': 'playwright test', 'typecheck': 'tsc --noEmit'},
    'vitest-pw-crossenv': {
        'test': 'pnpm run test:int && pnpm run test:e2e',
        'test:int': 'cross-env NODE_OPTIONS=--no-deprecation vitest run --config ./vitest.config.mts',
        'test:e2e': 'cross-env NODE_OPTIONS="--no-deprecation --import=tsx/esm" playwright test --config=playwright.config.ts'},
    'web-jest': {'test': 'jest', 'typecheck': 'tsc --noEmit -p tsconfig.json',
                 'validate:local': 'prettier --check "src/**/*.{ts,tsx}" && pnpm run typecheck && jest --ci --maxWorkers=4 --silent'},
    'npm-pre': {'pretest': 'jest', 'test': 'node --test'},
    'check-chain': {'check': 'pnpm lint && pnpm typecheck && pnpm test', 'lint': 'biome lint', 'typecheck': 'tsc --noEmit',
                    'test': 'vitest run'},
    'no-scripts': {},
}
VITEST_PROJECTS = """import { defineConfig } from "vitest/config";
export default defineConfig({
  resolve: { tsconfigPaths: true },
  test: {
    projects: [
      { test: { name: "unit", environment: "node", include: ["src/**/*.test.ts", "src/**/*.test.tsx"] } },
      { test: { name: "db", include: ["supabase/tests/**/*.test.ts"], globalSetup: ["./supabase/tests/global-setup.ts"] } },
    ],
  },
});
"""
VITE_APP = """export default defineConfig(() => ({
  optimizeDeps: { include: ["react", "react-dom/client"] },  // not a test include
  test: {
    environment: "jsdom",
    include: ['./tests/**/*.{test,spec}.ts'],
    coverage: { provider: "v8", include: ["src/**"] },  // not a test include
  },
}));
"""
TREES = {  # test configs: the path that IS the suite (testpaths / include / root)
    'py-backend': {'pyproject.toml': '[project]\nname = "app"\n\n[tool.pytest.ini_options]\naddopts = "-q"\n'
                                     'testpaths = ["backend/tests"]\n',
                   'backend/tests/test_b.py': '', 'backend/tests/unit/test_a.py': '', 'backend/app/__init__.py': ''},
    'py-monorepo': {'apps/api/pyproject.toml': '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
                    'apps/api/tests/test_a.py': '', 'apps/web/package.json': '{}'},
    'py-ini': {'pytest.ini': '[pytest]\ntestpaths = tests integration\n', 'tests/test_a.py': '',
               'integration/test_i.py': '', 'docs/test_doc.py': ''},
    'py-glob': {'pyproject.toml': '[tool.pytest.ini_options]\ntestpaths = ["packages/*/tests"]\n',
                'packages/a/tests/test_x.py': '', 'packages/b/tests/test_y.py': ''},
    'py-no-cfg': {'tests/test_a.py': ''},
    'vitest-projects': {'vitest.config.ts': VITEST_PROJECTS, 'src/lib/a.test.ts': '', 'supabase/tests/b.test.ts': ''},
    'vitest-pw-crossenv': {'vitest.config.mts': "export default defineConfig({ test: { include: ['tests/int/**/*.int.spec.ts'] } })\n",
                           'tests/int/a.int.spec.ts': '', 'tests/e2e/admin.e2e.spec.ts': ''},
    'vite-app': {'vite.config.ts': VITE_APP, 'src/a.ts': '', 'tests/a.test.ts': ''},
    'vitest-default': {'vitest.config.ts': 'export default defineConfig({ test: { environment: "node" } })\n',
                       'src/a.test.ts': ''},
    # Jest: rootDir in package.json, testMatch with a prefix, testMatch with ** and roots
    'jest-api': {'package.json': json.dumps({'name': 'api', 'scripts': {'test': 'jest', 'test:e2e': 'jest --config ./test/jest-e2e.json'},
                                             'jest': {'rootDir': 'src', 'testRegex': '.*\\.spec\\.ts$'}}),
                 'test/jest-e2e.json': json.dumps({'rootDir': '.', 'testRegex': '.e2e-spec.ts$'}),
                 'src/orders/a.spec.ts': '', 'test/app.e2e-spec.ts': ''},
    'web-jest': {'jest.config.js': "module.exports = {\n  moduleNameMapper: { '^@/(.*)$': '<rootDir>/src/$1' },\n"
                                   "  testMatch: [\n    '<rootDir>/src/**/*.test.{ts,tsx}',\n    '<rootDir>/packages/**/*.test.ts',\n  ],\n}\n",
                 'src/components/a.test.tsx': '', 'packages/contracts/b.test.ts': ''},
    'jest-app': {'jest.config.js': "// the `testMatch` below takes both layouts\nmodule.exports = { preset: 'jest-expo',\n"
                                   "  testMatch: ['**/__tests__/**/*.test.ts?(x)', '**/?(*.)+(test).ts?(x)'] }\n",
                 'src/a.test.ts': ''},
    'jest-roots': {'jest.config.ts': "export default { roots: ['<rootDir>/tests'] }\n", 'tests/unit/a.test.ts': ''},
    # Playwright: literal testDir and path.join(__dirname, ...)
    'pw-dirname': {'playwright.config.ts': "import path from 'path'\nexport default { testDir: path.join(__dirname, 'specs') }\n",
                   'specs/a.spec.ts': ''},
}
TREES['vitest-pw-crossenv']['playwright.config.ts'] = \
    "export default defineConfig({\n  testDir: './tests/e2e',\n  reporter: [['html', { open: 'never' }]],\n})\n"
TREES['vitest-projects'].update({'playwright.config.ts': 'export default defineConfig({ testDir: "./e2e", workers: 1 })\n',
                                 'e2e/login.spec.ts': ''})
ROOT = os.path.join(TMP, 'repos')
for name, scripts in FIXTURES.items():
    os.makedirs(os.path.join(ROOT, name, 'sub'))
    with open(os.path.join(ROOT, name, 'package.json'), 'w') as fh:
        json.dump({'name': name, 'scripts': scripts}, fh)
os.makedirs(os.path.join(ROOT, 'no-package'))
for name, files in TREES.items():
    for rel, content in files.items():
        path = os.path.join(ROOT, name, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as fh:
            fh.write(content)

SOLO_FORMS = ['solo', '~/.local/bin/solo', '$HOME/.local/bin/solo', '/opt/tools/bin/solo']
LEGACY = []
for v in SOLO_FORMS:
    LEGACY += [
        (f'{v} pnpm run validate:local', 'ok'), (f'{v} pnpm run typecheck', 'ok'),
        (f'{v} npx jest --ci --maxWorkers=4 --silent', 'ok'), (f'{v} npx jest --ci --maxWorkers=5', 'block'),
        (f'{v} npx jest --ci', 'block'), (f'{v} npx tsc --noEmit -p tsconfig.json', 'ok'),
        (f'cd web && {v} pnpm run validate:local 2>&1 | tail -20', 'ok'),
        (f'{v} npx jest --ci --maxWorkers=2 --silent --workerIdleMemoryLimit=1GB', 'ok'),
    ]
LEGACY += [
    ('npx jest --ci', 'block'), ('true || npx jest --ci', 'block'), ('npx jest --ci --maxWorkers=2', 'block'),
    ('npx jest src/a.spec.ts --maxWorkers=2 --silent', 'ok'), ('npx jest src/a.spec.ts --maxWorkers=3', 'block'),
    ('npx jest --changedSince=origin/main --maxWorkers=2', 'ok'), ('pnpm run validate:local', 'block'),
    ('npm run typecheck', 'block'), ('npx tsc --noEmit -p tsconfig.json', 'block'), ('npx tsc --noEmit src/a.ts', 'ok'),
    ('pnpm test', 'block'), ('bash -c "npx jest --ci"', 'block'), ("zsh -c 'solo npx jest --ci --maxWorkers=4'", 'ok'),
    ('git commit -m "runs jest --ci"', 'ok'), ('grep -rn jest package.json', 'ok'),
    ('solo-ci npx jest --ci', 'ok'), ('solo-ci; npx jest --ci', 'block'), ('~/.local/bin/solo-ci && npx jest --ci', 'block'),
    ('echo solo; npx jest --ci', 'block'), ('solo true; npx jest --ci', 'block'),
]

CASES = [
    # ---- a redirection is not a "target"
    ('npx jest --ci --maxWorkers=2 2>&1 | tail -3', None, 'jest-suite-outside-semaphore'),
    ('npx jest --ci --maxWorkers=2 >/tmp/x.log 2>&1', None, 'jest-suite-outside-semaphore'),
    ('npx jest src/a.spec.ts --maxWorkers=2 2>&1 | tail -3', None, 'ok'),
    ("sed -i '' 's/<Radio> = (x) => {/y/' f.ts && pnpm typecheck 2>&1 | tail -3", None, 'heavy-script-outside-semaphore'),
    ('echo "a > b < c" && npx jest --ci', None, 'jest-no-workers'),                     # < and > inside quotes
    # ---- watch: always blocked, even inside solo
    ('npx jest --watch --maxWorkers=2 src/a.spec.ts', None, 'watch-blocked'),
    ('solo npx jest --watchAll --maxWorkers=4', None, 'watch-blocked'),
    ('npx vitest', None, 'watch-blocked'),
    ('npx vitest src/a.test.ts --maxWorkers=2', None, 'watch-blocked'),
    ('solo npx vitest watch --maxWorkers=4', None, 'watch-blocked'),
    ('npx vitest dev', None, 'watch-blocked'),
    ('npx vitest run -w --maxWorkers=2 src/a.test.ts', None, 'watch-blocked'),
    ('pnpm test:watch', 'vitest-projects', 'watch-blocked'),
    ('solo pnpm run test:watch', 'vitest-projects', 'watch-blocked'),
    # ---- Vitest: literal workers (even with an explicit file), ceiling 4/2, full suite only inside solo
    ('npx vitest run src/a.test.ts', None, 'vitest-no-workers'),
    ('npx vitest run src/a.test.ts --maxWorkers=2', None, 'ok'),
    ('npx vitest run src/a.test.ts --maxWorkers 2', None, 'ok'),
    ('npx vitest --run src/a.test.ts --maxWorkers=2', None, 'ok'),
    ('pnpm exec vitest run test/db test/ledger --maxWorkers=2', None, 'ok'),
    ('TEST_DATABASE_URL=postgresql://test pnpm exec vitest run test/db --maxWorkers=2 2>&1 | grep Tests', None, 'ok'),
    ('pnpm vitest run src/a.test.ts --maxWorkers=2', None, 'ok'),
    ('./node_modules/.bin/vitest run src/a.test.ts --maxWorkers=2', None, 'ok'),
    ('node node_modules/vitest/vitest.mjs run src/a.test.ts', None, 'vitest-no-workers'),
    ('npx vitest run src/a.test.ts --no-file-parallelism', None, 'ok'),
    ('npx vitest run src/a.test.ts --maxWorkers=3', None, 'vitest-over-limit'),
    ('npx vitest run src/a.test.ts --maxWorkers=50%', None, 'vitest-workers-not-literal'),
    ('npx vitest run src/a.test.ts --maxWorkers=$W', None, 'vitest-workers-not-literal'),
    ('npx vitest run --maxWorkers=2', None, 'vitest-suite-outside-semaphore'),
    ('npx vitest run --project unit --maxWorkers=2', None, 'vitest-suite-outside-semaphore'),
    ('npx vitest run -t "saves draft" --maxWorkers=2', None, 'vitest-suite-outside-semaphore'),
    ('npx vitest run --changed --maxWorkers=2', None, 'ok'),
    ('npx vitest related src/lib/a.ts --run --maxWorkers=2', None, 'ok'),
    ('solo npx vitest run --maxWorkers=4', None, 'ok'),
    ('solo npx vitest run --maxWorkers=5', None, 'vitest-over-limit'),
    ('solo npx vitest run', None, 'vitest-no-workers'),
    ('npx vitest --version', None, 'ok'),
    ('npx vitest list', None, 'ok'),
    # ---- scripts resolved from package.json
    ('npm test', 'node-test-only', 'ok'),                                   # build + node --test: not Jest
    ('solo npm test', 'node-test-only', 'ok'),
    ('npm test', 'jest-chain', 'jest-no-workers'),                          # bare jest inside the script
    ('npm test', 'npm-pre', 'jest-no-workers'),                             # npm's pretest also runs
    ('pnpm test', 'vitest-projects', 'vitest-no-workers'),
    ('pnpm test -- --maxWorkers=2', 'vitest-projects', 'vitest-suite-outside-semaphore'),
    ('solo pnpm test -- --maxWorkers=4', 'vitest-projects', 'ok'),
    ('solo pnpm test --maxWorkers=4', 'vitest-projects', 'ok'),
    ('pnpm test src/lib/a.test.ts --maxWorkers=2', 'vitest-projects', 'ok'),
    ('npm test -- src/lib/a.test.ts --maxWorkers=2', 'vitest-projects', 'ok'),
    ('npm test src/lib/a.test.ts --maxWorkers=2', 'vitest-projects', 'vitest-no-workers'),  # npm passes args only after --
    ('solo pnpm test:db -- --maxWorkers=4', 'vitest-projects', 'ok'),
    ('cd sub && cd .. && pnpm test', 'vitest-projects', 'vitest-no-workers'),  # cd is followed
    ('pnpm --dir .. test', 'no-package', 'unresolved-script-outside-semaphore'),
    ('solo pnpm test:int -- --maxWorkers=4', 'vitest-pw-crossenv', 'ok'),
    ('pnpm test:int -- src/x.test.ts --maxWorkers=2', 'vitest-pw-crossenv', 'ok'),
    ('solo pnpm test', 'vitest-pw-crossenv', 'vitest-no-workers'),
    ('pnpm check', 'check-chain', 'heavy-script-outside-semaphore'),       # typecheck inside check
    ('solo pnpm check', 'check-chain', 'vitest-no-workers'),
    ('solo pnpm run validate:local', 'web-jest', 'ok'),
    ('pnpm test', 'no-scripts', 'unresolved-script-outside-semaphore'),
    ('solo pnpm test', 'no-scripts', 'ok'),
    ('npm test', 'no-package', 'unresolved-script-outside-semaphore'),
    ('pnpm --filter web test', 'vitest-projects', 'unresolved-script-outside-semaphore'),
    ('solo pnpm --filter web test', 'vitest-projects', 'ok'),
    ('pnpm -r test', 'vitest-projects', 'unresolved-script-outside-semaphore'),
    ('pnpm build', 'node-test-only', 'ok'),
    ('pnpm install', 'vitest-projects', 'ok'),
    ('npm ci', 'vitest-projects', 'ok'),
    # ---- pytest
    ('pytest', None, 'pytest-suite-outside-semaphore'),
    ('python3 -m pytest 2>&1 | tail -1', None, 'pytest-suite-outside-semaphore'),
    ('/opt/app/.venv/bin/python -m pytest -rs -o addopts= -q', None, 'pytest-suite-outside-semaphore'),
    ('solo /opt/app/.venv/bin/python -m pytest -rs -o addopts= -q', None, 'ok'),
    ('APP_TEST_DATABASE_URL=postgresql://app_test .venv/bin/python -m pytest tests/test_a.py -q', None, 'ok'),
    ('.venv/bin/pytest tests/test_a.py::test_one -q', None, 'ok'),
    ('"$VENV/pytest" backend/api backend/core -q -p no:cacheprovider', None, 'ok'),
    ('pytest -p no:cacheprovider -q', None, 'pytest-suite-outside-semaphore'),
    ('pytest -k save -q', None, 'pytest-suite-outside-semaphore'),
    ('pytest -m "not slow"', None, 'pytest-suite-outside-semaphore'),
    ('pytest --lf -q', None, 'ok'),
    ('pytest --last-failed', None, 'ok'),
    ('pytest --collect-only -q', None, 'ok'),
    ('uv run --directory apps/api pytest --collect-only -q 2>&1 | tail -3', None, 'ok'),
    ('uv run --directory apps/api pytest -q --no-header', None, 'pytest-suite-outside-semaphore'),
    ('CI_REQUIRE_SERVICES=true uv run --env-file ../../.env --directory apps/api pytest tests/test_a.py -q', None, 'ok'),
    ('solo uv run --directory apps/api pytest -q', None, 'ok'),
    ('poetry run pytest tests/', None, 'ok'),
    ('py.test', None, 'pytest-suite-outside-semaphore'),
    ('pytest tests/ -n 2', None, 'ok'),
    ('pytest tests/ -n 3', None, 'pytest-over-limit'),
    ('solo pytest -n 4', None, 'ok'),
    ('solo pytest -n 5', None, 'pytest-over-limit'),
    ('pytest tests/ -n auto', None, 'pytest-workers-not-literal'),
    ('solo pytest -n logical', None, 'pytest-workers-not-literal'),
    ('pytest tests/ -n2', None, 'ok'),
    ('pytest tests/ --numprocesses=auto', None, 'pytest-workers-not-literal'),
    ('python3 -c "import pytest"', None, 'ok'),
    # ---- Playwright
    ('npx playwright test', None, 'playwright-no-workers'),
    ('npx playwright test e2e/login.spec.ts', None, 'playwright-no-workers'),
    ('npx playwright test e2e/login.spec.ts --workers=1', None, 'ok'),
    ('npx playwright test e2e/login.spec.ts:12 -j 1', None, 'ok'),
    ('npx playwright test e2e/login.spec.ts --workers=2', None, 'playwright-over-limit'),
    ('npx playwright test --workers=1', None, 'playwright-suite-outside-semaphore'),
    ('npx playwright test --grep login --workers=1', None, 'playwright-suite-outside-semaphore'),
    ('npx playwright test --last-failed --workers=1', None, 'ok'),
    ('npx playwright test --only-changed=origin/main --workers=1', None, 'ok'),
    ('solo npx playwright test --workers=2', None, 'ok'),
    ('solo npx playwright test --workers=3', None, 'playwright-over-limit'),
    ('solo npx playwright test --workers=50%', None, 'playwright-workers-not-literal'),
    ('solo npx playwright test', None, 'playwright-no-workers'),
    ('solo npx playwright test --ui', None, 'playwright-interactive'),
    ('npx playwright test e2e/a.spec.ts --workers=1 --debug', None, 'playwright-interactive'),
    ('PWDEBUG=1 npx playwright test e2e/a.spec.ts --workers=1', None, 'playwright-interactive'),
    ('export PWDEBUG=console; solo npx playwright test --workers=1', None, 'playwright-interactive'),
    ('PWTEST_WATCH=1 npx playwright test e2e/a.spec.ts --workers=1', None, 'watch-blocked'),
    ('npx playwright show-report', None, 'playwright-interactive'),
    ('npx playwright codegen http://localhost:3000', None, 'playwright-interactive'),
    ('npx playwright install chromium', None, 'ok'),
    ('npx playwright test --list', None, 'ok'),
    ('solo pnpm test:e2e -- --workers=2', 'vitest-projects', 'ok'),
    ('pnpm test:e2e', 'vitest-projects', 'playwright-no-workers'),
    ('solo pnpm test:e2e -- --workers=2', 'vitest-pw-crossenv', 'ok'),     # cross-env + quotes in the script
    ('pnpm test:e2e -- tests/e2e/admin.e2e.spec.ts --workers=1', 'vitest-pw-crossenv', 'ok'),
    # ---- a path that IS the suite: pytest testpaths, Vitest root/include, or an ancestor of them
    ('pytest backend/tests -q', 'py-backend', 'pytest-suite-outside-semaphore'),           # = testpaths (pyproject)
    ('solo pytest backend/tests -q', 'py-backend', 'ok'),
    ('pytest backend/tests/ -q', 'py-backend', 'pytest-suite-outside-semaphore'),
    ('pytest ./backend/tests -p no:cacheprovider', 'py-backend', 'pytest-suite-outside-semaphore'),
    ('pytest backend', 'py-backend', 'pytest-suite-outside-semaphore'),                    # ancestor of testpaths
    ('pytest .', 'py-backend', 'pytest-suite-outside-semaphore'),                          # rootdir
    ('.venv/bin/python -m pytest backend/tests -n 2 2>&1 | tail -3', 'py-backend', 'pytest-suite-outside-semaphore'),
    ('pytest backend/app backend/tests', 'py-backend', 'pytest-suite-outside-semaphore'),  # one of the paths is the suite
    ('cd backend && pytest tests', 'py-backend', 'pytest-suite-outside-semaphore'),        # config found walking up
    ('pytest -c pyproject.toml backend/tests', 'py-backend', 'pytest-suite-outside-semaphore'),
    ('pytest backend/tests/unit -q', 'py-backend', 'ok'),                                   # subfolder = target
    ('cd backend && pytest tests/unit', 'py-backend', 'ok'),
    ('pytest backend/tests/test_b.py -q', 'py-backend', 'ok'),
    ('pytest backend/tests/test_b.py::test_one', 'py-backend', 'ok'),
    ('pytest backend/tests --lf', 'py-backend', 'ok'),
    ('pytest "$DIR/backend/tests"', 'py-backend', 'ok'),                                    # $VAR: any path is a target
    ('uv run --directory apps/api pytest tests -q', 'py-monorepo', 'pytest-suite-outside-semaphore'),
    ('solo uv run --directory apps/api pytest tests -q', 'py-monorepo', 'ok'),
    ('uv run --directory apps/api pytest tests/test_a.py -q', 'py-monorepo', 'ok'),
    ('cd apps/api && uv run pytest tests', 'py-monorepo', 'pytest-suite-outside-semaphore'),
    ('pytest integration', 'py-ini', 'pytest-suite-outside-semaphore'),                    # pytest.ini, 2nd testpaths
    ('pytest docs', 'py-ini', 'ok'),
    ('pytest packages/a/tests', 'py-glob', 'pytest-suite-outside-semaphore'),              # testpaths with a glob
    ('pytest packages/a/tests/test_x.py', 'py-glob', 'ok'),
    ('pytest .', 'py-no-cfg', 'pytest-suite-outside-semaphore'),                           # no config: root = cwd
    ('pytest tests', 'py-no-cfg', 'ok'),
    ('npx vitest run . --maxWorkers=2', 'vitest-projects', 'vitest-suite-outside-semaphore'),     # root
    ('solo npx vitest run . --maxWorkers=4', 'vitest-projects', 'ok'),
    ('npx vitest run ./ --maxWorkers=2', 'vitest-projects', 'vitest-suite-outside-semaphore'),
    ('npx vitest run src --maxWorkers=2', 'vitest-projects', 'vitest-suite-outside-semaphore'),   # include prefix (unit)
    ('npx vitest run supabase --maxWorkers=2', 'vitest-projects', 'vitest-suite-outside-semaphore'),  # ancestor (db)
    ('npx vitest run src/lib --maxWorkers=2', 'vitest-projects', 'ok'),
    ('pnpm test src --maxWorkers=2', 'vitest-projects', 'vitest-suite-outside-semaphore'),        # through the script
    ('solo pnpm test src --maxWorkers=4', 'vitest-projects', 'ok'),
    ('pnpm test:int -- tests/int --maxWorkers=2', 'vitest-pw-crossenv', 'vitest-suite-outside-semaphore'),
    ('pnpm test:int -- tests --maxWorkers=2', 'vitest-pw-crossenv', 'vitest-suite-outside-semaphore'),
    ('npx vitest run --config ./vitest.config.mts tests/int --maxWorkers=2', 'vitest-pw-crossenv',
     'vitest-suite-outside-semaphore'),
    ('npx vitest run tests --maxWorkers=2', 'vite-app', 'vitest-suite-outside-semaphore'),
    ('npx vitest run src --maxWorkers=2', 'vite-app', 'ok'),                                    # coverage.include
    ('npx vitest run react --maxWorkers=2', 'vite-app', 'ok'),                                  # optimizeDeps.include
    ('npx vitest run . --maxWorkers=2', 'vitest-default', 'vitest-suite-outside-semaphore'),    # default include = root
    ('npx vitest run src --maxWorkers=2', 'vitest-default', 'ok'),
    # ---- Jest: rootDir, roots, testMatch prefix, --testPathPattern and --config
    ('npx jest src --maxWorkers=2', 'jest-api', 'jest-suite-outside-semaphore'),                  # rootDir in package.json
    ('npx jest src/ --maxWorkers=2', 'jest-api', 'jest-suite-outside-semaphore'),
    ('npx jest . --maxWorkers=2', 'jest-api', 'jest-suite-outside-semaphore'),
    ('solo npx jest src --maxWorkers=4', 'jest-api', 'ok'),
    ('npx jest --testPathPattern=src --maxWorkers=2', 'jest-api', 'jest-suite-outside-semaphore'),
    ('npx jest --testPathPattern src/orders --maxWorkers=2', 'jest-api', 'ok'),
    ('npx jest src/orders --maxWorkers=2', 'jest-api', 'ok'),
    ('npx jest src/orders/a.spec.ts --maxWorkers=2', 'jest-api', 'ok'),
    ('npx jest orders --maxWorkers=2', 'jest-api', 'ok'),                                          # pattern, not folder
    ('cd src && npx jest . --maxWorkers=2', 'jest-api', 'jest-suite-outside-semaphore'),          # config found walking up
    ('cd src && npx jest orders --maxWorkers=2', 'jest-api', 'ok'),
    ('npx jest src --changedSince=origin/main --maxWorkers=2', 'jest-api', 'ok'),                 # strong target
    ('npm test -- src --maxWorkers=2', 'jest-api', 'jest-suite-outside-semaphore'),
    ('npx jest --config ./test/jest-e2e.json test --maxWorkers=2', 'jest-api', 'jest-suite-outside-semaphore'),
    ('pnpm test:e2e test/app.e2e-spec.ts --maxWorkers=2', 'jest-api', 'ok'),
    ('pnpm test:e2e test --maxWorkers=2', 'jest-api', 'jest-suite-outside-semaphore'),
    ('npx jest src --maxWorkers=2', 'web-jest', 'jest-suite-outside-semaphore'),                  # testMatch prefix
    ('pnpm test packages --maxWorkers=2', 'web-jest', 'jest-suite-outside-semaphore'),
    ('npx jest src/components --maxWorkers=2', 'web-jest', 'ok'),
    ('npx jest src --maxWorkers=2', 'jest-app', 'ok'),                                             # testMatch ** = root
    ('npx jest . --maxWorkers=2', 'jest-app', 'jest-suite-outside-semaphore'),
    ('npx jest tests --maxWorkers=2', 'jest-roots', 'jest-suite-outside-semaphore'),              # roots
    ('npx jest tests/unit --maxWorkers=2', 'jest-roots', 'ok'),
    # ---- Playwright: config directory and testDir
    ('npx playwright test tests/e2e --workers=1', 'vitest-pw-crossenv', 'playwright-suite-outside-semaphore'),
    ('npx playwright test tests --workers=1', 'vitest-pw-crossenv', 'playwright-suite-outside-semaphore'),  # ancestor
    ('npx playwright test tests/e2e/admin.e2e.spec.ts --workers=1', 'vitest-pw-crossenv', 'ok'),
    ('npx playwright test tests/e2e/admin.e2e.spec.ts:12 --workers=1', 'vitest-pw-crossenv', 'ok'),
    ('pnpm test:e2e -- tests/e2e --workers=1', 'vitest-pw-crossenv', 'playwright-suite-outside-semaphore'),  # --config=
    ('solo pnpm test:e2e -- tests/e2e --workers=2', 'vitest-pw-crossenv', 'ok'),
    ('npx playwright test e2e --workers=1', 'vitest-projects', 'playwright-suite-outside-semaphore'),
    ('npx playwright test . --workers=1', 'vitest-projects', 'playwright-suite-outside-semaphore'),       # config dir
    ('npx playwright test login --workers=1', 'vitest-projects', 'ok'),
    ('npx playwright test specs --workers=1', 'pw-dirname', 'playwright-suite-outside-semaphore'),        # __dirname
    ('npx playwright test specs/a.spec.ts --workers=1', 'pw-dirname', 'ok'),
    # ---- pnpm with -C/--dir before exec
    ('pnpm -C sub exec vitest run', 'vitest-projects', 'vitest-no-workers'),
    ('pnpm --dir web exec jest --ci', None, 'jest-no-workers'),
    ('cd sub && pnpm -C .. exec vitest run src --maxWorkers=2', 'vitest-projects', 'vitest-suite-outside-semaphore'),
    ('pnpm --filter web exec vitest run src --maxWorkers=2', 'vitest-projects', 'ok'),  # uncertain package: any path
    # ---- only mentions a runner
    ('git commit -m "vitest run without workers and pytest -n auto"', None, 'ok'),
    ('echo "npx playwright test --ui"', None, 'ok'),
    ('cat > notes.md <<EOF\nnpx vitest\npytest\nEOF', None, 'ok'),
]

fails = 0
for cmd, want in LEGACY:
    got = 'block' if guard.check(cmd) else 'ok'
    if got != want:
        fails += 1
        print(f'FAILED (legacy): expected {want}, got {got}: {cmd}')
for cmd, pkg, want in CASES:
    hit = guard.check_rule(cmd, cwd=os.path.join(ROOT, pkg) if pkg else None)
    got = hit[0] if hit else 'ok'
    if got != want:
        fails += 1
        print(f'FAILED: expected {want}, got {got}: [{pkg}] {cmd}')
total = len(LEGACY) + len(CASES)
print(f'{total - fails}/{total} cases ({len(LEGACY)} legacy + {len(CASES)} rule cases)')
sys.exit(1 if fails else 0)
