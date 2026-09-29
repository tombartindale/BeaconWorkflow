"""bcn diagnostics: every outstanding diagnostic beneath the path.

Collected from step results that are still current (a result older than its
inputs no longer describes the files), plus problems in the tree itself and in
the module documents. Read-only; the UI groups these by code.
"""

from __future__ import annotations

import argparse

from .. import fsutil, reviewfile
from ..envelope import Diagnostic, Envelope
from ..state import TopicState, all_topics, module_context
from ..tree import Target

HELP = "every outstanding diagnostic beneath the path"


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--level", choices=["error", "warn", "info"], default="info", help="lowest level to include")


def _diag(d: dict, step: str | None) -> Diagnostic:
    data = dict(d.get("data") or {})
    if step:
        data["step"] = step
    return Diagnostic(d["code"], d["message"], level=d.get("level"), topic=d.get("topic"), file=d.get("file"),
                      line=d.get("line"), slide=d.get("slide"), hint=d.get("hint"), lang=d.get("lang"), data=data or None)


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    order = {"error": 0, "warn": 1, "info": 2}
    floor = order[args.level]
    out: list[Diagnostic] = list(target.diagnostics)
    for m in target.modules if target.level in ("root", "module") else []:
        mv = fsutil.read_json(target.root / m / "build" / "validate.json") or {}
        out += [_diag(d, "validate") for d in mv.get("diagnostics", [])]
        qa = fsutil.read_json(target.root / m / "build" / "qa.json") or {}
        out += [_diag(d, "qa") for d in qa.get("diagnostics", []) if d.get("code", "").startswith("QA_") and not d.get("topic")]
    for t in all_topics(target.root, target.modules, target.topics, target.level, target.rel):
        ctx = module_context(t.root, t.module)
        st = TopicState(t, ctx)
        st.compute()
        decisions = reviewfile.load(t)["transcripts"] if t.review_file.is_file() else {}
        out += [d for d in st.scan if d.level != "info" or floor >= 2]
        for (step, lang), s in st.steps.items():
            if not s.exists or not s.fresh:
                continue
            for d in (s.env or {}).get("diagnostics", []):
                d = dict(d)
                d.setdefault("topic", t.id)
                d["lang"] = d.get("lang") or lang
                if d.get("code") == "CUE_MISTRANSCRIPTION" and d.get("data"):
                    dec = decisions.get(d["data"].get("id"))
                    d["data"] = {**d["data"], "review": dec}
                    d["level"] = "info" if dec else "warn"
                out.append(_diag(d, step))
    env.diagnostics.extend(x for x in out if order.get(x.level or "info", 2) <= floor)
    # Reporting outstanding problems is this command succeeding, not failing.
    env.extra["counts"] = {lvl: sum(1 for x in env.diagnostics if x.level == lvl) for lvl in order}
    env.extra["outstanding"] = [x.to_json() for x in env.diagnostics]
    env.diagnostics = []
