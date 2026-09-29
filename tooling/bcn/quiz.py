"""Quizzes: the content producer's quiz markdown, parsed, and written as a QTI 2.1 package.

The markdown is an activity.md with type: quiz in its front matter:

    # Unit 1 — Check your understanding

    ## Question 1

    The stem, one or more paragraphs.

    - (a) An option → Feedback shown when this option is chosen.
    - (b) The right answer ✔ → Correct. Why it is right.

One ## section per question. Options are lettered list items; ✔ (or ✓) marks a
correct one, and → (or ->) starts its feedback. One correct option makes a
single-choice question; more than one makes a multiple-response question.

The package is an IMS content package: imsmanifest.xml, one assessmentItem per
question, and an assessmentTest that holds them in order. Options are never
shuffled, because feedback and stems may refer to them by letter.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import quoteattr

from .envelope import Diagnostic
from .markdown import _inline_plain as inline_html

OPTION_RE = re.compile(r"^\s*[-*]\s+\(([A-Za-z])\)\s*(.*)$")
CORRECT_RE = re.compile(r"\s*[✔✓✅]️?\s*")
FEEDBACK_RE = re.compile(r"\s+(?:→|->)\s+|\s*→\s*")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")

QTI_NS = "http://www.imsglobal.org/xsd/imsqti_v2p1"
QTI_XSD = "http://www.imsglobal.org/xsd/qti/qtiv2p1/imsqti_v2p1p2.xsd"
CP_NS = "http://www.imsglobal.org/xsd/imscp_v1p1"
CP_XSD = "http://www.imsglobal.org/xsd/imscp_v1p1.xsd"


@dataclass
class Option:
    letter: str
    text: str
    correct: bool
    feedback: str
    line: int

    @property
    def ident(self) -> str:
        return self.letter.upper()


@dataclass
class Question:
    title: str
    line: int
    stem: list[str] = field(default_factory=list)  # paragraphs
    options: list[Option] = field(default_factory=list)

    @property
    def multiple(self) -> bool:
        return sum(o.correct for o in self.options) > 1


@dataclass
class Quiz:
    title: str
    front: dict[str, str]
    questions: list[Question]
    diagnostics: list[Diagnostic]


def _front(lines: list[str]) -> tuple[dict[str, str], int]:
    if not lines or lines[0].strip() != "---":
        return {}, 0
    fm = {}
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return fm, i + 1
        if ":" in lines[i]:
            k, v = lines[i].split(":", 1)
            fm[k.strip()] = v.strip().strip("'\"")
    return {}, 0


def _paragraphs(lines: list[str]) -> list[str]:
    """Wrapped lines joined into paragraphs, split at blank lines."""
    out, cur = [], []
    for ln in lines:
        if ln.strip():
            cur.append(ln.strip())
        elif cur:
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def _option(letter: str, body: str, line: int) -> Option:
    parts = FEEDBACK_RE.split(body, maxsplit=1)
    text, feedback = parts[0], (parts[1].strip() if len(parts) > 1 else "")
    correct = bool(CORRECT_RE.search(text))
    return Option(letter.lower(), CORRECT_RE.sub(" ", text).strip(), correct, feedback, line)


def parse_quiz(path: Path, rel: str) -> Quiz:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    front, start = _front(lines)
    diags: list[Diagnostic] = []
    title = ""
    questions: list[Question] = []
    q: Question | None = None
    stem: list[str] = []
    opt: tuple[str, list[str], int] | None = None  # letter, body lines, line number
    opt_gap = False                                # a blank line since the option began
    trailing: int | None = None                    # first line of stray text after a question's options

    def close_option() -> None:
        nonlocal opt
        if opt and q is not None:
            q.options.append(_option(opt[0], " ".join(s.strip() for s in opt[1]), opt[2]))
        opt = None

    def close_question() -> None:
        nonlocal q, stem, trailing
        close_option()
        if q is not None:
            q.stem = _paragraphs(stem)
            if trailing:
                diags.append(Diagnostic("QUIZ_FORMAT", f"{q.title}: text after the options is not part of any option; it is left out.",
                                        file=rel, line=trailing, hint="Put stem text before the options, or indent it under an option."))
            questions.append(q)
        q, stem, trailing = None, [], None

    for i in range(start, len(lines)):
        ln, no = lines[i], i + 1
        h = HEADING_RE.match(ln)
        if h and len(h.group(1)) == 1 and not title and q is None:
            title = h.group(2)
            continue
        if h and len(h.group(1)) == 2:
            close_question()
            q = Question(h.group(2), no)
            continue
        if q is None:
            continue  # the quiz's own introduction; not part of any question
        m = OPTION_RE.match(ln)
        if m:
            close_option()
            opt, opt_gap = (m.group(1), [m.group(2)], no), False
            continue
        if opt is not None:
            if not ln.strip():
                opt_gap = True
            elif ln.startswith((" ", "\t")) or not opt_gap:
                opt[1].append(ln)      # a wrapped option: indented, or straight after it (markdown's lazy continuation)
            else:
                close_option()
                trailing = trailing or no
            continue
        if q.options:
            if ln.strip():
                trailing = trailing or no
            continue
        stem.append(ln)
    close_question()

    if not questions:
        diags.append(Diagnostic("QUIZ_NO_QUESTIONS", f"{rel} has no ## question sections.", file=rel))
    for q in questions:
        where = {"file": rel, "line": q.line}
        if not q.stem:
            diags.append(Diagnostic("QUIZ_FORMAT", f"{q.title} has no question text.", **where))
        if len(q.options) < 2:
            diags.append(Diagnostic("QUIZ_FORMAT", f"{q.title} has {len(q.options)} option(s); a question needs at least two.",
                                    hint="Write options as '- (a) text', one per line.", **where))
        if q.options and not any(o.correct for o in q.options):
            diags.append(Diagnostic("QUIZ_NO_CORRECT", f"{q.title} has no option marked correct.",
                                    hint="Mark the right answer with ✔ before its →.", **where))
        letters = [o.letter for o in q.options]
        if len(set(letters)) != len(letters):
            diags.append(Diagnostic("QUIZ_FORMAT", f"{q.title} uses the same option letter twice ({', '.join(letters)}).", **where))
        for o in q.options:
            if not o.text:
                diags.append(Diagnostic("QUIZ_FORMAT", f"{q.title}, option ({o.letter}) has no text.", file=rel, line=o.line))
            if not o.feedback:
                diags.append(Diagnostic("QUIZ_NO_FEEDBACK", f"{q.title}, option ({o.letter}) has no feedback.", file=rel, line=o.line,
                                        hint="Add '→ feedback' after the option."))
    return Quiz(title, front, questions, diags)


# -- QTI 2.1 ------------------------------------------------------------------------------

def _p(paragraphs: list[str]) -> str:
    return "".join(f"<p>{inline_html(p)}</p>" for p in paragraphs)


def item_xml(ident: str, q: Question, lang: str) -> str:
    card = "multiple" if q.multiple else "single"
    correct = "".join(f"<value>{o.ident}</value>" for o in q.options if o.correct)
    choices = "\n".join(f'        <simpleChoice identifier="{o.ident}">{inline_html(o.text)}</simpleChoice>' for o in q.options)
    feedback = "\n".join(
        f'  <modalFeedback outcomeIdentifier="FEEDBACK" showHide="show" identifier="{o.ident}"><p>{inline_html(o.feedback)}</p></modalFeedback>'
        for o in q.options if o.feedback)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<assessmentItem xmlns="{QTI_NS}" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:schemaLocation="{QTI_NS} {QTI_XSD}"
    identifier="{ident}" title={quoteattr(q.title)} adaptive="false" timeDependent="false" xml:lang="{lang}">
  <responseDeclaration identifier="RESPONSE" cardinality="{card}" baseType="identifier">
    <correctResponse>{correct}</correctResponse>
  </responseDeclaration>
  <outcomeDeclaration identifier="SCORE" cardinality="single" baseType="float">
    <defaultValue><value>0</value></defaultValue>
  </outcomeDeclaration>
  <outcomeDeclaration identifier="MAXSCORE" cardinality="single" baseType="float">
    <defaultValue><value>1</value></defaultValue>
  </outcomeDeclaration>
  <outcomeDeclaration identifier="FEEDBACK" cardinality="{card}" baseType="identifier"/>
  <itemBody>
    {_p(q.stem)}
    <choiceInteraction responseIdentifier="RESPONSE" shuffle="false" maxChoices="{0 if q.multiple else 1}">
{choices}
    </choiceInteraction>
  </itemBody>
  <responseProcessing>
    <responseCondition>
      <responseIf>
        <match><variable identifier="RESPONSE"/><correct identifier="RESPONSE"/></match>
        <setOutcomeValue identifier="SCORE"><baseValue baseType="float">1</baseValue></setOutcomeValue>
      </responseIf>
      <responseElse>
        <setOutcomeValue identifier="SCORE"><baseValue baseType="float">0</baseValue></setOutcomeValue>
      </responseElse>
    </responseCondition>
    <setOutcomeValue identifier="FEEDBACK"><variable identifier="RESPONSE"/></setOutcomeValue>
  </responseProcessing>
{feedback}
</assessmentItem>
"""


def test_xml(ident: str, title: str, items: list[tuple[str, str]]) -> str:
    refs = "\n".join(f'        <assessmentItemRef identifier="{i}" href="{href}"/>' for i, href in items)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<assessmentTest xmlns="{QTI_NS}" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:schemaLocation="{QTI_NS} {QTI_XSD}"
    identifier="{ident}" title={quoteattr(title)}>
  <outcomeDeclaration identifier="SCORE" cardinality="single" baseType="float"/>
  <testPart identifier="{ident}-part" navigationMode="nonlinear" submissionMode="simultaneous">
    <assessmentSection identifier="{ident}-section" title={quoteattr(title)} visible="true">
{refs}
    </assessmentSection>
  </testPart>
  <outcomeProcessing>
    <setOutcomeValue identifier="SCORE"><sum><testVariables variableIdentifier="SCORE"/></sum></setOutcomeValue>
  </outcomeProcessing>
</assessmentTest>
"""


def manifest_xml(ident: str, test_href: str, items: list[tuple[str, str]]) -> str:
    item_res = "\n".join(
        f'    <resource identifier="res-{i}" type="imsqti_item_xmlv2p1" href="{href}"><file href="{href}"/></resource>'
        for i, href in items)
    deps = "".join(f'<dependency identifierref="res-{i}"/>' for i, _ in items)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<manifest xmlns="{CP_NS}" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:schemaLocation="{CP_NS} {CP_XSD}" identifier="manifest-{ident}">
  <metadata><schema>QTIv2.1 Package</schema><schemaversion>1.0.0</schemaversion></metadata>
  <organizations/>
  <resources>
    <resource identifier="res-{ident}" type="imsqti_test_xmlv2p1" href="{test_href}"><file href="{test_href}"/>{deps}</resource>
{item_res}
  </resources>
</manifest>
"""


def qti_package(quiz: Quiz, ident: str, lang: str = "en") -> bytes:
    """The QTI 2.1 content package as zip bytes. ident must be an XML NCName (e.g. KV7016-U01-quiz)."""
    items = [(f"{ident}-q{n:02d}", f"items/{ident}-q{n:02d}.xml") for n in range(1, len(quiz.questions) + 1)]
    title = quiz.title or ident
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # A fixed timestamp, so the same quiz always makes byte-identical packages.
        def put(name: str, text: str) -> None:
            z.writestr(zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0)), text.encode("utf-8"), zipfile.ZIP_DEFLATED)

        put("imsmanifest.xml", manifest_xml(ident, f"{ident}.xml", items))
        put(f"{ident}.xml", test_xml(ident, title, items))
        for (i, href), q in zip(items, quiz.questions):
            put(href, item_xml(i, q, lang))
    return buf.getvalue()
