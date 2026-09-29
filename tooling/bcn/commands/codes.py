"""bcn codes: enumerate every diagnostic code, its default level and meaning."""

from __future__ import annotations

import argparse

from .. import codes
from ..envelope import Envelope

HELP = "list every diagnostic code"


def add_args(p: argparse.ArgumentParser) -> None:
    pass


def run(args: argparse.Namespace, env: Envelope) -> None:
    env.target = "codes"
    env.extra["codes"] = [
        {"code": c, "level": lvl, "area": c.split("_")[0], "description": desc}
        for c, (lvl, desc) in sorted(codes.CODES.items())
    ]
