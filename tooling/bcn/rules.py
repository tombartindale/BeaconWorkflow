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
]
EN_WEEKDAY_PATTERN = re.compile(r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b", re.IGNORECASE)

# Candidate person names: a title (Dr, Professor, Mr, ...) followed by capitalised word(s), or
# two-plus consecutive capitalised words not at the start of a sentence (so an ordinary sentence-
# initial capital is not itself a hit). Heuristic, not NLP: it is a prompt to a human, not a fact.
_HONORIFIC = r"(?:Dr|Mr|Mrs|Ms|Miss|Prof|Professor|Sir|Dame)\.?"
_CAP_WORD = r"[A-Z][a-z]+(?:['’][A-Z]?[a-z]+)?"
NAME_PATTERNS = [
    re.compile(rf"\b{_HONORIFIC}\s+{_CAP_WORD}(?:\s+{_CAP_WORD})?\b"),
    re.compile(rf"(?<!^)(?<![.!?]\s){_CAP_WORD}(?:\s+{_CAP_WORD})+\b"),
]
# Common capitalised phrases a heuristic like this will otherwise flag constantly: title-case
# headings, place names already covered by [validate].localization, institution words, etc.
NAME_STOPWORDS = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
}

# Checks that are a matter of editorial judgement: a person may acknowledge a
# particular finding (bcn ack) and the pipeline carries on. Structural checks
# (missing narration, parity, front matter, assets) can never be acknowledged.
ACKABLE = {"MD_DATE", "MD_FORBIDDEN", "MD_DEICTIC", "MD_WORD_COUNT", "MD_SLIDE_WORDS", "MD_TITLE_LENGTH",
           "MD_SLIDE_COUNT", "MD_ZH_CHARS", "MD_SLIDE_TITLE_MISSING", "MD_ZH_UNTRANSLATED", "MD_LOCALIZATION",
           "MD_PERSON_NAME"}
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
          level: str | None = None, file: str | None = None, match: str | None = None) -> Diagnostic:
        data = None
        if code in ACKABLE:
            # Identifies the finding by what it is, not where: an acknowledgement follows the
            # text if lines move, and lapses if the text itself changes.
            data = {"fingerprint": f"{code}|{self.lang}|s{slide or 0}|{(match or '').lower()}"}
            if match:
                data["match"] = match
        return Diagnostic(code, msg, topic=self.topic.id, lang=self.lang, file=file or self.rel, line=line, slide=slide,
                          hint=hint, level=level, data=data)

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
        return self._configure(self._dedupe(out))

    @staticmethod
    def _dedupe(diags: list[Diagnostic]) -> list[Diagnostic]:
        """One finding per fingerprint: 'Friday' three times on a slide is one thing to decide about."""
        seen: dict[str, Diagnostic] = {}
        out = []
        for d in diags:
            fp = (d.data or {}).get("fingerprint")
            if fp and fp in seen:
                seen[fp].data["occurrences"] = seen[fp].data.get("occurrences", 1) + 1
                continue
            if fp:
                seen[fp] = d
            out.append(d)
        return out

    def _configure(self, diags: list[Diagnostic]) -> list[Diagnostic]:
        """[validate] severity in programme.toml can make a check a warning, info, or switch it off."""
        sev = self.cfg["validate"].get("severity", {})
        out = []
        for d in diags:
            level = sev.get(d.code)
            if level == "off":
                continue
            if level in ("error", "warn", "info"):
                d.level = level
            out.append(d)
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
                                  s.title_line, s.index, match=s.title))
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

    def localization_list(self) -> list[str]:
        return list(self.cfg["validate"]["localization"])

    def date_patterns(self) -> list[re.Pattern[str]]:
        return EN_DATE_PATTERNS + ([EN_WEEKDAY_PATTERN] if self.cfg["validate"].get("date_weekdays") else [])

    def texts(self, s: Slide) -> list[str]:
        return [s.content]

    def text(self, p: ParsedTopic) -> list[Diagnostic]:
        out = []
        forbidden = [(w, _phrase_re(w)) for w in self.forbidden_list() if w.strip()]
        localized = [(w, _phrase_re(w)) for w in self.localization_list() if w.strip()]
        for s in p.slides:
            for t in self.texts(s):
                for rx in self.date_patterns():
                    for m in rx.finditer(t):
                        lr = re.compile(re.escape(m.group(0)))
                        out.append(self.d("MD_DATE", f"Slide {s.index} contains a date: '{m.group(0)}'.", self.locate(p, s, lr), s.index,
                                          hint="Dated material goes stale. If this date is deliberate (an example, not a schedule), acknowledge it.",
                                          match=m.group(0)))
                for w, rx in forbidden:
                    if rx.search(t):
                        out.append(self.d("MD_FORBIDDEN", f"Slide {s.index} contains the forbidden string '{w}'.", self.locate(p, s, rx), s.index,
                                          match=w))
                for w, rx in localized:
                    if rx.search(t):
                        out.append(self.d("MD_LOCALIZATION", f"Slide {s.index} names '{w}', which ties this content to one place.",
                                          self.locate(p, s, rx), s.index,
                                          hint="Generalise the reference, or acknowledge it if this topic is deliberately local.",
                                          match=w))
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
                                      self.locate(p, s, rx), s.index, hint="Describe the thing rather than point at it.", match=ph))
        if self.cfg["validate"]["names"]:
            allow = {w.lower() for w in self.cfg["validate"]["names_allow"]}
            for s in p.slides:
                for t in self.texts(s):
                    seen: set[str] = set()
                    for rx in NAME_PATTERNS:
                        for m in rx.finditer(t):
                            name = m.group(0).lstrip("!?.").strip()
                            key = name.lower()
                            if key in seen or key in allow or name.split()[0].lower() in NAME_STOPWORDS:
                                continue
                            seen.add(key)
                            lr = re.compile(re.escape(name))
                            out.append(self.d("MD_PERSON_NAME", f"Slide {s.index} may name a person: '{name}'.",
                                              self.locate(p, s, lr), s.index,
                                              hint="If this names a real person, generalise it, or acknowledge it if the topic is about them specifically.",
                                              match=name))
        return out


class MandarinRules(RuleSet):
    lang = "zh"

    def __init__(self, cfg: Config, topic: Topic) -> None:
        super().__init__(cfg, topic)

    def title_limit(self) -> int:
        return int(self.cfg["validate_zh"].get("title_max_chars", 30))

    def forbidden_list(self) -> list[str]:
        return list(self.cfg["validate_zh"]["forbidden"])

    def localization_list(self) -> list[str]:
        return list(self.cfg["validate_zh"]["localization"])

    def date_patterns(self) -> list[re.Pattern[str]]:
        return ZH_DATE_PATTERNS

    def structure(self, p: ParsedTopic) -> list[Diagnostic]:
        out = super().structure(p)
        limit = int(self.cfg["validate_zh"]["slide_chars_max"])
        # The topic title and slide headings are translated in this file (the SRT only carries
        # what was spoken), so one with no Chinese at all was almost certainly missed.
        title = p.front.get("title", "").strip()
        if title and not CJK_RE.search(title):
            out.append(self.d("MD_ZH_UNTRANSLATED", f"The topic title '{title}' has no Chinese in it.",
                              p.front_lines.get("title"), hint="Return it to the translator, or acknowledge it if it is meant to stay as it is.",
                              match=title))
        for s in p.slides:
            if s.title and not CJK_RE.search(s.title):
                out.append(self.d("MD_ZH_UNTRANSLATED", f"Slide {s.index} heading '{s.title}' has no Chinese in it.",
                                  s.title_line, s.index, hint="Return it to the translator, or acknowledge it if it is meant to stay as it is.",
                                  match=s.title))
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


def validate_topic(cfg: Config, topic: Topic, lang: str, text: str | None = None) -> tuple[ParsedTopic | None, list[Diagnostic]]:
    """Validate the topic's source, or `text` in its place (an unsaved edit)."""
    rules = RULESETS[lang](cfg, topic)
    src = topic.src(lang)
    rel = src.name
    if text is None and not src.is_file():
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
        p = parse(src, rel, topic.id, text=text)
        diags += rules.parity(p, en)  # type: ignore[attr-defined]
    else:
        p = parse(src, rel, topic.id, text=text)
    diags += rules.check(p)
    return p, apply_acknowledgements(topic, diags)


def apply_acknowledgements(topic: Topic, diags: list[Diagnostic]) -> list[Diagnostic]:
    """Findings a person has acknowledged (in review.json) become info and stop blocking."""
    from . import reviewfile
    acks = reviewfile.load(topic).get("acknowledged", {}) if topic.review_file.is_file() else {}
    if not acks:
        return diags
    for d in diags:
        fp = (d.data or {}).get("fingerprint")
        if fp and fp in acks and d.code in ACKABLE:
            d.level = "info"
            d.data["acknowledged"] = acks[fp]
            d.hint = None
    return diags
