#!/usr/bin/env bash
# One-time setup of the build machine. Needs network; nothing after this does.
#   - Python venv for bcn (tooling/.venv) with the UI installed alongside
#   - pinned Marp toolchain (tooling/node, npm ci)
#   - pinned Chrome for Testing (tooling/vendor/chrome)
# ffmpeg comes from Homebrew and is checked, not installed: brew install ffmpeg (7.1.x).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
CHROME_VERSION=$(python3 -c "import re;print(re.search(r'CHROME_VERSION = \"([^\"]+)\"', open('tooling/bcn/tools.py').read()).group(1))")

PY=${PYTHON:-python3}
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11+ required")'
[ -d tooling/.venv ] || "$PY" -m venv tooling/.venv
tooling/.venv/bin/pip install -q -e "tooling[test]" -e ui

(cd tooling/node && npm ci --no-audit --no-fund)
(cd tooling/node && npx --no-install @puppeteer/browsers install "chrome@${CHROME_VERSION}" --path "$ROOT/tooling/vendor/chrome")
# Clear the download quarantine so Gatekeeper does not stall the first headless launch.
xattr -dr com.apple.quarantine tooling/vendor/chrome 2>/dev/null || true

tooling/.venv/bin/bcn doctor --human --quiet
