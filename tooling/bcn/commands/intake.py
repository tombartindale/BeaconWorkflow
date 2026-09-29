"""bcn intake: place pasted topic markdown into the tree, then validate it.

Identify, check, place, validate. No editing. A paste may hold a whole unit;
it is split on topic front matter. A topic not in the course map is refused, and
an existing file that differs is never overwritten: the diff is returned instead.
"""

from __future__ import annotations

import argparse
import difflib
import re
from pathlib import Path

from .. import fsutil
from ..config import load
from ..envelope import Diagnostic, Envelope, Fail, TopicResult
from ..markdown import parse
from ..rules import validate_topic
from ..state import module_context
from ..tree import TOPIC_ID_RE, Target, Topic

HELP = "place pasted topic markdown into the tree and validate it"
FENCE = re.compile(r"^\s*(```|~~~)[\w-]*\s*$")


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--from", dest="source", required=True, help="file holding the pasted text")
    p.add_argument("--dry-run", action="store_true", help="check and report, write nothing")


def split_topics(text: str) -> list[tuple[int, str]]:
    """Split pasted text into (first line number, topic text) blocks on topic front matter."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    starts = []
    for i, line in enumerate(lines):
        if line.strip() != "---":
            continue
        # An opening front matter delimiter: closes within a few lines and names a topic_id.
        close = next((j for j in range(i + 1, min(i + 10, len(lines))) if lines[j].strip() == "---"), None)
        if close and any(re.match(r"^\s*topic_id\s*:", w) for w in lines[i + 1:close]):
            starts.append(i)
    blocks = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(lines)
        chunk = lines[s:e]
        # Claude often wraps each topic in a code fence; drop fences at the edges only.
        while chunk and (not chunk[-1].strip() or FENCE.match(chunk[-1])):
            chunk.pop()
        blocks.append((s + 1, "\n".join(chunk).rstrip() + "\n"))
    return blocks


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    src = Path(args.source)
    if not src.is_file():
        raise Fail("FS_MISSING", f"{args.source} does not exist.")
    text = src.read_text(encoding="utf-8-sig")
    blocks = split_topics(text)
    if not blocks:
        raise Fail("INTAKE_NO_TOPICS", "No topic front matter (--- then topic_id: ...) was found in the pasted text.",
                   hint="Paste the markdown exactly as Claude produced it, front matter included.")
    parts = target.rel.split("/") if target.rel != "." else []
    for first_line, block in blocks:
        p = parse(Path("pasted.md"), "pasted", text=block)
        tid = p.front.get("topic_id", "")
        r = TopicResult(tid or f"line {first_line}", target.rel)
        env.results.append(r)
        if not TOPIC_ID_RE.match(tid):
            r.diagnostics.append(Diagnostic("MD_TOPIC_ID_FORMAT", f"The topic starting at pasted line {first_line} has topic_id '{tid}'.",
                                            file="pasted", line=first_line))
            r.ok = False
            continue
        m, u, c = tid.split("-")
        t = Topic(target.root, m, u, c)
        r.rel = t.rel
        r.extra["path"] = f"{t.rel}/topic.md"
        if parts and (parts[0] != m or (len(parts) > 1 and parts[1] != u) or (len(parts) > 2 and parts[2] != c)):
            r.diagnostics.append(Diagnostic("INTAKE_NOT_IN_MAP", f"{tid} is outside {target.rel}, where this paste was aimed.", topic=tid))
            r.ok = False
            continue
        if not (target.root / m / "course-map.md").is_file() or module_context(target.root, m).course_map.find(u, c) is None:
            r.diagnostics.append(Diagnostic("INTAKE_NOT_IN_MAP", f"{tid} is not in {m}/course-map.md.", topic=tid,
                                            hint="Add it to the course map first, or check the topic_id."))
            r.extra["action"] = "refused"
            r.ok = False
            continue
        dest = t.src("en")
        if dest.exists():
            current = dest.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
            if current.rstrip() == block.rstrip():
                r.extra["action"] = "unchanged"
                r.diagnostics.append(Diagnostic("INTAKE_UNCHANGED", f"{t.rel}/topic.md already holds this text.", topic=tid))
            else:
                diff = "".join(difflib.unified_diff(current.splitlines(True), block.splitlines(True),
                                                    fromfile=f"{t.rel}/topic.md (on disk)", tofile="pasted"))
                r.extra["action"] = "exists_differs"
                r.extra["diff"] = diff
                r.diagnostics.append(Diagnostic("INTAKE_EXISTS_DIFFERS", f"{t.rel}/topic.md exists and differs from the paste; nothing was written.",
                                                topic=tid, file="topic.md", data={"diff": diff},
                                                hint="Review the diff. If the paste is right, edit or remove the file on disk yourself."))
                r.ok = False
                continue
        elif args.dry_run:
            r.extra["action"] = "would_write"
        else:
            fsutil.write_text(dest, block)
            r.extra["action"] = "written"
            r.diagnostics.append(Diagnostic("INTAKE_WRITTEN", f"Wrote {t.rel}/topic.md.", topic=tid, file="topic.md"))
        # Validate in place, exactly as bcn validate would, and record it as that step's result.
        if dest.exists():
            cfg = load(t.root, t.module_dir)
            _, diags = validate_topic(cfg, t, "en")
            vr = TopicResult(tid, t.rel, diagnostics=diags)
            vr.ok = not any(d.level == "error" for d in diags)
            if not args.dry_run:
                venv = Envelope("validate", t.rel, t.root)
                fsutil.write_json(t.step_file("validate", "en"), venv.topic_envelope(vr))
            r.extra["validate_ok"] = vr.ok
            r.diagnostics.extend(diags)
        r.ok = not any(d.level == "error" for d in r.diagnostics)
    env.extra["topics_found"] = len(blocks)
