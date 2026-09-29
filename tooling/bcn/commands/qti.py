"""bcn qti: each unit's quiz as a QTI 2.1 package, to import into an LMS.

Reads <module>/<unit>/activity.md where its front matter says type: quiz (the
format is in bcn/quiz.py) and writes <module>/build/qti/<module>-<unit>-quiz.zip.
A quiz with errors (a question with no correct answer, fewer than two options)
gets no package, so a broken quiz never reaches students. Missing feedback is a
warning. A package newer than its activity.md is skipped unless --force.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import fsutil
from ..envelope import Diagnostic, Envelope, Fail, TopicResult
from ..quiz import parse_quiz, qti_package
from ..tree import UNIT_RE, Target

HELP = "export unit quizzes as QTI 2.1 packages for an LMS"


def add_args(p: argparse.ArgumentParser) -> None:
    pass


def package_path(root: Path, module: str, unit: str) -> Path:
    return root / module / "build" / "qti" / f"{module}-{unit}-quiz.zip"


def _units(target: Target) -> list[tuple[str, str]]:
    if target.level in ("unit", "topic"):
        m, u = target.rel.split("/")[:2]
        return [(m, u)]
    return [(m, p.parent.name) for m in target.modules
            for p in sorted((target.root / m).glob("U*/activity.md")) if UNIT_RE.match(p.parent.name)]


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    if args.lang != "en":
        raise Fail("USAGE", "Quizzes are exported from the English activity.md only.")
    env.diagnostics.extend(target.diagnostics)
    for m, u in _units(target):
        src = target.root / m / u / "activity.md"
        rel = f"{m}/{u}/activity.md"
        r = TopicResult(f"{m}-{u}", f"{m}/{u}")
        if not src.is_file():
            if target.level in ("unit", "topic"):
                r.diagnostics.append(Diagnostic("FS_MISSING", f"{rel} does not exist.", file=rel))
                r.ok = False
                env.results.append(r)
            continue
        quiz = parse_quiz(src, rel)
        if quiz.front.get("type") != "quiz":
            # Tasks and other activities are not quizzes; only say so when asked about this unit.
            if target.level in ("unit", "topic"):
                r.diagnostics.append(Diagnostic("QUIZ_NOT_A_QUIZ", f"{rel} is not a quiz (type: {quiz.front.get('type') or 'none'}).",
                                                file=rel, hint="Only activities with type: quiz in their front matter are exported."))
                env.results.append(r)
            continue
        out = package_path(target.root, m, u)
        r.extra.update({"title": quiz.title, "questions": len(quiz.questions),
                        "multiple_response": sum(q.multiple for q in quiz.questions),
                        "package": str(out.relative_to(target.root))})
        if not args.force and fsutil.is_fresh([out], [src]):
            r.skipped = True
            r.diagnostics.extend(d for d in quiz.diagnostics if d.level != "error")
            env.results.append(r)
            continue
        r.diagnostics.extend(quiz.diagnostics)
        if any(d.level == "error" for d in r.diagnostics):
            r.ok = False
            r.extra["package"] = None
            out.unlink(missing_ok=True)  # an old package would no longer match the quiz
        else:
            fsutil.write_bytes(out, qti_package(quiz, f"{m}-{u}-quiz", quiz.front.get("lang") or "en"))
            r.artifacts.append(Envelope.artifact_for(target.root, out, "qti"))
        env.results.append(r)
    if not env.results and not env.diagnostics:
        env.diagnostics.append(Diagnostic("QUIZ_NOT_A_QUIZ", f"No unit under {target.rel} has a quiz activity.md.", level="info"))
