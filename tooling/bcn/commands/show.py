"""bcn show: everything the topic page needs about one topic, parsed by the CLI.

The UI never parses markdown, so the script preview (slide content as HTML,
narration, word counts against the target), cue boundaries, overflow flags and
media paths all come from here. Read-only; writes nothing.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import cuesheet, fsutil, reviewfile
from ..config import load, load_theme, resolve_theme_name
from ..envelope import Envelope, Fail, TopicResult
from ..markdown import parse, to_html
from ..tree import Target, Topic

HELP = "one topic's parsed script, cues and media, for the topic page"


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--asset-prefix", default="", help="prefix for image URLs in the rendered HTML")


def _slides(t: Topic, lang: str, prefix: str, cfg: Any) -> dict[str, Any] | None:
    src = t.src(lang)
    if not src.is_file():
        return None
    p = parse(src, src.name, t.id)
    v = cfg["validate"]
    base = f"{prefix}{t.rel}/"
    out = []
    running = 0
    for s in p.slides:
        running += s.say_words
        words = s.say_words
        out.append({
            "index": s.index,
            "title": s.title,
            "line": s.start_line,
            "content_html": to_html(s.content, base),
            "narration": s.say_text,
            "words": words,
            "running_words": running,
            "words_ok": (v["narration_min_words"] <= words <= v["narration_max_words"]) if lang == "en" else None,
            "images": [{"alt": i.alt, "path": f"{t.rel}/{i.path}"} for i in s.images],
        })
    try:
        minutes = int(p.front.get("minutes", 0))
    except ValueError:
        minutes = 0
    target = minutes * v["words_per_minute"]
    tol = v["word_tolerance"]
    return {
        "front": p.front,
        "slides": out,
        "words": p.narration_words,
        "target_words": target,
        "range": [int(target * (1 - tol)), int(target * (1 + tol))],
        "words_ok": (abs(p.narration_words - target) <= target * tol) if (lang == "en" and target) else None,
        "words_per_minute": v["words_per_minute"],
        "narration_limits": [v["narration_min_words"], v["narration_max_words"]],
    }


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    if target.level != "topic":
        raise Fail("USAGE", "show needs a single topic directory.")
    t = target.topics[0]
    cfg = load(t.root, t.module_dir)
    r = TopicResult(t.id, t.rel)
    rel = lambda p: str(p.relative_to(t.root)) if p.is_file() else None  # noqa: E731

    data: dict[str, Any] = {"en": _slides(t, "en", args.asset_prefix, cfg), "zh": _slides(t, "zh", args.asset_prefix, cfg)}

    # Cues: the sheet plus the confidence and reason for each boundary.
    cues = []
    if t.cues_csv.is_file():
        try:
            times = cuesheet.read(t.cues_csv, None, t.cues_csv.name)
        except Fail:
            times = []
        report = fsutil.read_json(t.cues_report) or {}
        by_slide = {b["slide"]: b for b in report.get("boundaries", [])}
        for i, tc in enumerate(times, 1):
            b = by_slide.get(i, {})
            cues.append({"slide": i, "time": tc, "confidence": b.get("confidence"), "source": b.get("source"),
                         "reason": b.get("reason")})
    data["cues"] = cues
    data["cue_threshold"] = cfg["cues"]["min_confidence"]

    # Render facts: overflow flags per language, and the theme geometry for the safe-area overlay.
    render = {}
    for lang in ("en", "zh"):
        env_ = fsutil.read_json(t.step_file("render", lang)) or {}
        flagged = {}
        for d in env_.get("diagnostics", []):
            if d.get("code") in ("RENDER_OVERFLOW", "RENDER_SAFE_AREA") and d.get("slide"):
                flagged.setdefault(str(d["slide"]), []).append({"code": d["code"], "level": d["level"], "message": d["message"]})
        render[lang] = {"slides": [str(p.relative_to(t.root)) for p in t.slide_pngs(lang)], "flagged": flagged,
                        "pdf": rel(t.deck_pdf(lang))}
    try:
        theme = load_theme(t.root, resolve_theme_name(cfg, args.theme))
        render["theme"] = {"name": theme.name, "width": theme.width, "height": theme.height, "safe_bottom": theme.safe_bottom}
    except Fail:
        render["theme"] = None
    data["render"] = render

    # A draft with an intro bumper starts the body later; the player shifts cue markers by this.
    offsets = {lang: (((fsutil.read_json(t.step_file("compose", lang)) or {}).get("results") or [{}])[0].get("body_offset") or 0.0)
               for lang in ("en", "zh")}
    data["media"] = {
        "master": rel(t.video),
        "draft": {"en": rel(t.draft("en")), "zh": rel(t.draft("zh"))},
        "draft_offset": offsets,
        "subtitles": {
            "en": rel(t.subtitle_out("en", "srt")) or rel(t.srt("en")),
            "zh": rel(t.subtitle_out("zh", "srt")) or rel(t.srt("zh")),
        },
    }
    rv = reviewfile.load(t) if t.review_file.is_file() else {"cue_overrides": {}, "transcripts": {}}
    data["review"] = rv
    r.extra.update(data)
    env.results.append(r)
