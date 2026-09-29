"""bcn ack: acknowledge a validation finding as deliberate, so it stops blocking.

  bcn ack <topic>                                   list findings and acknowledgements
  bcn ack <topic> --fingerprint FP [--note TEXT]    acknowledge one finding
  bcn ack <topic> --fingerprint FP --clear          withdraw an acknowledgement

Only editorial checks can be acknowledged (dates, forbidden words, deictic
phrases, word counts, title length); never structural ones. The record goes in
the topic's review.json with who, when and why. It is tied to the finding's
text, so if the script changes and the text goes, the acknowledgement lapses.
The topic is re-validated straight away.
"""

from __future__ import annotations

import argparse
import getpass

from .. import reviewfile
from ..config import Config
from ..envelope import Envelope, Fail, TopicResult
from ..progress import TopicProgress
from ..rules import ACKABLE, validate_topic
from ..runner import run_topics
from ..tree import Target, Topic
from . import validate as validate_cmd

HELP = "acknowledge a validation finding as deliberate"


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--fingerprint", help="the finding's data.fingerprint, from validate or diagnostics")
    p.add_argument("--note", help="why it is acceptable (recorded)")
    p.add_argument("--clear", action="store_true", help="withdraw the acknowledgement")
    p.add_argument("--by", default=None, help="who is acknowledging (default: login name)")


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    if target.level != "topic":
        raise Fail("USAGE", "ack works on one topic directory.")
    t = target.topics[0]
    fp = args.fingerprint
    lang = "en"
    if fp:
        parts = fp.split("|")
        if len(parts) < 4 or parts[0] not in ACKABLE:
            raise Fail("USAGE", f"'{fp}' is not the fingerprint of a finding that can be acknowledged.",
                       hint=f"Acknowledgeable checks: {', '.join(sorted(ACKABLE))}.")
        lang = parts[1] if parts[1] in ("en", "zh") else "en"
        if args.clear:
            if not reviewfile.unacknowledge(t, fp):
                raise Fail("USAGE", "There is no such acknowledgement to withdraw.")
        else:
            from ..config import load
            _, current = validate_topic(load(t.root, t.module_dir), t, lang)
            finding = next((d for d in current if (d.data or {}).get("fingerprint") == fp), None)
            if finding is None:
                raise Fail("USAGE", "That finding is not in the current script (it may already have been fixed).",
                           hint="Run bcn validate and use a fingerprint from its output.")
            reviewfile.acknowledge(t, fp, {"code": finding.code, "message": finding.message}, args.note,
                                   args.by or getpass.getuser())

    # Re-validate so the step result, and with it the topic's stage, reflects the decision.
    v_env = Envelope("validate", t.rel, t.root)

    def fn(tt: Topic, r: TopicResult, tp: TopicProgress, cfg: Config) -> None:
        validate_cmd.check_topic(tt, r, tp, cfg, lang)

    run_topics(v_env, Target(target.root, target.path, "topic", t.rel, [t.module], [t], []), "validate", lang, fn)
    r = v_env.results[0]
    env.results.append(r)
    env.extra["acknowledged"] = reviewfile.load(t)["acknowledged"]
