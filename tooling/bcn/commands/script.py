"""bcn script: the narration as a PDF to read from while recording.

Just the words the presenter says, as paragraphs in a large, plain sans-serif,
with a clear divider between slides so the reader can keep their place.
Written to build/script.en.pdf, with build/script.en.html (open or paste it into a
teleprompter such as QPrompt) and build/script.en.txt beside it. Needs only topic.md, not a passing validate:
a script with acknowledged findings is still a script to record from.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil

from .. import fsutil, tools
from ..config import Config, load_theme, resolve_theme_name
from ..envelope import Envelope, Fail, TopicResult
from ..markdown import parse
from ..progress import TopicProgress, run as run_proc
from ..runner import require_input, run_topics, try_skip
from ..tree import Target, Topic

HELP = "the narration as a PDF for recording"

CSS = """
@page { size: A4; }
html { -webkit-text-size-adjust: none; }
body { color: #111; margin: 0; }
header { border-bottom: 2px solid #222; padding-bottom: 10pt; margin-bottom: 18pt; }
header .id { font-size: 11pt; color: #555; letter-spacing: .5pt; }
header h1 { font-size: 22pt; margin: 4pt 0 6pt; line-height: 1.2; font-weight: var(--title-weight); }
header .meta { font-size: 11pt; color: #555; }
.slide { margin: 0 0 8pt; }
.divider { display: flex; align-items: center; gap: 10pt; margin: 22pt 0 12pt; break-after: avoid; page-break-after: avoid;
           font-size: 11pt; font-weight: 600; color: #666; text-transform: uppercase; letter-spacing: 1pt; }
.divider::after { content: ""; flex: 1; border-top: 1.5pt solid #bbb; }
.slide:first-of-type .divider { margin-top: 0; }
p { font-size: 20pt; line-height: 1.6; margin: 0 0 14pt; orphans: 3; widows: 3; }
em { font-style: italic; }
strong { font-weight: 700; }
.empty { font-size: 14pt; color: #a00; font-style: italic; }
"""


def _inline(text: str) -> str:
    s = html.escape(text, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<em>\1</em>", s)
    s = re.sub(r"(?<![_\w])_(?!\s)(.+?)(?<!\s)_(?![_\w])", r"<em>\1</em>", s)
    return s


def script_text(p) -> str:
    out = [p.front.get("title", ""), ""]
    for s in p.slides:
        out.append(f"— SLIDE {s.index}{' · ' + s.title if s.title else ''} —")
        out.append("")
        for para in s.say_paragraphs or ([s.say_text] if s.say_text else []):
            out += [re.sub(r"\*\*(.+?)\*\*|\*(.+?)\*|_(.+?)_", lambda m: next(g for g in m.groups() if g), para), ""]
    return "\n".join(out).rstrip() + "\n"


def theme_css(theme) -> str:
    """The theme's own faces and English stack, so the script is set in the house font."""
    from .bumpers import _font_stack, font_faces_css
    return (font_faces_css(theme) + f"body {{ font-family: {_font_stack(theme.fonts['en'])}; }}\n"
            f":root {{ --title-weight: {theme.title_weight}; }}\n")


def script_html(t: Topic, p, teleprompter: bool = False, theme=None) -> str:
    """The printable page, or (teleprompter=True) plain semantic HTML that pastes cleanly into a prompter."""
    if teleprompter:
        body = [f"<h1>{html.escape(p.front.get('title', ''))}</h1>"]
        for s in p.slides:
            body.append(f"<h2>SLIDE {s.index}{' · ' + html.escape(s.title) if s.title else ''}</h2>")
            body += [f"<p>{_inline(para)}</p>" for para in (s.say_paragraphs or ([s.say_text] if s.say_text else []))]
        return f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{html.escape(t.id)}</title></head><body>\n" + \
            "\n".join(body) + "\n</body></html>\n"
    title = p.front.get("title", "")
    words = p.narration_words
    minutes = p.front.get("minutes", "")
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{html.escape(t.id)}</title>",
             f"<style>{CSS}{theme_css(theme) if theme else ''}</style></head><body>",
             "<header>",
             f"<div class='id'>{html.escape(t.id)}</div>",
             f"<h1>{html.escape(title)}</h1>",
             f"<div class='meta'>{len(p.slides)} slides · {words} words · {html.escape(str(minutes))} minutes planned</div>",
             "</header>"]
    for s in p.slides:
        label = f"Slide {s.index}" + (f" · {s.title}" if s.title else "")
        parts.append(f"<section class='slide'><div class='divider'>{html.escape(label)}</div>")
        paras = s.say_paragraphs or ([s.say_text] if s.say_text else [])
        if paras:
            parts += [f"<p>{_inline(para)}</p>" for para in paras]
        else:
            parts.append("<p class='empty'>(no narration on this slide)</p>")
        parts.append("</section>")
    parts.append("</body></html>")
    return "\n".join(parts)


def add_args(p: argparse.ArgumentParser) -> None:
    pass


def script_topic(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config, force: bool) -> None:
    src = t.src("en")
    require_input(t, src, "en")
    out = t.build / "script.en.pdf"
    theme_files = load_theme(t.root, resolve_theme_name(cfg, None)).files()
    if try_skip(t, r, "script", "en", [out, t.build / "script.en.html", t.build / "script.en.txt"], [src, *theme_files], force):
        return
    tl = tools.require(cfg, "marp", "chrome")
    p = parse(src, "topic.md", t.id)
    if not p.slides:
        raise Fail("MD_NO_SLIDES", "topic.md has no slides to print.", file="topic.md")
    work = t.build / ".script"
    work.mkdir(parents=True, exist_ok=True)
    try:
        page = work / "script.html"
        theme = load_theme(t.root, resolve_theme_name(cfg, None))
        page.write_text(script_html(t, p, theme=theme), encoding="utf-8")
        tp.update(20, "printing")
        with fsutil.atomic_path(out) as tmp:
            code, _, err = run_proc([tl.node or "node", str(tools.NODE_DIR / "print.mjs"), str(page), str(tmp),
                                     f"{t.id} · {p.front.get('title', '')}"],
                                    env={"CHROME_PATH": tl.chrome or ""}, cwd=str(tools.NODE_DIR), timeout=180)
            if code != 0 or not tmp.stat().st_size:
                raise Fail("RENDER_FAILED", f"Printing the script failed (exit {code}).", file="topic.md",
                           data={"stderr": err[-2000:]})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    # Teleprompter copies: the same words as formatted text and as plain text.
    fsutil.write_text(t.build / "script.en.html", script_html(t, p, teleprompter=True))
    fsutil.write_text(t.build / "script.en.txt", script_text(p))
    for f in (out, t.build / "script.en.html", t.build / "script.en.txt"):
        r.artifacts.append(Envelope.artifact_for(t.root, f, "script"))
    r.extra.update({"slides": len(p.slides), "words": p.narration_words, "pdf": str(out.relative_to(t.root))})
    tp.update(100, "done")


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    if args.lang != "en":
        raise Fail("USAGE", "The recording script is English only: the Mandarin sources carry no narration.")

    def fn(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config) -> None:
        script_topic(t, r, tp, cfg, args.force)

    run_topics(env, target, "script", "en", fn, jobs=args.jobs)
