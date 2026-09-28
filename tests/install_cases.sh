#!/usr/bin/env bash
# shellcheck disable=SC2016,SC2034,SC2317,SC2329 # checks are strings passed to eval: variables and helpers are used there
# Cases for install.sh and uninstall.sh, each in a temporary HOME (never your real one): settings.json with other hooks,
# idempotence, kept config, conflicts, invalid JSON, launchd rendering (macOS, launchctl replaced by a stub), uninstall
# and --purge. Usage: bash tests/install_cases.sh (rc=0 = every case passed).
set -u
here=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$here/.." && pwd)
PYTHON_BIN=${PYTHON:-$(command -v python3)}
unset XDG_CONFIG_HOME XDG_DATA_HOME XDG_STATE_HOME AGENT_THROTTLE_CONFIG AT_PYTHON
ROOT=$(cd "$(mktemp -d "${TMPDIR:-/tmp}/install-cases.XXXXXX")" && pwd -P); trap 'rm -rf "$ROOT"' EXIT
fails=0; n=0
ok() { n=$((n + 1)); if eval "$2"; then echo "ok   $1"; else echo "FAILED $1"; fails=$((fails + 1)); fi; }
json_eq() { "$PYTHON_BIN" -c 'import json,sys; sys.exit(json.load(open(sys.argv[1])) != json.load(open(sys.argv[2])))' "$1" "$2"; }
py() { "$PYTHON_BIN" -c "$@"; }

new_home() { # fresh HOME with a Claude Code settings.json that already has other hooks and settings
  export HOME=$ROOT/$1; mkdir -p "$HOME/.claude"
  export PATH=$HOME/.local/bin:$ORIG_PATH
  cat > "$HOME/.claude/settings.json" <<'EOF'
{
  "model": "opus",
  "permissions": {"allow": ["Bash(ls:*)"]},
  "hooks": {
    "PreToolUse": [
      {"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/local/bin/other-guard.sh"}]},
      {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "echo edit"}]}
    ],
    "PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo post"}]}],
    "SessionStart": [{"hooks": [{"type": "command", "command": "echo start"}]}]
  }
}
EOF
  cp "$HOME/.claude/settings.json" "$ROOT/$1.original.json"
}
ORIG_PATH=$PATH
S() { echo "$HOME/.claude/settings.json"; }
backups() { find "$HOME/.claude" -name 'settings.json.bak-agent-throttle-*' | wc -l | tr -d ' '; }
DATA_OF() { echo "$HOME/.local/share/agent-throttle"; }

# ---------------------------------------------------------------- first install
new_home h1
"$REPO/install.sh" --python "$PYTHON_BIN" > "$ROOT/i1" 2>&1; rc=$?
ok 'install exits 0' '[ $rc = 0 ]'
ok 'all 8 commands linked into ~/.local/bin' '[ "$(find "$HOME/.local/bin" -type l | wc -l | tr -d " ")" = 8 ] && [ -x "$HOME/.local/bin/solo" ]'
ok 'settings.json is valid JSON' 'py "import json; json.load(open(\"$(S)\"))"'
ok 'other hooks and settings kept exactly; ours appended as the last PreToolUse group' 'py "
import json
new, old = json.load(open(\"$(S)\")), json.load(open(\"$ROOT/h1.original.json\"))
pre = new[\"hooks\"][\"PreToolUse\"]
assert pre[:-1] == old[\"hooks\"][\"PreToolUse\"]
assert pre[-1] == {\"matcher\": \"Bash\", \"hooks\": [{\"type\": \"command\", \"command\": \"$HOME/.local/bin/throttle-guard\"}]}
for k in (\"PostToolUse\", \"SessionStart\"): assert new[\"hooks\"][k] == old[\"hooks\"][k]
assert new[\"model\"] == old[\"model\"] and new[\"permissions\"] == old[\"permissions\"]
"'
ok 'backup taken before the change, equal to the original' '[ "$(backups)" = 1 ] && json_eq "$(find "$HOME/.claude" -name "settings.json.bak-agent-throttle-*")" "$ROOT/h1.original.json"'
ok 'config created from the example' '[ -f "$HOME/.config/agent-throttle/config.toml" ] && grep -q "How to calibrate" "$HOME/.config/agent-throttle/config.toml"'
ok 'python pinned in the shebangs and in the bash wrapper' 'head -1 "$(DATA_OF)/bin/throttle-config" | grep -qx "#!$PYTHON_BIN" && grep -q "^PYTHON=\${AT_PYTHON:-$PYTHON_BIN}" "$(DATA_OF)/bin/throttle-guard"'

cd "$ROOT" || exit 2
solo true > "$ROOT/s1" 2>&1; rc=$?
ok 'solo works from PATH after install' '[ $rc = 0 ] && grep -q "rc=0" "$ROOT/s1" && [ -s "$HOME/.local/state/agent-throttle/semaphore.log" ]'
printf '{"tool_input":{"command":"npx jest --ci"},"cwd":"%s"}' "$ROOT" | throttle-guard 2> "$ROOT/g1"; rc=$?
ok 'the hook command from settings.json blocks (exit 2) and logs' '[ $rc = 2 ] && grep -q jest-no-workers "$ROOT/g1" && [ -s "$HOME/.local/state/agent-throttle/guard-blocks.log" ]'
throttle-guard --check 'npx vitest run src/a.test.ts --maxWorkers=2' > "$ROOT/g2"; rc=$?
ok 'throttle-guard --check ok' '[ $rc = 0 ] && grep -qx ok "$ROOT/g2"'

# ---------------------------------------------------------------- idempotence
cp "$(S)" "$ROOT/h1.after1.json"
echo '# my edit' >> "$HOME/.config/agent-throttle/config.toml"
"$REPO/install.sh" --python "$PYTHON_BIN" > "$ROOT/i2" 2>&1; rc=$?
ok 'second install exits 0 and reports the hook already present' '[ $rc = 0 ] && grep -q "hook already present" "$ROOT/i2"'
ok 'second install: settings.json byte-identical, no new backup' 'cmp -s "$(S)" "$ROOT/h1.after1.json" && [ "$(backups)" = 1 ]'
ok 'second install keeps the edited config' 'grep -q "# my edit" "$HOME/.config/agent-throttle/config.toml"'
ok 'still exactly one throttle-guard hook' '[ "$(grep -c throttle-guard "$(S)")" = 1 ]'

# ---------------------------------------------------------------- uninstall
"$REPO/uninstall.sh" > "$ROOT/u1" 2>&1; rc=$?
ok 'uninstall exits 0' '[ $rc = 0 ]'
ok 'uninstall: settings.json equal to the original again' 'json_eq "$(S)" "$ROOT/h1.original.json"'
ok 'uninstall: backup taken before removing the hook' '[ "$(backups)" = 2 ]'
ok 'uninstall: links and data folder gone' '[ -z "$(find "$HOME/.local/bin" -type l)" ] && [ ! -d "$(DATA_OF)" ]'
ok 'uninstall: config and logs kept' '[ -f "$HOME/.config/agent-throttle/config.toml" ] && [ -d "$HOME/.local/state/agent-throttle" ]'
"$REPO/uninstall.sh" > "$ROOT/u2" 2>&1; rc=$?
ok 'uninstall again: exit 0, nothing to do' '[ $rc = 0 ] && grep -q "hook not present" "$ROOT/u2" && json_eq "$(S)" "$ROOT/h1.original.json"'
"$REPO/install.sh" --python "$PYTHON_BIN" > /dev/null 2>&1 && "$REPO/uninstall.sh" --purge > "$ROOT/u3" 2>&1; rc=$?
ok 'uninstall --purge removes config and logs' '[ $rc = 0 ] && [ ! -e "$HOME/.config/agent-throttle" ] && [ ! -e "$HOME/.local/state/agent-throttle" ]'

# ---------------------------------------------------------------- a different prefix replaces the old hook
new_home h2
"$REPO/install.sh" --python "$PYTHON_BIN" > /dev/null 2>&1
"$REPO/install.sh" --python "$PYTHON_BIN" --prefix "$HOME/bin2" > "$ROOT/i3" 2>&1; rc=$?
ok 'new prefix: hook replaced, one entry, new path' '[ $rc = 0 ] && grep -q "hook replaced" "$ROOT/i3" && [ "$(grep -c throttle-guard "$(S)")" = 1 ] && grep -q "$HOME/bin2/throttle-guard" "$(S)"'

# ---------------------------------------------------------------- two changes in the same second keep both backups
new_home h2b
S2=$(S)
"$PYTHON_BIN" "$REPO/scripts/settings_merge.py" add --settings "$S2" --command /x/throttle-guard > /dev/null
"$PYTHON_BIN" "$REPO/scripts/settings_merge.py" remove --settings "$S2" > /dev/null
"$PYTHON_BIN" "$REPO/scripts/settings_merge.py" add --settings "$S2" --command /x/throttle-guard > /dev/null
ok 'backups never overwrite each other (3 changes, 3 backups; the first one is the original)' '[ "$(backups)" = 3 ] && json_eq "$(find "$HOME/.claude" -name "settings.json.bak-agent-throttle-*" | sort | head -1)" "$ROOT/h2b.original.json"'

# ---------------------------------------------------------------- conflicts and refusals
new_home h3
mkdir -p "$HOME/.local/bin"; echo 'not ours' > "$HOME/.local/bin/solo"
"$REPO/install.sh" --python "$PYTHON_BIN" > "$ROOT/i4" 2>&1; rc=$?
ok 'existing file with the same name: kept, install exits 1, the rest installed' '[ $rc = 1 ] && [ "$(cat "$HOME/.local/bin/solo")" = "not ours" ] && [ -L "$HOME/.local/bin/solo-ci" ] && grep -q "skipped .*solo: a file that is not ours" "$ROOT/i4"'

new_home h4
printf '{ "hooks": { not json' > "$(S)"; cp "$(S)" "$ROOT/h4.bad"
"$REPO/install.sh" --python "$PYTHON_BIN" > "$ROOT/i5" 2>&1; rc=$?
ok 'invalid settings.json: install fails, file untouched, no backup' '[ $rc != 0 ] && cmp -s "$(S)" "$ROOT/h4.bad" && [ "$(backups)" = 0 ] && grep -q "left untouched" "$ROOT/i5"'

new_home h5
rm -rf "$HOME/.claude"
"$REPO/install.sh" --python "$PYTHON_BIN" > "$ROOT/i6" 2>&1; rc=$?
ok 'no ~/.claude: install ok, hook skipped, nothing created there' '[ $rc = 0 ] && grep -q "hook skipped" "$ROOT/i6" && [ ! -e "$HOME/.claude" ]'

new_home h6
"$REPO/install.sh" --python "$PYTHON_BIN" --no-hook > /dev/null 2>&1; rc=$?
ok '--no-hook leaves settings.json alone' '[ $rc = 0 ] && cmp -s "$(S)" "$ROOT/h6.original.json"'
"$REPO/install.sh" --python /nonexistent/python --data-dir "$HOME/x" > "$ROOT/i7" 2>&1; rc=$?
ok 'data dir must end with /agent-throttle' '[ $rc = 1 ] && grep -q "must end with /agent-throttle" "$ROOT/i7"'

# ---------------------------------------------------------------- launchd (macOS; launchctl replaced by a stub)
if [ "$(uname -s)" = Darwin ]; then
  new_home h7
  stub=$ROOT/launchctl-stub; printf '#!/bin/sh\necho "$@" >> %s/launchctl.calls\n' "$ROOT" > "$stub"; chmod +x "$stub"
  AT_LAUNCHCTL=$stub "$REPO/install.sh" --python "$PYTHON_BIN" --launchd --yes > "$ROOT/i8" 2>&1; rc=$?
  ok 'launchd: 3 plists rendered, valid, no placeholder left' '[ $rc = 0 ] && [ "$(ls "$HOME/Library/LaunchAgents"/dev.agent-throttle.*.plist | wc -l | tr -d " ")" = 3 ] && ! grep -q @ "$HOME/Library/LaunchAgents"/dev.agent-throttle.*.plist && plutil -lint "$HOME/Library/LaunchAgents"/dev.agent-throttle.*.plist > /dev/null'
  ok 'launchd: jobs point to the data folder and the config' 'grep -q "$HOME/.local/share/agent-throttle/bin/throttle-load" "$HOME/Library/LaunchAgents/dev.agent-throttle.load-log.plist" && grep -q "$HOME/.config/agent-throttle/config.toml" "$HOME/Library/LaunchAgents/dev.agent-throttle.report.plist"'
  ok 'launchd: bootstrap called for each job' '[ "$(grep -c bootstrap "$ROOT/launchctl.calls")" = 3 ]'
  AT_LAUNCHCTL=$stub "$REPO/uninstall.sh" > /dev/null 2>&1
  ok 'uninstall: launchd jobs booted out and removed' '[ -z "$(ls "$HOME/Library/LaunchAgents" 2>/dev/null)" ] && [ "$(grep -c bootout "$ROOT/launchctl.calls")" -ge 6 ]'
else
  new_home h7
  "$REPO/install.sh" --python "$PYTHON_BIN" --launchd --yes > "$ROOT/i8" 2>&1; rc=$?
  ok 'launchd on Linux: skipped with a message' '[ $rc = 0 ] && grep -q "macOS only" "$ROOT/i8"'
fi

echo "$((n - fails))/$n cases"
exit $((fails > 0))
