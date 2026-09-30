#!/usr/bin/env bash
# One-time import of the existing local working_area/ programme into the Docker Compose
# stack's shared "programme-root" volume. Run this once, after `docker compose up -d`
# has started the stack (the api container must already be running), and before pointing
# colleagues at the deployment.
#
# Usage: docker compose up -d && deploy/import-programme.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d working_area ]; then
  echo "working_area/ not found in the repo root; nothing to import." >&2
  exit 1
fi

echo "Pulling any unsynced changes from OneDrive before import..."
tooling/.venv/bin/bcn sync --pull working_area || {
  echo "warning: bcn sync --pull failed or found nothing to pull; continuing with what's on disk." >&2
}

echo "Capturing a pre-import baseline (bcn status)..."
tooling/.venv/bin/bcn status working_area > /tmp/beacon-import-baseline.json || true

echo "Copying working_area/ (excluding sync-state.json) into the programme-root volume..."
# Copy into the already-running api container's mounted volume (started by
# `docker compose up -d` beforehand) via `docker compose cp`, which needs no knowledge of
# the volume's generated name (unlike a raw `docker run -v <name>:...`).
TMP_STAGE="$(mktemp -d)"
trap 'rm -rf "$TMP_STAGE"' EXIT
mkdir -p "$TMP_STAGE/programme"
tar -C working_area --exclude=sync-state.json -cf - . | tar -C "$TMP_STAGE/programme" -xf -
docker compose cp "$TMP_STAGE/programme/." api:/data/programme

echo "Import complete. Verify with:"
echo "  docker compose exec api /app/tooling/.venv/bin/bcn status /data/programme"
echo "and diff the result against /tmp/beacon-import-baseline.json"
