"""Parser for topic.md and topic.<lang>.md.

Collects every structural problem with its line number rather than stopping at
the first. The same parse feeds validate, render (narration stripped), cues
(narration extracted), package (narration stripped) and show.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from .envelope import Diagnostic

FRONT_KEYS = ("topic_id", "title", "minutes", "lang")
SAY_RE = re.compile(r"^>\s?\*\*Say:\*\*\s*(.*)$")
BREAK_RE = re.compile(r"^---\s*$")
FENCE_RE = re.compile(r"^(```|~~~)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?")


@dataclass
class Image:
    alt: str
    path: str
    line: int


@dataclass
class Slide:
    index: int
    start_line: int
    end_line: int
    lines: list[tuple[int, str]]  # content lines, narration removed
    title: str | None = None
    title_line: int | None = None
    heading_level: int | None = None
    say_blocks: int = 0
    say_line: int | None = None
    say_text: str = ""
    images: list[Image] = field(default_factory=list)
    list_items: int = 0

    @property
    def content(self) -> str:
        return "\n".join(text for _, text in self.lines).strip("\n")

    @property
    def say_words(self) -> int:
        return len(WORD_RE.findall(self.say_text))

    def signature(self) -> dict[str, object]:
        return {"heading_level": self.heading_level, "images": [i.path for i in self.images], "list_items": self.list_items}


@dataclass
class ParsedTopic:
    path: Path
    front: dict[str, str]
    front_lines: dict[str, int]
    slides: list[Slide]
    diagnostics: list[Diagnostic]
    body_start: int  # 1-based line number where the body begins
    raw_lines: list[str]

    @property
    def narration_words(self) -> int:
        return sum(s.say_words for s in self.slides)

    def stripped_body(self) -> str:
        """The body with narration removed and breaks preserved: what the slides show."""
        return "\n\n---\n\n".join(s.content for s in self.slides).strip() + "\n"

    def stripped_file(self) -> str:
        """The whole file minus narration, front matter kept. The delivery copy."""
        fm = "\n".join(self.raw_lines[: self.body_start - 1]).rstrip("\n")
        return fm + "\n\n" + self.stripped_body()


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def parse(path: Path, rel: str | None = None, topic_id: str | None = None, text: str | None = None) -> ParsedTopic:
    rel = rel or path.name
    if text is None:
        text = path.read_text(encoding="utf-8-sig")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    diags: list[Diagnostic] = []

    def d(code: str, msg: str, line: int | None, slide: int | None = None, hint: str | None = None) -> None:
        diags.append(Diagnostic(code, msg, topic=topic_id, file=rel, line=line, slide=slide, hint=hint))

    front: dict[str, str] = {}
    front_lines: dict[str, int] = {}
    body_start = 1
    if not lines or lines[0].strip() != "---":
        d("MD_FRONT_MATTER_MISSING", "The file must begin with a --- front matter block.", 1,
          hint="Add topic_id, title, minutes and lang between --- lines at the top.")
    else:
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is None:
            d("MD_FRONT_MATTER_MISSING", "Front matter block is never closed with ---.", 1)
        else:
            for i in range(1, end):
                raw = lines[i]
                if not raw.strip() or raw.lstrip().startswith("#"):
                    continue
                if ":" not in raw:
                    d("MD_FRONT_MATTER_VALUE", f"Front matter line is not 'key: value': {raw.strip()!r}.", i + 1)
                    continue
                k, v = raw.split(":", 1)
                k = k.strip()
                if k in front:
                    d("MD_FRONT_MATTER_KEYS", f"Front matter key '{k}' appears twice.", i + 1)
                front[k] = _unquote(v)
                front_lines[k] = i + 1
            body_start = end + 2
            extra = [k for k in front if k not in FRONT_KEYS]
            missing = [k for k in FRONT_KEYS if k not in front]
            for k in extra:
                d("MD_FRONT_MATTER_KEYS", f"Front matter key '{k}' is not allowed.", front_lines[k],
                  hint="Front matter holds exactly topic_id, title, minutes and lang.")
            if missing:
                d("MD_FRONT_MATTER_KEYS", f"Front matter is missing {', '.join(missing)}.", 1,
                  hint="Front matter holds exactly topic_id, title, minutes and lang.")

    # Split the body into slides on --- at column zero, outside code fences.
    chunks: list[list[tuple[int, str]]] = [[]]
    in_fence = False
    for i in range(body_start - 1, len(lines)):
        line = lines[i]
        if FENCE_RE.match(line):
            in_fence = not in_fence
        if not in_fence and BREAK_RE.match(line):
            chunks.append([])
            continue
        chunks[-1].append((i + 1, line))

    slides: list[Slide] = []
    for chunk in chunks:
        if not any(t.strip() for _, t in chunk):
            if chunk and slides:
                # An empty slide between two breaks is a structural fault, not whitespace.
                pass
            continue
        idx = len(slides) + 1
        slide = Slide(idx, chunk[0][0], chunk[-1][0], [])
        say_parts: list[str] = []
        state = "content"  # content | say | after_say
        after_say_line = None
        in_fence = False
        for ln, t in chunk:
            if FENCE_RE.match(t):
                in_fence = not in_fence
            m = SAY_RE.match(t) if not in_fence else None
            if m:
                slide.say_blocks += 1
                if slide.say_blocks == 1:
                    slide.say_line = ln
                    say_parts.append(m.group(1))
                else:
                    d("MD_SAY_MULTIPLE", f"Slide {idx} has more than one Say block.", ln, idx,
                      hint="Merge the narration into one > **Say:** block at the end of the slide.")
                state = "say"
                continue
            if state == "say":
                if t.startswith(">"):
                    if slide.say_blocks == 1:
                        say_parts.append(t[1:].strip())
                    continue
                state = "after_say"
            if state == "after_say" and t.strip():
                if after_say_line is None:
                    after_say_line = ln
            slide.lines.append((ln, t))
            if in_fence:
                continue
            h = HEADING_RE.match(t)
            if h and slide.title is None:
                slide.title, slide.title_line, slide.heading_level = h.group(2).strip(), ln, len(h.group(1))
            if LIST_RE.match(t):
                slide.list_items += 1
            for im in IMAGE_RE.finditer(t):
                slide.images.append(Image(im.group(1).strip(), im.group(2), ln))
        if after_say_line is not None:
            d("MD_SAY_NOT_LAST", f"Slide {idx} has content after its Say block.", after_say_line, idx,
              hint="The Say block must be the last thing on the slide.")
        # Trim trailing blank lines left where narration was removed.
        while slide.lines and not slide.lines[-1][1].strip():
            slide.lines.pop()
        while slide.lines and not slide.lines[0][1].strip():
            slide.lines.pop(0)
        slide.say_text = " ".join(p for p in say_parts if p).strip()
        slides.append(slide)

    return ParsedTopic(path, front, front_lines, slides, diags, body_start, lines)


# -- minimal markdown to HTML, for the script preview ------------------------------

_INLINE = [
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])"), r"<em>\1</em>"),
    (re.compile(r"(?<![_\w])_(?!\s)(.+?)(?<!\s)_(?![_\w])"), r"<em>\1</em>"),
]


def _inline(text: str, image_base: str) -> str:
    out = []
    pos = 0
    for m in re.finditer(r"!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?\s*\)|\[([^\]]+)\]\(([^)\s]+)\)", text):
        out.append(_inline_plain(text[pos:m.start()]))
        if m.group(2) is not None:
            src = m.group(2)
            if not re.match(r"^[a-z]+:", src):
                src = image_base + src
            out.append(f'<img src="{html.escape(src)}" alt="{html.escape(m.group(1))}">')
        else:
            out.append(f'<a href="{html.escape(m.group(4))}">{_inline_plain(m.group(3))}</a>')
        pos = m.end()
    out.append(_inline_plain(text[pos:]))
    return "".join(out)


def _inline_plain(text: str) -> str:
    s = html.escape(text, quote=False)
    for rx, rep in _INLINE:
        s = rx.sub(rep, s)
    return s


def to_html(md: str, image_base: str = "") -> str:
    lines = md.split("\n")
    out: list[str] = []
    para: list[str] = []
    list_stack: list[str] = []
    i = 0

    def flush_para() -> None:
        if para:
            out.append("<p>" + _inline(" ".join(para), image_base) + "</p>")
            para.clear()

    def close_lists(depth: int = 0) -> None:
        while len(list_stack) > depth:
            out.append(f"</{list_stack.pop()}>")

    while i < len(lines):
        line = lines[i]
        if FENCE_RE.match(line):
            flush_para(); close_lists()
            body = []
            i += 1
            while i < len(lines) and not FENCE_RE.match(lines[i]):
                body.append(lines[i]); i += 1
            out.append("<pre><code>" + html.escape("\n".join(body)) + "</code></pre>")
            i += 1
            continue
        h = HEADING_RE.match(line)
        if h:
            flush_para(); close_lists()
            n = len(h.group(1))
            out.append(f"<h{n}>{_inline(h.group(2), image_base)}</h{n}>")
        elif m := re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line):
            flush_para()
            depth = len(m.group(1).replace("\t", "  ")) // 2 + 1
            tag = "ol" if m.group(2)[0].isdigit() else "ul"
            if len(list_stack) > depth:
                close_lists(depth)
            while len(list_stack) < depth:
                list_stack.append(tag); out.append(f"<{tag}>")
            out.append(f"<li>{_inline(m.group(3), image_base)}</li>")
        elif line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|?\s*:?-+", lines[i + 1]):
            flush_para(); close_lists()
            head = [c.strip() for c in line.strip().strip("|").split("|")]
            out.append("<table><thead><tr>" + "".join(f"<th>{_inline(c, image_base)}</th>" for c in head) + "</tr></thead><tbody>")
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                out.append("<tr>" + "".join(f"<td>{_inline(c, image_base)}</td>" for c in cells) + "</tr>")
                i += 1
            out.append("</tbody></table>")
            continue
        elif line.startswith(">"):
            flush_para(); close_lists()
            out.append("<blockquote>" + _inline(line.lstrip("> "), image_base) + "</blockquote>")
        elif not line.strip():
            flush_para()
            if not (i + 1 < len(lines) and re.match(r"^\s*([-*+]|\d+[.)])\s+", lines[i + 1])):
                close_lists()
        else:
            close_lists()
            para.append(line.strip())
        i += 1
    flush_para(); close_lists()
    return "\n".join(out)
