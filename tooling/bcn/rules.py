"""Validation rule sets. The check set is a property of the language.

English: narration rules, word counts at the configured rate, deictic phrases,
English date patterns. Mandarin: no narration at all, a character-count sanity
check, Chinese date patterns, the translated forbidden list, and slide parity
against the English.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from .config import Config
from .envelope import Diagnostic
from .markdown import ParsedTopic, Slide, parse
from .tree import TOPIC_ID_RE, Topic

_MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december|"
           "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
EN_DATE_PATTERNS = [
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{_MONTHS})\b\.?", re.IGNORECASE),
    re.compile(rf"\b(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b", re.IGNORECASE),
    re.compile(rf"\b(?:{_MONTHS})\.?\s+\d{{4}}\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b", re.IGNORECASE),
]
ZH_DATE_PATTERNS = [
    re.compile(r"\d{2,4}\s*年\s*\d{1,2}\s*月"),
    re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]"),
    re.compile(r"[〇一二三四五六七八九零]{4}年"),
    re.compile(r"(?:星期|礼拜)[一二三四五六日天]"),
    re.compile(r"(?<![a-zA-Z])周[一二三四五六日]"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b"),
]
CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿　-〿＀-￯]")
MD_SYNTAX_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)|[#*_`>|\-]+|\[|\]\([^)]*\)")


def _phrase_re(phrase: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in phrase.split()]
    return re.compile(r"(?<![\w])" + r"\s+".join(words) + r"(?![\w])", re.IGNORECASE)


class RuleSet:
    lang = ""

    def __init__(self, cfg: Config, topic: Topic) -> None:
        self.cfg = cfg
        self.topic = topic
        self.rel = self._rel(topic.src(self.lang))

    def _rel(self, p: Path) -> str:
        return str(p.relative_to(self.topic.dir))

    def d(self, code: str, msg: str, line: int | None = None, slide: int | None = None, hint: str | None = None,
          level: str | None = None, file: str | None = None) -> Diagnostic:
        return Diagnostic(code, msg, topic=self.topic.id, lang=self.lang, file=file or self.rel, line=line, slide=slide,
                          hint=hint, level=level)

    def locate(self, p: ParsedTopic, s: Slide, rx: re.Pattern[str]) -> int | None:
        for ln in range(s.start_line, s.end_line + 1):
            if rx.search(p.raw_lines[ln - 1]):
                return ln
        return s.say_line or s.start_line

    # -- common ------------------------------------------------------------------
    def check(self, p: ParsedTopic) -> list[Diagnostic]:
        out = [self._tag(x) for x in p.diagnostics]
        out += self.front_matter(p)
        out += self.structure(p)
        out += self.images(p)
        out += self.text(p)
        return out

    def _tag(self, x: Diagnostic) -> Diagnostic:
        x.lang = self.lang
        return x

    def front_matter(self, p: ParsedTopic) -> list[Diagnostic]:
        out = []
        fm, fl = p.front, p.front_lines
        tid = fm.get("topic_id")
        if tid is not None:
            if not TOPIC_ID_RE.match(tid):
                out.append(self.d("MD_TOPIC_ID_FORMAT", f"topic_id '{tid}' does not match XX0000-U00-T00.", fl["topic_id"]))
            elif tid != self.topic.id:
                out.append(self.d("MD_TOPIC_ID_PATH", f"topic_id '{tid}' does not match its directory {self.topic.rel}, which is {self.topic.id}.",
                                  fl["topic_id"], hint="Either the id is wrong or the file is in the wrong folder."))
        if "minutes" in fm:
            try:
                if int(fm["minutes"]) <= 0:
                    raise ValueError
            except ValueError:
                out.append(self.d("MD_FRONT_MATTER_VALUE", f"minutes must be a positive whole number, not '{fm['minutes']}'.", fl["minutes"]))
        if "lang" in fm and fm["lang"] != self.lang:
            out.append(self.d("MD_FRONT_MATTER_VALUE", f"lang is '{fm['lang']}' but this file must declare '{self.lang}'.", fl["lang"]))
        if "title" in fm and not fm["title"].strip():
            out.append(self.d("MD_FRONT_MATTER_VALUE", "title is empty.", fl["title"]))
        return out

    def structure(self, p: ParsedTopic) -> list[Diagnostic]:
        v = self.cfg["validate"]
        out = []
        n = len(p.slides)
        if n == 0:
            out.append(self.d("MD_NO_SLIDES", "No slides found.", p.body_start))
            return out
        if not v["slides_min"] <= n <= v["slides_max"]:
            out.append(self.d("MD_SLIDE_COUNT", f"{n} slide{'s' if n != 1 else ''}; expected between {v['slides_min']} and {v['slides_max']}.",
                              p.body_start))
        limit = self.title_limit()
        for s in p.slides:
            if s.title is None:
                out.append(self.d("MD_SLIDE_TITLE_MISSING", f"Slide {s.index} has no heading.", s.start_line, s.index))
            elif len(s.title) >= limit:
                out.append(self.d("MD_TITLE_LENGTH", f"Slide {s.index} title is {len(s.title)} characters; the limit is under {limit}.",
                                  s.title_line, s.index))
        return out

    def title_limit(self) -> int:
        return int(self.cfg["validate"]["title_max_chars"])

    def images(self, p: ParsedTopic) -> list[Diagnostic]:
        out = []
        for s in p.slides:
            for im in s.images:
                if not im.alt:
                    out.append(self.d("MD_IMAGE_ALT", f"Image {im.path} on slide {s.index} has no alt text.", im.line, s.index))
                pp = PurePosixPath(im.path)
                if re.match(r"^[a-z]+:", im.path) or pp.is_absolute() or not pp.parts or pp.parts[0] != "assets" or ".." in pp.parts:
                    out.append(self.d("MD_IMAGE_PATH", f"Image path '{im.path}' must be relative and inside assets/.", im.line, s.index))
                elif not (self.topic.dir / pp).is_file():
                    out.append(self.d("MD_ASSET_MISSING", f"Asset '{im.path}' does not exist.", im.line, s.index,
                                      hint="Add the file to assets/ or check the asset request in assets.md."))
                else:
                    on_disk = set(_listdir(self.topic.dir / pp.parent))
                    if pp.name not in on_disk:
                        out.append(self.d("FS_WRONG_CASE", f"Asset '{im.path}' exists only with different capitalisation.",
                                          im.line, s.index))
        return out

    def forbidden_list(self) -> list[str]:
        return list(self.cfg["validate"]["forbidden"])

    def date_patterns(self) -> list[re.Pattern[str]]:
        return EN_DATE_PATTERNS

    def texts(self, s: Slide) -> list[str]:
        return [s.content]

    def text(self, p: ParsedTopic) -> list[Diagnostic]:
        out = []
        forbidden = [(w, _phrase_re(w)) for w in self.forbidden_list() if w.strip()]
        for s in p.slides:
            for t in self.texts(s):
                for rx in self.date_patterns():
                    for m in rx.finditer(t):
                        lr = re.compile(re.escape(m.group(0)))
                        out.append(self.d("MD_DATE", f"Slide {s.index} contains a date: '{m.group(0)}'.", self.locate(p, s, lr), s.index,
                                          hint="Dated material goes stale; remove it or move it out of the recorded content."))
                for w, rx in forbidden:
                    if rx.search(t):
                        out.append(self.d("MD_FORBIDDEN", f"Slide {s.index} contains the forbidden string '{w}'.", self.locate(p, s, rx), s.index))
        return out


def _listdir(d: Path) -> list[str]:
    import os
    try:
        return os.listdir(d)
    except OSError:
        return []


class EnglishRules(RuleSet):
    lang = "en"

    def texts(self, s: Slide) -> list[str]:
        return [s.content, s.say_text]

    def structure(self, p: ParsedTopic) -> list[Diagnostic]:
        out = super().structure(p)
        v = self.cfg["validate"]
        for s in p.slides:
            if s.say_blocks == 0:
                out.append(self.d("MD_SAY_MISSING", f"Slide {s.index} has no > **Say:** block.", s.start_line, s.index,
                                  hint="End every slide with a > **Say:** block holding its narration."))
            elif not (v["narration_min_words"] <= s.say_words <= v["narration_max_words"]):
                out.append(self.d("MD_SLIDE_WORDS", f"Slide {s.index} narration is {s.say_words} words; expected {v['narration_min_words']} to {v['narration_max_words']}.",
                                  s.say_line, s.index))
        try:
            minutes = int(p.front.get("minutes", ""))
        except ValueError:
            minutes = 0
        if minutes > 0 and p.slides:
            target = minutes * v["words_per_minute"]
            tol = v["word_tolerance"]
            words = p.narration_words
            if abs(words - target) > target * tol:
                out.append(self.d("MD_WORD_COUNT", f"Narration is {words} words; {minutes} minutes at {v['words_per_minute']} wpm is {target} ± {int(tol * 100)}% ({int(target * (1 - tol))}–{int(target * (1 + tol))}).",
                                  p.front_lines.get("minutes"),
                                  hint="Adjust the narration or the declared minutes."))
        return out

    def text(self, p: ParsedTopic) -> list[Diagnostic]:
        out = super().text(p)
        phrases = [(ph, _phrase_re(ph)) for ph in self.cfg["validate"]["deictic"] if ph.strip()]
        for s in p.slides:
            for ph, rx in phrases:
                if rx.search(s.say_text):
                    out.append(self.d("MD_DEICTIC", f"Slide {s.index} narration says '{ph}', but the presenter is recorded without the slides in shot.",
                                      self.locate(p, s, rx), s.index, hint="Describe the thing rather than point at it."))
        return out


class MandarinRules(RuleSet):
    lang = "zh"

    def __init__(self, cfg: Config, topic: Topic) -> None:
        super().__init__(cfg, topic)

    def title_limit(self) -> int:
        return int(self.cfg["validate_zh"].get("title_max_chars", 30))

    def forbidden_list(self) -> list[str]:
        return list(self.cfg["validate_zh"]["forbidden"])

    def date_patterns(self) -> list[re.Pattern[str]]:
        return ZH_DATE_PATTERNS

    def structure(self, p: ParsedTopic) -> list[Diagnostic]:
        out = super().structure(p)
        limit = int(self.cfg["validate_zh"]["slide_chars_max"])
        for s in p.slides:
            if s.say_blocks:
                out.append(self.d("MD_SAY_IN_ZH", f"Slide {s.index} has a Say block; Mandarin sources carry no narration.",
                                  s.say_line, s.index,
                                  hint="Narration is translated once, in the SRT. Remove the block from topic.zh.md."))
            chars = slide_chars(s.content)
            if chars > limit:
                out.append(self.d("MD_ZH_CHARS", f"Slide {s.index} has {chars} characters of text; the sanity limit is {limit}.",
                                  s.start_line, s.index))
        return out

    def parity(self, p: ParsedTopic, en: ParsedTopic) -> list[Diagnostic]:
        out = []
        if len(p.slides) != len(en.slides):
            out.append(self.d("MD_PARITY_COUNT", f"topic.zh.md has {len(p.slides)} slides; topic.md has {len(en.slides)}.",
                              p.body_start,
                              hint="Every cue after the first mismatch is invalid. Return the file to the translator; slides must not be merged or split."))
            # Point at the first slide whose shape stops matching, to help the translator.
        for a, b in zip(p.slides, en.slides):
            sa, sb = a.signature(), b.signature()
            if sa["images"] != sb["images"] or sa["heading_level"] != sb["heading_level"]:
                out.append(self.d("MD_PARITY_BREAK", f"Slide {a.index} does not match English slide {b.index} in shape (heading or images differ); a break has probably moved.",
                                  a.start_line, a.index, hint="Compare the slide breaks against topic.md from this slide on."))
                break
            if sa["list_items"] != sb["list_items"]:
                out.append(self.d("MD_PARITY_BREAK", f"Slide {a.index} has {sa['list_items']} list items; the English has {sb['list_items']}.",
                                  a.start_line, a.index, level="warn"))
        return out


RULESETS: dict[str, type[RuleSet]] = {"en": EnglishRules, "zh": MandarinRules}


def slide_chars(content: str) -> int:
    text = MD_SYNTAX_RE.sub("", content)
    return len(re.sub(r"\s+", "", text))


def validate_topic(cfg: Config, topic: Topic, lang: str) -> tuple[ParsedTopic | None, list[Diagnostic]]:
    rules = RULESETS[lang](cfg, topic)
    src = topic.src(lang)
    rel = src.name
    if not src.is_file():
        return None, [Diagnostic("FS_MISSING", f"{rel} does not exist.", topic=topic.id, lang=lang, file=rel)]
    diags: list[Diagnostic] = []
    if lang != "en":
        # Parity runs first: nothing else touches a Mandarin file until it is known
        # to line up slide for slide with the English.
        en_src = topic.src("en")
        if not en_src.is_file():
            return None, [Diagnostic("FS_MISSING", "topic.md does not exist; Mandarin cannot be checked for parity.",
                                     topic=topic.id, lang=lang, file="topic.md")]
        en = parse(en_src, "topic.md", topic.id)
        p = parse(src, rel, topic.id)
        diags += rules.parity(p, en)  # type: ignore[attr-defined]
    else:
        p = parse(src, rel, topic.id)
    diags += rules.check(p)
    return p, diags
