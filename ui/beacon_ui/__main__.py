"""python -m beacon_ui --root /path/to/programme

Serves the UI on http://127.0.0.1:8420. Localhost only, single user, no
authentication: see the README before changing any of that.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

from .bcn import locate
from .server import App, StartupError, serve


def default_data_dir(root: Path) -> Path:
    # Outside the programme root on purpose: UI state must never sync to SharePoint
    # or be mistaken for content.
    base = Path.home() / "Library" / "Application Support" / "BeaconUI"
    return base / hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:12]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="beacon-ui", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=os.environ.get("BEACON_ROOT"), help="programme root (holds programme.toml)")
    p.add_argument("--port", type=int, default=int(os.environ.get("BEACON_PORT", 8420)))
    p.add_argument("--bcn", default=None, help="path to the bcn executable (default: tooling/.venv/bin/bcn, then PATH)")
    p.add_argument("--data-dir", default=None, help="where the UI keeps its SQLite file and intake pastes")
    args = p.parse_args(argv)
    if not args.root:
        p.error("--root is required (or set BEACON_ROOT)")
    root = Path(args.root).expanduser().resolve()
    data_dir = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir(root)
    try:
        app = App(root, locate(args.bcn), data_dir / "ui.sqlite", data_dir)
    except StartupError as e:
        sys.stderr.write(f"beacon-ui: {e}\n")
        return 2
    httpd = serve(app, "127.0.0.1", args.port)
    sys.stderr.write(f"Beacon UI for {root}\n  http://127.0.0.1:{args.port}\n  data: {data_dir}\n")
    for w in app.warnings:
        sys.stderr.write(f"  warning: {w}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
