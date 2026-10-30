#!/usr/bin/env bash
# session-finder installer (macOS / Linux)
#
#   bash install.sh                            # this machine only: skill + venv
#   bash install.sh --sync <SessionSync folder> # also sync across machines (Linux: systemd --user service)
#
# A Linux box without a sync client can read the folder through a one-way rclone
# mirror and push what it writes with rclone:
#   SESSION_SYNC_RCLONE='onedrive:SessionSync' bash install.sh --sync ~/OneDrive/SessionSync
# Rerunning it keeps an existing sync setup (the saved folder or SESSION_SYNC_ROOT).
set -euo pipefail

SYNC_ROOT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --sync) SYNC_ROOT="${2:?--sync needs a folder}"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.workbuddy/skills/session-finder"
VENV="$DEST/.venv"
STATE="${XDG_CACHE_HOME:-$HOME/.cache}/session-finder"
FS="$DEST/scripts/find_session.py"

echo "[1/4] Copying the skill to $DEST"
mkdir -p "$DEST/scripts"
cp "$HERE/session-finder/SKILL.md" "$DEST/SKILL.md"
cp "$HERE/session-finder/scripts/find_session.py" "$FS"

# Claude Code / Codex / CodeBuddy get only SKILL.md; the script and venv above are shared.
# A client that isn't installed is skipped.
for client in .claude .codex .codebuddy; do
  [ -d "$HOME/$client" ] || continue
  sk="$HOME/$client/skills/session-finder"
  if [ -d "$sk" ] && ls -A "$sk" | grep -qv '^SKILL.md$'; then
    bak="$STATE/replaced/${client#.}-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$(dirname "$bak")"
    mv "$sk" "$bak"
    echo "      moved the old $sk to $bak"
  fi
  mkdir -p "$sk"
  cp "$HERE/session-finder/SKILL.md" "$sk/SKILL.md"
  echo "      SKILL.md -> $sk"
done

echo "[2/4] Creating a venv with zstandard (Codex compresses its sessions with zstd)"
PYBIN="${PYTHON:-python3}"
if ! command -v "$PYBIN" >/dev/null 2>&1; then
  echo "$PYBIN not found: install Python 3.9+ first" >&2
  exit 1
fi
if [ ! -x "$VENV/bin/python" ]; then
  "$PYBIN" -m venv "$VENV" || { echo "venv failed; on Ubuntu/Debian install python3-venv first" >&2; exit 1; }
fi
"$VENV/bin/python" -m pip install --disable-pip-version-check -q zstandard

echo "[3/4] Self-test"
export SESSION_FINDER_NO_UPDATE=1
"$VENV/bin/python" "$FS" list --limit 3 || true

if [ -n "$SYNC_ROOT" ]; then
  "$VENV/bin/python" "$FS" sync-root "$SYNC_ROOT" >/dev/null
fi
ROOT="$("$VENV/bin/python" "$FS" sync-root 2>/dev/null || true)"
mkdir -p "$STATE"
if [ -z "$ROOT" ]; then
  echo "[4/4] Multi-machine sync: off (search covers this machine only; rerun with --sync <folder> to turn it on)"
else
  echo "[4/4] Multi-machine sync to $ROOT"
  if [ -n "${SESSION_SYNC_RCLONE:-}" ]; then
    # Saved so a manual `sync`, or an agent running the skill without the env var, pushes too.
    printf '%s\n' "$SESSION_SYNC_RCLONE" > "$STATE/rclone_remote"
    command -v rclone >/dev/null 2>&1 || echo "      warning: rclone not found, pushes will keep failing" >&2
  fi
  if [ "$(uname -s)" != Linux ] || ! command -v systemctl >/dev/null 2>&1; then
    echo "      no systemd here: run \"$VENV/bin/python\" \"$FS\" watch at login (launchd, cron @reboot...)"
    "$VENV/bin/python" "$FS" sync || true
  else
    unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    mkdir -p "$unit_dir"
    esc() { printf '%s' "$1" | sed 's/%/%%/g'; }  # systemd treats % as a specifier
    {
      echo "[Unit]"
      echo "Description=session-finder: push agent sessions to the SessionSync folder"
      echo "After=network-online.target"
      echo
      echo "[Service]"
      echo "Environment=\"SESSION_SYNC_ROOT=$(esc "$ROOT")\""
      [ -n "${SESSION_SYNC_RCLONE:-}" ] && echo "Environment=\"SESSION_SYNC_RCLONE=$(esc "$SESSION_SYNC_RCLONE")\""
      echo "Environment=PYTHONUTF8=1"
      echo "ExecStart=$(esc "$VENV/bin/python") $(esc "$FS") watch"
      echo "# watch exits after a self-update; this brings the new version up"
      echo "Restart=always"
      echo "RestartSec=30"
      echo "Nice=10"
      echo
      echo "[Install]"
      echo "WantedBy=default.target"
    } > "$unit_dir/session-finder-sync.service"
    systemctl --user stop session-finder-sync.service 2>/dev/null || true
    SESSION_SYNC_ROOT="$ROOT" "$VENV/bin/python" "$FS" sync || true
    systemctl --user daemon-reload
    systemctl --user enable --now session-finder-sync.service
    systemctl --user --no-pager status session-finder-sync.service | head -5 || true
    if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q 'Linger=yes'; then
      echo "      to keep it running with nobody logged in: sudo loginctl enable-linger $USER"
    fi
  fi
  echo "      log: $STATE/sync.log"
fi

echo
echo "Done. Start a new agent session so it picks up the skill."
echo "Try it: \"$VENV/bin/python\" \"$FS\" find <words from a session title>"
