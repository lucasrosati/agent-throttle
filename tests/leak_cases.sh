#!/usr/bin/env bash
# shellcheck disable=SC2016,SC2034 # checks are strings passed to eval: variables expand there
# Cases for scripts/leak_check.py: it must fire on real-looking leaks and stay quiet on the placeholders the docs use.
# Usage: bash tests/leak_cases.sh (rc=0 = every case passed). The fake leaks below are split with '' so that this
# file itself does not match the patterns; the shell joins them at run time.
set -u
here=$(cd "$(dirname "$0")" && pwd)
LC=${LEAK_CHECK_PATH:-$here/../scripts/leak_check.py}
PYTHON_BIN=${PYTHON:-python3}
T=$(mktemp -d "${TMPDIR:-/tmp}/leak-cases.XXXXXX"); trap 'rm -rf "$T"' EXIT
fails=0; n=0
ok() { n=$((n + 1)); if eval "$2"; then echo "ok   $1"; else echo "FAILED $1"; fails=$((fails + 1)); fi; }
cd "$T" && git init -q repo && cd repo || exit 2
g() { git -c user.name=tester -c user.email=tester@example.com "$@"; }

printf 'path /Users/you/code/app and /home/you/x and /home/runner/work\nmail you@example.com\n' > clean.md
g add -A && g commit -qm clean
"$PYTHON_BIN" "$LC" --history > "$T/o1"; rc=$?
ok 'placeholders and example addresses pass' '[ $rc = 0 ] && grep -q "0 finding" "$T/o1"'

# Pull-request CI includes a merge commit constructed by GitHub, with its public bot identity.
git -c user.name=GitHub -c user.email='noreply''@github.com' commit -q --allow-empty -m 'synthetic merge identity'
"$PYTHON_BIN" "$LC" --history > "$T/bot"; rc=$?
ok 'GitHub synthetic merge identity passes history scan' '[ $rc = 0 ]'

leak() { # leak <name> <content> <rule>
  printf '%s\n' "$2" > leak.txt
  "$PYTHON_BIN" "$LC" > "$T/o2"; rc=$?
  ok "$1" "[ \$rc = 1 ] && grep -qF '[$3]' \"\$T/o2\""
  rm -f leak.txt
}
leak 'macOS home path' 'see /Users/''alice/code' home-path
leak 'Linux home path' 'see /home/''bob/code' home-path
leak 'e-mail address' 'ping alice''@corp.io' email
leak 'other noreply address still blocked' 'noreply''@corp.io' email
leak 'GitHub token' "token ghp_$(printf 'a%.0s' $(seq 1 36))" github-token
leak 'sk- style key' "key sk-$(printf 'b%.0s' $(seq 1 30))" api-key
leak 'AWS key' 'AKIA''ABCDEFGHIJKLMNOP' aws-key
leak 'private key header' '-----BEGIN RSA PRIVATE'' KEY-----' private-key

printf 'acme\nre: \\bproj-[0-9]+\\b\nallow: github.com/acme/tool\n' > "$T/terms.txt"
printf 'clone github.com/acme/tool\n' > url.md
"$PYTHON_BIN" "$LC" --terms-file "$T/terms.txt" > "$T/o3"; rc=$?
ok 'allow: removes the allowed text before matching terms' '[ $rc = 0 ]'
printf 'work for ACME Corp on PROJ-12\n' > client.md
"$PYTHON_BIN" "$LC" --terms-file "$T/terms.txt" > "$T/o4"; rc=$?
ok 'private term and regex fire, case-insensitive' '[ $rc = 1 ] && grep -q "private-term" "$T/o4" && grep -q "private-regex" "$T/o4"'
rm client.md
g add -A && g commit -qm "mention acme"
"$PYTHON_BIN" "$LC" --terms-file "$T/terms.txt" > "$T/o5"; rc5=$?
"$PYTHON_BIN" "$LC" --terms-file "$T/terms.txt" --history > "$T/o6"; rc6=$?
ok '--history finds what only the commit message has' '[ $rc5 = 0 ] && [ $rc6 = 1 ] && grep -q "^commit " "$T/o6"'
g commit -q --allow-empty -m ok --author 'Acme Person <someone''@acme.io>'
"$PYTHON_BIN" "$LC" --history > "$T/o7"; rc=$?
ok '--history checks author identities' '[ $rc = 1 ] && grep -q "^identity: \[email\]" "$T/o7"'

echo "$((n - fails))/$n cases"
exit $((fails > 0))
