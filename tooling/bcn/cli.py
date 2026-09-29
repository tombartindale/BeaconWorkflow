"""bcn: the Beacon delivery pipeline CLI.

stdout is always exactly one JSON envelope. Progress and logs go to stderr as
NDJSON. Nothing prompts; nothing reads stdin.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import traceback

from . import progress
from .envelope import Diagnostic, Envelope, Fail, emit
from .tree import find_root, resolve

COMMANDS = [
    "validate", "render", "script", "bumpers", "cues", "subtitles", "compose", "status", "package", "qa",
    "show", "diagnostics", "review", "ack", "edit", "intake", "translation", "sync", "schema", "codes", "doctor",
]
# Commands whose positional argument is not a tree path.
NO_TARGET = {"schema", "codes", "doctor"}


class UsageError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(message)

    def exit(self, status: int = 0, message: str | None = None) -> None:  # type: ignore[override]
        # --help: print to stderr, keep stdout for envelopes only.
        if message:
            sys.stderr.write(message)
        raise SystemExit(status)

    def print_help(self, file=None) -> None:  # type: ignore[override]
        super().print_help(sys.stderr)


def build_parser() -> Parser:
    common = Parser(add_help=False)
    common.add_argument("--human", action="store_true", help="format the envelope as a table")
    common.add_argument("--quiet", action="store_true", help="suppress progress and logs on stderr")
    common.add_argument("--force", action="store_true", help="rebuild rather than skip unchanged work")
    common.add_argument("--theme", default=None, help="override theme resolution")
    common.add_argument("--lang", default="en", choices=["en", "zh"])
    common.add_argument("--jobs", type=int, default=1, help="topics to process in parallel")

    p = Parser(prog="bcn", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True, parser_class=Parser)
    for name in COMMANDS:
        mod = importlib.import_module(f".commands.{name}", __package__)
        sp = sub.add_parser(name, parents=[common], help=getattr(mod, "HELP", ""))
        if name not in NO_TARGET:
            sp.add_argument("path", help="topic, unit, module or programme directory")
        mod.add_args(sp)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    progress.install_signal_handlers()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except UsageError as e:
        tool = next((a for a in argv if a in COMMANDS), "bcn")
        env = Envelope(tool, " ".join(argv), None)
        env.diagnostics.append(Diagnostic("USAGE", str(e), hint="bcn <command> --help"))
        return emit(env, "--human" in argv)
    progress.set_quiet(args.quiet)
    mod = importlib.import_module(f".commands.{args.command}", __package__)

    if args.command in NO_TARGET:
        env = Envelope(args.command, getattr(args, "tool", None) or "", None)
        try:
            mod.run(args, env)
        except Fail as f:
            env.diagnostics.append(f.diagnostic)
        return emit(env, args.human)

    env = Envelope(args.command, args.path, None)
    try:
        if args.jobs < 1:
            raise Fail("USAGE", "--jobs must be at least 1.")
        if hasattr(mod, "prepare"):
            mod.prepare(args)  # e.g. sync --init creates the root before it can be resolved
        try:
            env.root = find_root(__import__("pathlib").Path(args.path))
        except Fail:
            pass
        target = resolve(args.path)
        env.root = target.root
        env.target = target.rel
        mod.run(args, env, target)
    except Fail as f:
        env.diagnostics.append(f.diagnostic)
    except KeyboardInterrupt:
        env.cancelled = True
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(traceback.format_exc())
        env.diagnostics.append(Diagnostic("INTERNAL", f"{type(e).__name__}: {e}", hint="This is a bug in bcn."))
    if progress.CANCEL.is_set():
        env.cancelled = True
    return emit(env, args.human)


if __name__ == "__main__":
    sys.exit(main())
