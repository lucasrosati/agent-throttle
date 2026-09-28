#!/usr/bin/env bash
# uninstall.sh: remove what install.sh installed. Safe to run again.
# Removes the hook from the Claude Code settings (backup first; other hooks kept), the launchd jobs, the command links
# that point to the data folder, and the data folder. The config file and the logs stay unless --purge.
set -euo pipefail

COMMANDS="solo solo-ci throttle-guard throttle-config throttle-load throttle-clean throttle-report throttle-logrotate"
LABEL_PREFIX=dev.agent-throttle
LAUNCHCTL=${AT_LAUNCHCTL:-launchctl}
DATA=${XDG_DATA_HOME:-$HOME/.local/share}/agent-throttle
PREFIX=""; SETTINGS=""; PURGE=0

usage() {
  cat <<EOF
usage: ./uninstall.sh [options]
  --prefix DIR      where the commands were linked (default: what install.sh recorded, else ~/.local/bin)
  --data-dir DIR    data folder (default: \${XDG_DATA_HOME:-~/.local/share}/agent-throttle)
  --settings FILE   Claude Code settings file (default: what install.sh recorded, else ~/.claude/settings.json)
  --purge           also delete the config file and the logs
EOF
}
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX=$2; shift ;;
    --data-dir) DATA=$2; shift ;;
    --settings) SETTINGS=$2; shift ;;
    --purge) PURGE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 64 ;;
  esac
  shift
done
say() { echo "[uninstall] $*"; }
case "$DATA" in */agent-throttle) ;; *) echo "[uninstall] error: --data-dir must end with /agent-throttle" >&2; exit 1 ;; esac
src=$(cd "$(dirname "$0")" && pwd -P)

recorded() { if [ -f "$DATA/install.env" ]; then sed -n "s/^$1=//p" "$DATA/install.env" | head -1; fi; }
PREFIX=${PREFIX:-$(recorded prefix)}; PREFIX=${PREFIX:-$HOME/.local/bin}
SETTINGS=${SETTINGS:-$(recorded settings)}; SETTINGS=${SETTINGS:-$HOME/.claude/settings.json}
PY=$(recorded python)
[ -n "$PY" ] && [ -x "$PY" ] || PY=$(command -v python3 || true)

# what --purge deletes, read before the data folder (and throttle-config) goes away
cfg_file=""; logs_dir=""
if [ $PURGE = 1 ]; then
  tc=$DATA/bin/throttle-config; [ -x "$tc" ] || tc=$src/bin/throttle-config
  cfg_file=$("$tc" path 2>/dev/null || true)
  logs_dir=$("$tc" get logs.dir 2>/dev/null || true)
fi

# hook
merge=$DATA/scripts/settings_merge.py; [ -f "$merge" ] || merge=$src/scripts/settings_merge.py
if [ -f "$SETTINGS" ]; then
  if [ -n "$PY" ]; then
    "$PY" "$merge" remove --settings "$SETTINGS" | sed "s|^|[uninstall] $SETTINGS: |" \
      || say "could not update $SETTINGS (left untouched); remove the throttle-guard hook by hand"
  else
    say "no python3 found: remove the throttle-guard hook from $SETTINGS by hand"
  fi
fi

# launchd
if [ "$(uname -s)" = Darwin ]; then
  for plist in "$HOME/Library/LaunchAgents/$LABEL_PREFIX".*.plist; do
    [ -e "$plist" ] || continue
    label=$(basename "$plist" .plist)
    $LAUNCHCTL bootout "gui/$(id -u)/$label" 2>/dev/null || true
    rm -f "$plist"
    say "launchd: $label removed"
  done
fi

# links (only those pointing to our data folder)
for c in $COMMANDS; do
  target=$PREFIX/$c
  if [ -L "$target" ] && [ "$(readlink "$target")" = "$DATA/bin/$c" ]; then rm -f "$target"; fi
done
say "command links removed from $PREFIX"

rm -rf "${DATA:?}"
say "data folder removed: $DATA"

if [ $PURGE = 1 ]; then
  if [ -n "$cfg_file" ] && [ -f "$cfg_file" ]; then rm -f "$cfg_file"; rmdir "$(dirname "$cfg_file")" 2>/dev/null || true; say "config removed: $cfg_file"; fi
  case "$logs_dir" in
    */agent-throttle) rm -rf "$logs_dir"; say "logs removed: $logs_dir" ;;
    "") ;;
    *) say "logs kept (custom folder, delete by hand): $logs_dir" ;;
  esac
else
  say "config and logs kept (use --purge to delete them)"
fi
