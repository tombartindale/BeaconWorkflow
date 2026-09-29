#!/usr/bin/env bash
# Start Beacon against the live OneDrive folder.
#
#   ./start.sh            check tools, show what is waiting in OneDrive, start the UI
#   ./start.sh --pull     the same, but pull from OneDrive before starting
#
# The pipeline never runs on the OneDrive folder itself: it runs on a local working
# copy, working_area/ beside this script, and the UI's Sync page moves files between
# the two. The first run creates working_area/ and pulls into it.
#
# Override any of these with environment variables, e.g. BEACON_PORT=8500 ./start.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

REMOTE="${BEACON_REMOTE:-$HOME/Library/CloudStorage/OneDrive-NorthumbriaUniversity-ProductionAzureAD/BEACON Content Production - General/Module Development}"
ROOT="${BEACON_ROOT:-$HERE/working_area}"
PORT="${BEACON_PORT:-8420}"
BCN="$HERE/tooling/.venv/bin/bcn"
UI="$HERE/ui/server/bin/beacon-ui"
URL="http://127.0.0.1:$PORT"

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

PULL=0
for arg in "$@"; do
  case "$arg" in
    --pull) PULL=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) fail "Unknown option: $arg (try --help)" ;;
  esac
done

# -- already running? ---------------------------------------------------------------
if curl -fsS -o /dev/null "$URL/api/boot" 2>/dev/null; then
  say "Beacon is already running at $URL; opening it."
  open "$URL"
  exit 0
fi

# -- installed and pinned? ------------------------------------------------------------
[ -x "$BCN" ] && [ -f "$HERE/ui/server/dist/cli.js" ] && [ -f "$HERE/ui/app/dist/spa/index.html" ] \
  || fail "Beacon is not installed yet. Run: $HERE/scripts/setup.sh"
say "Checking tools…"
"$BCN" doctor "$HERE" --human --quiet || fail "A pinned tool is missing or the wrong version (see above). Run scripts/setup.sh."

# -- OneDrive reachable? --------------------------------------------------------------
[ -d "$REMOTE" ] || fail "The OneDrive folder is not there:
  $REMOTE
Is OneDrive running and signed in? Set BEACON_REMOTE if it has moved."
case "$ROOT" in
  "$HOME/Library/CloudStorage/"*) fail "BEACON_ROOT must be on local disk, not inside OneDrive: $ROOT" ;;
esac

# -- working copy ---------------------------------------------------------------------
if [ ! -f "$ROOT/programme.toml" ]; then
  say "No working copy at $ROOT yet: creating it and pulling from OneDrive…"
  "$BCN" sync "$ROOT" --pull --init --remote "$REMOTE" --human --quiet || \
    say "The first pull reported problems (above). The UI will show them; carry on."
else
  CONFIGURED="$(python3 - "$ROOT/programme.toml" <<'EOF'
import sys, tomllib
print(tomllib.load(open(sys.argv[1], "rb")).get("sync", {}).get("remote", ""))
EOF
)"
  if [ "$CONFIGURED" != "$REMOTE" ]; then
    printf '\033[33mNote:\033[0m %s syncs with\n  %s\nnot\n  %s\nEdit [sync] remote in programme.toml if that is wrong.\n' \
      "$ROOT/programme.toml" "${CONFIGURED:-(nothing)}" "$REMOTE"
  fi
  if [ "$PULL" = 1 ]; then
    say "Pulling from OneDrive…"
    "$BCN" sync "$ROOT" --pull --human --quiet || say "The pull reported problems (above); resolve them on the Sync page."
  else
    # A dry run only reads file attributes: it never downloads anything.
    "$BCN" sync "$ROOT" --pull --dry-run --quiet 2>/dev/null | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
c = d.get("counts", {})
n, k = c.get("copy", 0) + c.get("check", 0), c.get("conflict", 0)
if n or k:
    msg = f"OneDrive has {n} change(s) to pull"
    if k:
        msg += f" and {k} conflict(s)"
    print(msg + ". Use the Sync page, or restart with ./start.sh --pull")
else:
    print("The working copy is up to date with OneDrive.")
' || true
  fi
fi

# -- UI -------------------------------------------------------------------------------
say "Starting Beacon at $URL  (working copy: $ROOT)"
say "Press Ctrl-C to stop."
( for _ in $(seq 1 40); do
    sleep 0.5
    if curl -fsS -o /dev/null "$URL/api/boot" 2>/dev/null; then open "$URL"; break; fi
  done ) &
exec "$UI" --root "$ROOT" --port "$PORT"
