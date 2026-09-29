"""bcn doctor: check every external binary is present and at its pinned version.

Run it before a long job, or at backend startup. Each tool is one result row.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import tools
from ..config import DEFAULTS, Config
from ..envelope import Envelope, Fail, TopicResult
from ..tree import find_root

HELP = "check external tools are present and pinned"


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("path", nargs="?", default=".", help="programme root (for [tools] overrides)")


def run(args: argparse.Namespace, env: Envelope) -> None:
    from ..config import load
    try:
        root = find_root(Path(args.path))
        cfg = load(root)
        env.root = root
    except Fail:
        cfg = Config(Path("."), DEFAULTS)
    env.target = "tools"
    for name, pin in (("ffmpeg", tools.FFMPEG_VERSION), ("ffprobe", tools.FFMPEG_VERSION),
                      ("marp", tools.MARP_CLI_VERSION), ("chrome", tools.CHROME_VERSION)):
        r = TopicResult(name, name)
        r.extra["pinned"] = pin
        try:
            tl = tools.require(cfg, name)
            r.extra["found"] = ", ".join(f"{k} {v}" for k, v in (tl.versions or {}).items())
            r.extra["path"] = getattr(tl, name, None)
        except Fail as f:
            f.diagnostic.topic = None
            r.diagnostics.append(f.diagnostic)
            r.ok = False
        env.results.append(r)
