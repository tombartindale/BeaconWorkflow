"""bcn edit: replace a topic's script with edited text, safely, then validate it.

  bcn edit <topic> --from FILE [--lang zh] [--expect-sha SHA] [--dry-run]

--expect-sha is the SHA-256 of the file as the editor loaded it. If the file on
disk has changed since (a sync pull, another editor), nothing is written and the
edit is refused, so no one's changes are silently lost. --dry-run validates the
text without writing anything, for checking as you type.

The version being replaced is kept in the topic's .history/ folder (the last 30),
which is content rather than build output, so it survives build/ being cleared.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
from pathlib import Path

from .. import fsutil
from ..config import load
from ..envelope import Diagnostic, Envelope, Fail, TopicResult
from ..rules import validate_topic
from ..tree import Target

HELP = "save edited script text for a topic, then validate it"
KEEP_VERSIONS = 30


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--from", dest="source", required=True, help="file holding the edited text")
    p.add_argument("--expect-sha", help="SHA-256 of the file as it was when editing began")
    p.add_argument("--dry-run", action="store_true", help="validate the text; write nothing")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    if target.level != "topic":
        raise Fail("USAGE", "edit works on one topic directory.")
    t = target.topics[0]
    lang = args.lang
    src = t.src(lang)
    source = Path(args.source)
    if not source.is_file():
        raise Fail("FS_MISSING", f"{args.source} does not exist.")
    text = source.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    new_bytes = text.encode("utf-8")
    old_bytes = src.read_bytes() if src.is_file() else None
    current_sha = _sha(old_bytes) if old_bytes is not None else None

    r = TopicResult(t.id, t.rel)
    env.results.append(r)
    if args.expect_sha and current_sha and args.expect_sha != current_sha and not args.dry_run:
        r.ok = False
        r.extra["current_sha256"] = current_sha
        raise Fail("EDIT_CONFLICT", f"{src.name} has changed on disk since you started editing; nothing was written.",
                   topic=t.id, lang=lang, file=src.name,
                   hint="Reload to see the new version (your text is kept to copy from), or save again to overwrite it.")

    cfg = load(t.root, t.module_dir)
    parsed, diags = validate_topic(cfg, t, lang, text=text)
    r.diagnostics.extend(diags)
    if parsed is not None:
        v = cfg["validate"]
        try:
            minutes = int(parsed.front.get("minutes", 0))
        except ValueError:
            minutes = 0
        r.extra.update({"slides": len(parsed.slides), "words": parsed.narration_words,
                        "target_words": minutes * v["words_per_minute"] if lang == "en" else None})

    if args.dry_run:
        r.extra.update({"written": False, "sha256": current_sha})
    elif old_bytes == new_bytes:
        r.extra.update({"written": False, "sha256": current_sha})
    else:
        if old_bytes is not None:
            hist = t.dir / ".history"
            hist.mkdir(exist_ok=True)
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            fsutil.write_bytes(hist / f"{src.stem}.{stamp}{src.suffix}", old_bytes)
            for old in sorted(hist.glob(f"{src.stem}.*{src.suffix}"))[:-KEEP_VERSIONS]:
                old.unlink(missing_ok=True)
        fsutil.write_bytes(src, new_bytes)
        r.extra.update({"written": True, "sha256": _sha(new_bytes)})
        r.diagnostics.append(Diagnostic("EDIT_SAVED", f"Saved {src.name}.", topic=t.id, lang=lang, file=src.name))
        # Record the result as validate's, exactly as bcn validate would.
        vr = TopicResult(t.id, t.rel, diagnostics=[d for d in diags])
        vr.ok = not any(d.level == "error" for d in diags)
        vr.extra = {k: r.extra[k] for k in ("slides", "words", "target_words") if k in r.extra}
        fsutil.write_json(t.step_file("validate", lang), Envelope("validate", t.rel, t.root).topic_envelope(vr))
    # Saving succeeded even if the script still has problems: they are reported, not a failure to save.
    r.ok = True
    env.extra["validation_ok"] = not any(d.level == "error" for d in diags)
    env.extra["counts"] = {lvl: sum(1 for d in diags if d.level == lvl) for lvl in ("error", "warn", "info")}
    env.extra["diagnostics_are_validation"] = True
