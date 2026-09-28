#!/usr/bin/env bash
# install.sh: install agent-throttle for the current user. Safe to run again: it updates what it installed and leaves
# everything else alone.
#
# What it does:
#   1. finds Python 3.11 or newer and pins it in the scripts (launchd and hooks do not read your shell profile);
#   2. copies bin/, lib/, config/, templates/ and scripts/ to the data folder and links the commands into the prefix
#      (an existing file with the same name that is not ours is never overwritten without --force);
#   3. creates the config file from the example if there is none (an existing one is kept);
#   4. merges the PreToolUse hook into Claude Code's settings.json (backup first, JSON validated after, other hooks
#      and settings kept);
#   5. with --launchd (macOS): renders the three launchd jobs and loads them, after asking.
set -euo pipefail

COMMANDS="solo solo-ci throttle-guard throttle-config throttle-load throttle-clean throttle-report throttle-logrotate"
LABEL_PREFIX=dev.agent-throttle
LAUNCHCTL=${AT_LAUNCHCTL:-launchctl}   # tests replace it
PREFIX=$HOME/.local/bin
DATA=${XDG_DATA_HOME:-$HOME/.local/share}/agent-throttle
SETTINGS=$HOME/.claude/settings.json
PY=""; HOOK=1; LAUNCHD=0; YES=0; FORCE=0

usage() {
  cat <<EOF
usage: ./install.sh [options]
  --prefix DIR      where the commands are linked (default: ~/.local/bin)
  --data-dir DIR    where the files are copied (default: \${XDG_DATA_HOME:-~/.local/share}/agent-throttle)
  --python PATH     Python 3.11+ to use (default: the first one found on PATH)
  --settings FILE   Claude Code settings file for the hook (default: ~/.claude/settings.json)
  --no-hook         do not touch the Claude Code settings
  --launchd         also install the launchd jobs (macOS): load sampling, weekly report, log rotation
  --yes             do not ask before loading the launchd jobs
  --force           replace existing commands with the same names in the prefix
EOF
}
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX=$2; shift ;;
    --data-dir) DATA=$2; shift ;;
    --python) PY=$2; shift ;;
    --settings) SETTINGS=$2; shift ;;
    --no-hook) HOOK=0 ;;
    --launchd) LAUNCHD=1 ;;
    --yes) YES=1 ;;
    --force) FORCE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 64 ;;
  esac
  shift
done

say() { echo "[install] $*"; }
die() { echo "[install] error: $*" >&2; exit 1; }
src=$(cd "$(dirname "$0")" && pwd -P)
[ -f "$src/bin/solo" ] && [ -f "$src/lib/agent_throttle/guard.py" ] || die "run install.sh from the agent-throttle folder"
case "$DATA" in */agent-throttle) ;; *) die "--data-dir must end with /agent-throttle (it is replaced on every install)" ;; esac
mkdir -p "$DATA"
[ "$(cd "$DATA" && pwd -P)" != "$src" ] || die "--data-dir cannot be the source folder"

find_python() {
  local c p
  for c in "$@" python3 python3.14 python3.13 python3.12 python3.11 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    [ -n "$c" ] || continue
    p=$(command -v "$c" 2>/dev/null) || continue
    if "$p" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then echo "$p"; return 0; fi
  done
  return 1
}
PY=$(find_python "$PY" "${AT_PYTHON:-}") || die "Python 3.11 or newer not found (use --python PATH)"
say "Python: $PY ($("$PY" -c 'import platform; print(platform.python_version())'))"

# 1-2. files, pinned interpreter, links
for d in bin lib config templates scripts; do
  rm -rf "${DATA:?}/$d"
  cp -R "$src/$d" "$DATA/$d"
done
cp "$src/LICENSE" "$DATA/LICENSE"
find "$DATA" -name __pycache__ -type d -prune -exec rm -rf {} +
for f in "$DATA"/bin/* "$DATA/lib/agent_throttle/guard.py" "$DATA/scripts/settings_merge.py"; do
  if head -1 "$f" | grep -q '^#!/usr/bin/env python3$'; then
    { echo "#!$PY"; tail -n +2 "$f"; } > "$f.tmp"
  else
    sed "s|^PYTHON=\${AT_PYTHON:-python3}|PYTHON=\${AT_PYTHON:-$PY}|" "$f" > "$f.tmp"
  fi
  mv "$f.tmp" "$f"; chmod 755 "$f"
done
say "files: $DATA"

mkdir -p "$PREFIX"
conflicts=0
for c in $COMMANDS; do
  target=$PREFIX/$c
  if [ -L "$target" ]; then
    case "$(readlink "$target")" in
      "$DATA/bin/$c"|*/agent-throttle/bin/"$c") ;;
      *) if [ $FORCE = 0 ]; then say "skipped $target: a link to something else (use --force)"; conflicts=1; continue; fi ;;
    esac
  elif [ -e "$target" ] && [ $FORCE = 0 ]; then
    say "skipped $target: a file that is not ours (use --force)"; conflicts=1; continue
  fi
  ln -sfn "$DATA/bin/$c" "$target"
done
say "commands linked in $PREFIX: $COMMANDS"

# 3. config
"$DATA/bin/throttle-config" init | sed 's/^/[install] config: /'
CONFIG=$("$DATA/bin/throttle-config" path)

# 4. Claude Code hook
if [ $HOOK = 1 ]; then
  if [ -d "$(dirname "$SETTINGS")" ]; then
    "$PY" "$DATA/scripts/settings_merge.py" add --settings "$SETTINGS" --command "$PREFIX/throttle-guard" \
      | sed "s|^|[install] $SETTINGS: |" || die "could not merge the hook into $SETTINGS (file left untouched)"
  else
    say "no $(dirname "$SETTINGS") folder (Claude Code not set up?): hook skipped; rerun with --settings FILE"
  fi
fi
printf 'prefix=%s\nsettings=%s\nhook=%s\npython=%s\n' "$PREFIX" "$SETTINGS" "$HOOK" "$PY" > "$DATA/install.env"

# 5. launchd
if [ $LAUNCHD = 1 ]; then
  if [ "$(uname -s)" != Darwin ]; then
    say "--launchd is macOS only (Linux: see CONTRIBUTING.md); skipped"
  else
    agents=$HOME/Library/LaunchAgents; logs=$HOME/Library/Logs/agent-throttle
    answer=y
    if [ $YES = 0 ]; then
      read -r -p "[install] load 3 launchd jobs (load sampling every 5 min, weekly report, daily log rotation)? [y/N] " answer || answer=n
    fi
    if [ "$answer" = y ]; then
      mkdir -p "$agents" "$logs"
      for tpl in "$DATA"/templates/launchd/*.plist.in; do
        name=$(basename "$tpl" .plist.in); label=$LABEL_PREFIX.$name; plist=$agents/$label.plist
        sed -e "s|@LABEL_PREFIX@|$LABEL_PREFIX|g" -e "s|@BIN@|$DATA/bin|g" -e "s|@PYTHON@|$PY|g" \
            -e "s|@CONFIG@|$CONFIG|g" -e "s|@LOG_DIR@|$logs|g" "$tpl" > "$plist"
        plutil -lint "$plist" >/dev/null || die "invalid plist: $plist"
        $LAUNCHCTL bootout "gui/$(id -u)/$label" 2>/dev/null || true
        $LAUNCHCTL bootstrap "gui/$(id -u)" "$plist"
        say "launchd: $label loaded ($plist)"
      done
    else
      say "launchd jobs not loaded"
    fi
  fi
fi

case ":$PATH:" in *":$PREFIX:"*) ;; *) say "note: $PREFIX is not on your PATH; add it to your shell profile" ;; esac
say "done. Next: paste the rules for your agents from $DATA/templates/ (claude-md-rules.md, agents-md-rules.md)"
say "try it: solo sleep 3   and   throttle-guard --check 'npx jest'"
exit $conflicts
