"""bcn review: list suspected mis-transcriptions, and record accept or correct.

Decisions are written to the topic's review.json, which is content and
survives build/ being deleted. A correction is applied to the delivered subtitle
text by bcn subtitles; timings are never touched.
"""

from __future__ import annotations

import argparse
import getpass

from .. import fsutil, reviewfile
from ..envelope import Envelope, Fail, TopicResult
from ..tree import Target

HELP = "list suspected mis-transcriptions, or accept/correct one"


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--item", help="divergence id from cues-report.json")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--accept", action="store_true", help="the SRT reading is fine as it is")
    g.add_argument("--correct", metavar="TEXT", help="the SRT should read TEXT here")
    g.add_argument("--clear", action="store_true", help="remove a previous decision")
    p.add_argument("--by", default=None, help="who made the decision (default: login name)")


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    deciding = args.accept or args.correct is not None or args.clear
    if deciding and (target.level != "topic" or not args.item):
        raise Fail("USAGE", "Deciding needs a single topic directory and --item.")
    if args.item and not deciding:
        raise Fail("USAGE", "--item needs one of --accept, --correct TEXT or --clear.")
    if args.correct is not None and not args.correct.strip():
        raise Fail("USAGE", "--correct needs the text the subtitle should read.")
    for t in target.topics:
        r = TopicResult(t.id, t.rel)
        report = fsutil.read_json(t.cues_report) or {}
        items = [d for d in report.get("divergences", []) if d.get("kind") == "mistranscription"]
        if deciding:
            item = next((d for d in items if d["id"] == args.item), None)
            if item is None:
                raise Fail("REVIEW_UNKNOWN_ITEM", f"No suspected mis-transcription '{args.item}' in {t.rel}/build/cues-report.json.",
                           topic=t.id, hint="Run bcn cues first, or list the items with bcn review.")
            if args.clear:
                data = reviewfile.load(t)
                data["transcripts"].pop(args.item, None)
                reviewfile.save(t, data)
            else:
                reviewfile.decide(t, item, "accept" if args.accept else "correct", args.correct, args.by or getpass.getuser())
        decisions = reviewfile.load(t)["transcripts"]
        r.extra["items"] = [{**d, "review": decisions.get(d["id"])} for d in items]
        r.extra["unreviewed"] = sum(1 for d in items if d["id"] not in decisions)
        if report and not fsutil.is_fresh([t.cues_report], [t.src("en"), t.srt("en")]):
            r.extra["stale"] = True
        env.results.append(r)
