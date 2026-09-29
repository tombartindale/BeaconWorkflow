#!/usr/bin/env bash
# Run the whole pipeline over a scratch copy of the example programme.
#   scripts/demo.sh /tmp/beacon-demo
# Leaves T02 needing a hand-placed cue (a cut spans its slide 4/5 break) and T01
# with two suspected mis-transcriptions to proofread: open the UI to deal with them.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST=${1:-/tmp/beacon-demo}
BCN="$PWD/tooling/.venv/bin/bcn"
rm -rf "$DEST"
cp -Rp example "$DEST"   # -p matters: freshness is judged on modification times
sleep 2                  # let the copied media settle past the "still syncing" guard
for step in validate render cues subtitles; do
  "$BCN" "$step" "$DEST" --quiet --human || true
done
"$BCN" compose "$DEST/KV7015/U01/T02" --quiet --human --no-bumpers || true
"$BCN" status "$DEST" --quiet --human | head -8
echo
echo "UI: $PWD/ui/server/bin/beacon-ui --root $DEST"
