"""bcn qti: quiz markdown to a QTI 2.1 package."""

import io
import zipfile
import xml.etree.ElementTree as ET

from bcn.quiz import parse_quiz, qti_package

from .conftest import age
from .test_cli import bcn

NS = {"q": "http://www.imsglobal.org/xsd/imsqti_v2p1", "cp": "http://www.imsglobal.org/xsd/imscp_v1p1"}

QUIZ = """---
unit: KV7015-U01
type: quiz
lang: en
---

# Unit 1 — Check your understanding

Answer every question.

## Question 1

A model scores 94% on test data. What does the score
tell the firm about a **new** depot?

- (a) It will be 94% accurate there → The score only describes data like the test data.
- (b) Very little ✔ → Correct. A test score is a statement about situations like the test data.

## Question 2

Which are signs of drift? Choose all that apply.

- (a) Predictions get worse over time ✔ → Correct.
- (b) The inputs start to look different ✓ -> Correct. Changing inputs are drift
  even before accuracy falls.
- (c) The model file changes → The model not changing is the point.
"""


def _write(tree, text=QUIZ):
    p = tree / "KV7015" / "U01" / "activity.md"
    p.write_text(text)
    return p


def _package(tree):
    return tree / "KV7015" / "build" / "qti" / "KV7015-U01-quiz.zip"


def test_parse_quiz(tree):
    q = parse_quiz(_write(tree), "activity.md")
    assert q.title == "Unit 1 — Check your understanding" and q.front["type"] == "quiz"
    assert [x.title for x in q.questions] == ["Question 1", "Question 2"]
    one, two = q.questions
    assert one.stem == ["A model scores 94% on test data. What does the score tell the firm about a **new** depot?"]
    assert [(o.letter, o.correct) for o in one.options] == [("a", False), ("b", True)]
    assert one.options[1].text == "Very little" and one.options[1].feedback.startswith("Correct. A test score")
    assert two.multiple and [o.correct for o in two.options] == [True, True, False]
    assert two.options[1].feedback == "Correct. Changing inputs are drift even before accuracy falls."
    assert q.diagnostics == []


def test_lazy_continuation_and_stray_text(tree):
    text = QUIZ.replace("- (c) The model file changes → The model not changing is the point.\n",
                        "- (c) The model file changes → The model not\nchanging is the point.\n\nA stray note.\n")
    q = parse_quiz(_write(tree, text), "activity.md")
    assert q.questions[1].options[2].feedback == "The model not changing is the point."
    assert [d.code for d in q.diagnostics] == ["QUIZ_FORMAT"] and "after the options" in q.diagnostics[0].message


def test_package_structure(tree):
    data = qti_package(parse_quiz(_write(tree), "activity.md"), "KV7015-U01-quiz")
    z = zipfile.ZipFile(io.BytesIO(data))
    names = set(z.namelist())
    assert names == {"imsmanifest.xml", "KV7015-U01-quiz.xml",
                     "items/KV7015-U01-quiz-q01.xml", "items/KV7015-U01-quiz-q02.xml"}
    man = ET.fromstring(z.read("imsmanifest.xml"))
    types = [r.get("type") for r in man.iterfind(".//cp:resource", NS)]
    assert types == ["imsqti_test_xmlv2p1", "imsqti_item_xmlv2p1", "imsqti_item_xmlv2p1"]
    test = ET.fromstring(z.read("KV7015-U01-quiz.xml"))
    assert [r.get("href") for r in test.iterfind(".//q:assessmentItemRef", NS)] == \
        ["items/KV7015-U01-quiz-q01.xml", "items/KV7015-U01-quiz-q02.xml"]

    one = ET.fromstring(z.read("items/KV7015-U01-quiz-q01.xml"))
    assert one.find("q:responseDeclaration", NS).get("cardinality") == "single"
    assert [v.text for v in one.iterfind(".//q:correctResponse/q:value", NS)] == ["B"]
    assert one.find(".//q:choiceInteraction", NS).get("shuffle") == "false"
    assert one.find(".//q:itemBody/q:p/q:strong", NS).text == "new"
    assert [f.get("identifier") for f in one.iterfind("q:modalFeedback", NS)] == ["A", "B"]

    two = ET.fromstring(z.read("items/KV7015-U01-quiz-q02.xml"))
    assert two.find("q:responseDeclaration", NS).get("cardinality") == "multiple"
    assert two.find(".//q:choiceInteraction", NS).get("maxChoices") == "0"
    assert [v.text for v in two.iterfind(".//q:correctResponse/q:value", NS)] == ["A", "B"]
    assert data == qti_package(parse_quiz(_write(tree), "activity.md"), "KV7015-U01-quiz"), "byte-identical on rebuild"


def test_command_writes_skips_and_refuses(tree, capsys):
    src = _write(tree)
    code, env = bcn(capsys, "qti", str(tree / "KV7015"))
    assert code == 0 and env["ok"], env["diagnostics"]
    r = env["results"][0]
    assert r["questions"] == 2 and r["multiple_response"] == 1 and r["package"] == "KV7015/build/qti/KV7015-U01-quiz.zip"
    assert _package(tree).is_file() and env["artifacts"][0]["kind"] == "qti"

    code, env = bcn(capsys, "qti", str(src))  # the file itself is a valid target
    assert env["results"][0]["skipped"] is True

    # A question with no correct answer: no package, and the old one goes.
    age(_package(tree), 60)
    _write(tree, QUIZ.replace(" ✔ →", " →"))
    code, env = bcn(capsys, "qti", str(tree / "KV7015/U01"))
    assert code == 1 and "QUIZ_NO_CORRECT" in [d["code"] for d in env["diagnostics"]]
    assert env["results"][0]["package"] is None and not _package(tree).exists()


def test_not_a_quiz(tree, capsys):
    code, env = bcn(capsys, "qti", str(tree / "KV7015/U01"))  # the fixture's activity is a task
    assert code == 0 and [d["code"] for d in env["diagnostics"]] == ["QUIZ_NOT_A_QUIZ"]


def test_validate_reports_quiz_problems(tree, capsys):
    _write(tree, QUIZ.replace(" ✔ →", " →"))
    code, env = bcn(capsys, "validate", str(tree / "KV7015"))
    assert "QUIZ_NO_CORRECT" in [d["code"] for d in env["diagnostics"]]
