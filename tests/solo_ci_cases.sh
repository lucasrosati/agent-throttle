#!/usr/bin/env bash
# shellcheck disable=SC2016,SC2034 # checks are strings passed to eval: variables expand there
# Cases for bin/solo-ci that need no GitHub: every refusal happens before any push. Usage: bash tests/solo_ci_cases.sh
set -u
here=$(cd "$(dirname "$0")" && pwd)
CI=$here/../bin/solo-ci
T=$(mktemp -d "${TMPDIR:-/tmp}/solo-ci-cases.XXXXXX"); trap 'rm -rf "$T"' EXIT
export AGENT_THROTTLE_CONFIG=$T/none.toml XDG_STATE_HOME=$T/state
mkdir "$T/stub" && printf '#!/bin/sh\nexit 1\n' > "$T/stub/gh" && chmod +x "$T/stub/gh"
export PATH=$T/stub:$PATH   # a gh that is never reached: every case stops before talking to GitHub
fails=0; n=0
ok() { n=$((n + 1)); if eval "$2"; then echo "ok   $1"; else echo "FAILED $1"; fails=$((fails + 1)); fi; }

cd "$T" || exit 2
"$CI" > o1 2>&1; rc=$?
ok 'outside a git repository: exit 2' '[ $rc = 2 ] && grep -q "not inside a git repository" o1'

git init -q -b main repo && cd repo || exit 2
git -c user.name=t -c user.email=t@example.com commit -q --allow-empty -m init
"$CI" > ../o2 2>&1; rc=$?
ok 'on main: refused with exit 2' '[ $rc = 2 ] && grep -q "refused: I do not push main" ../o2'

git checkout -q -b feature && echo a > f && git add f && git -c user.name=t -c user.email=t@example.com commit -qm f
echo b > f
"$CI" > ../o3 2>&1; rc=$?
ok 'uncommitted tracked change: exit 2' '[ $rc = 2 ] && grep -q "not committed" ../o3'

git checkout -q f && git checkout -q --detach
"$CI" > ../o4 2>&1; rc=$?
ok 'detached HEAD: exit 2' '[ $rc = 2 ] && grep -q "detached HEAD" ../o4'

git checkout -q feature
"$CI" > ../o5 2>&1; rc=$?
ok 'no remote: push rejected, exit 5, nothing forced' '[ $rc = 5 ] && grep -q "nothing was forced" ../o5'
ok 'nothing written to the log before CI ran' '[ ! -e "$T/state/agent-throttle/semaphore.log" ]'

echo "$((n - fails))/$n cases"
exit $((fails > 0))
