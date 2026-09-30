"""bcn coursemap: the module's course-map.md as a PDF, to share with collaborators.

course-map.md is deliberately plain markdown (see ../coursemap.py): headings, bold
labels, bullet/numbered lists and pipe tables. This renders exactly that subset
to styled HTML and prints it with the pinned Chrome (the same tool bcn script
uses), rather than pulling in a general markdown library. Written to
<module>/build/course-map.pdf; skipped if already newer than course-map.md.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil

from .. import fsutil, tools
from ..config import load, load_theme, resolve_theme_name
from ..coursemap import load_course_map
from ..envelope import Diagnostic, Envelope, Fail, TopicResult
from ..progress import run as run_proc
from ..tree import Target
from .bumpers import _font_stack, font_faces_css
from .status import _module_title

HELP = "the module's course map as a PDF"

CSS = """
@page { size: A4; }
html { -webkit-text-size-adjust: none; }
body { color: #111; margin: 0; }
header { border-bottom: 2px solid #222; padding-bottom: 10pt; margin-bottom: 18pt; }
header .row { display: flex; align-items: flex-start; justify-content: space-between; gap: 16pt; }
header .id { font-size: 11pt; color: #555; letter-spacing: .5pt; }
header h1 { font-size: 22pt; margin: 4pt 0 6pt; line-height: 1.2; font-weight: var(--title-weight); }
header img.logo { display: block; }
h2 { font-size: 15pt; margin: 20pt 0 8pt; break-after: avoid; page-break-after: avoid; }
h2:first-of-type { margin-top: 0; }
p { font-size: 10.5pt; line-height: 1.5; margin: 0 0 8pt; orphans: 3; widows: 3; }
strong { font-weight: 700; }
em { font-style: italic; }
table { width: 100%; border-collapse: collapse; margin: 0 0 10pt; font-size: 9.5pt; }
th, td { border: 1px solid #ccc; padding: 4pt 6pt; text-align: left; vertical-align: top; }
th { background: #f0f0f0; font-weight: 600; }
ul, ol { margin: 0 0 10pt; padding-left: 18pt; font-size: 10pt; line-height: 1.45; }
li { margin: 0 0 4pt; }
"""


def _inline(text: str) -> str:
    s = html.escape(text, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<em>\1</em>", s)
    s = re.sub(r"(?<![_\w])_(?!\s)(.+?)(?<!\s)_(?![_\w])", r"<em>\1</em>", s)
    return s


def _table(rows: list[str]) -> str:
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    head, body = cells[0], cells[2:]  # row 1 is the separator
    out = ["<table><thead><tr>"] + [f"<th>{_inline(c)}</th>" for c in head] + ["</tr></thead><tbody>"]
    for r in body:
        out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def coursemap_html(module: str, title: str | None, lines: list[str], theme=None) -> str:
    parts: list[str] = []
    i, n = 0, len(lines)
    list_tag: str | None = None
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        m = re.match(r"^(#{1,2})\s+(.*)$", stripped)
        if m:
            if list_tag:
                parts.append(f"</{list_tag}>")
                list_tag = None
            level = len(m.group(1))
            if level == 1:
                parts.append(f"<h1>{_inline(m.group(2))}</h1>")
            else:
                parts.append(f"<h2>{_inline(m.group(2))}</h2>")
            i += 1
            continue
        if stripped.startswith("|"):
            j = i
            while j < n and lines[j].strip().startswith("|"):
                j += 1
            if list_tag:
                parts.append(f"</{list_tag}>")
                list_tag = None
            parts.append(_table([lines[k].strip() for k in range(i, j)]))
            i = j
            continue
        item = re.match(r"^([-*]|\d+\.)\s+(.*)$", stripped)
        if item:
            tag = "ol" if item.group(1)[0].isdigit() else "ul"
            if list_tag != tag:
                if list_tag:
                    parts.append(f"</{list_tag}>")
                parts.append(f"<{tag}>")
                list_tag = tag
            parts.append(f"<li>{_inline(item.group(2))}</li>")
            i += 1
            continue
        if list_tag:
            parts.append(f"</{list_tag}>")
            list_tag = None
        parts.append(f"<p>{_inline(stripped)}</p>")
        i += 1
    if list_tag:
        parts.append(f"</{list_tag}>")
    theme_css = ""
    if theme:
        theme_css = font_faces_css(theme) + f"body {{ font-family: {_font_stack(theme.fonts['en'])}; }}\n" \
            f":root {{ --title-weight: {theme.title_weight}; }}\n"
    heading = f"{module}{' — ' + title if title else ''}"
    logo = ""
    if theme and theme.document["logo"]:
        logo = f"<img class='logo' src='{theme.asset(theme.document['logo']).as_uri()}' alt='' style='height: {theme.document['logo_height']:.0f}pt'>"
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{html.escape(heading)} course map</title>"
            f"<style>{CSS}{theme_css}</style></head><body>"
            f"<header><div class='row'><div><div class='id'>{html.escape(module)}</div>"
            f"<h1>Course map{' — ' + html.escape(title) if title else ''}</h1></div>{logo}</div></header>"
            + "".join(parts) + "</body></html>")


def add_args(p: argparse.ArgumentParser) -> None:
    pass


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    for m in target.modules:
        rel = f"{m}/course-map.md"
        src = target.root / rel
        mdir = target.root / m
        r = TopicResult(m, m)
        if not src.is_file():
            r.diagnostics.append(Diagnostic("DOC_MISSING", f"{rel} does not exist.", file=rel))
            r.ok = False
            env.results.append(r)
            continue
        out = mdir / "build" / "course-map.pdf"
        try:
            cfg = load(target.root, mdir)
            theme = load_theme(target.root, resolve_theme_name(cfg, args.theme))
        except Fail as f:
            r.diagnostics.append(f.diagnostic)
            r.ok = False
            env.results.append(r)
            continue
        if not args.force and fsutil.is_fresh([out], [src, *theme.files(), *theme.document_media()]):
            r.skipped = True
            r.extra["pdf"] = str(out.relative_to(target.root))
            env.results.append(r)
            continue
        try:
            tl = tools.require(cfg, "marp", "chrome")
            cm = load_course_map(mdir, cfg["documents"]["course_map_headings"])
            title = _module_title(src)
            lines = src.read_text(encoding="utf-8-sig").splitlines()
            work = mdir / "build" / ".coursemap-pdf"
            work.mkdir(parents=True, exist_ok=True)
            try:
                page = work / "course-map.html"
                page.write_text(coursemap_html(m, title, lines, theme=theme), encoding="utf-8")
                with fsutil.atomic_path(out) as tmp:
                    code, _, err = run_proc([tl.node or "node", str(tools.NODE_DIR / "print.mjs"), str(page), str(tmp),
                                             f"{m} course map"], env={"CHROME_PATH": tl.chrome or ""},
                                            cwd=str(tools.NODE_DIR), timeout=180)
                    if code != 0 or not tmp.stat().st_size:
                        raise Fail("RENDER_FAILED", f"Printing the course map failed (exit {code}).", file=rel,
                                   data={"stderr": err[-2000:]})
            finally:
                shutil.rmtree(work, ignore_errors=True)
        except Fail as f:
            r.diagnostics.append(f.diagnostic)
            r.ok = False
            env.results.append(r)
            continue
        r.artifacts.append(Envelope.artifact_for(target.root, out, "coursemap"))
        r.extra["pdf"] = str(out.relative_to(target.root))
        r.diagnostics.extend(d for d in cm.diagnostics if d.level == "error")
        env.results.append(r)
