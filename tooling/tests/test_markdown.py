from pathlib import Path

from bcn.markdown import parse, to_html

from .conftest import topic_md


def p(text: str):
    return parse(Path("topic.md"), "topic.md", "KV7015-U01-T01", text=text)


def codes(parsed):
    return [d.code for d in parsed.diagnostics]


def test_parses_slides_and_narration():
    t = p(topic_md(slides=6))
    assert len(t.slides) == 6
    assert t.front["topic_id"] == "KV7015-U01-T01"
    s = t.slides[0]
    assert s.title == "Slide 1 heading" and s.heading_level == 1
    assert s.say_text.startswith("This is narration")
    assert "Say:" not in s.content
    assert codes(t) == []


def test_front_matter_is_not_a_slide_break():
    t = p(topic_md(slides=6))
    assert t.slides[0].start_line > 6


def test_multiline_say_block():
    t = p("---\ntopic_id: X\ntitle: t\nminutes: 1\nlang: en\n---\n\n# A\n\n> **Say:** line one\n> line two\n")
    assert t.slides[0].say_text == "line one line two"


def test_say_errors_reported_with_lines():
    text = "---\ntopic_id: X\ntitle: t\nminutes: 1\nlang: en\n---\n\n# A\n\n> **Say:** one\n\n> **Say:** two\n\n---\n\n# B\n\n> **Say:** x\n\nafter\n"
    t = p(text)
    assert "MD_SAY_MULTIPLE" in codes(t)
    bad = [d for d in t.diagnostics if d.code == "MD_SAY_NOT_LAST"]
    assert bad and bad[0].line == 20 and bad[0].slide == 2


def test_front_matter_keys_exact():
    t = p("---\ntopic_id: X\ntitle: t\nminutes: 1\nlang: en\nmarp: true\n---\n\n# A\n")
    assert "MD_FRONT_MATTER_KEYS" in codes(t)
    t = p("---\ntopic_id: X\ntitle: t\n---\n\n# A\n")
    assert "MD_FRONT_MATTER_KEYS" in codes(t)
    t = p("# no front matter\n")
    assert "MD_FRONT_MATTER_MISSING" in codes(t)


def test_break_inside_code_fence_is_not_a_break():
    t = p("---\ntopic_id: X\ntitle: t\nminutes: 1\nlang: en\n---\n\n# A\n\n```\n---\n```\n\n> **Say:** x\n")
    assert len(t.slides) == 1


def test_stripped_file_has_no_narration():
    t = p(topic_md(slides=3))
    out = t.stripped_file()
    assert "Say:" not in out and out.startswith("---\ntopic_id:") and out.count("\n---\n") >= 3


def test_images_collected():
    t = p("---\ntopic_id: X\ntitle: t\nminutes: 1\nlang: en\n---\n\n# A\n\n![A chart](assets/c.png)\n\n> **Say:** x\n")
    assert t.slides[0].images[0].path == "assets/c.png" and t.slides[0].images[0].alt == "A chart"


def test_to_html():
    h = to_html("# T\n\n- a **b**\n- c\n\n![alt](assets/x.png)", "/files/K/")
    assert "<h1>T</h1>" in h and "<strong>b</strong>" in h and 'src="/files/K/assets/x.png"' in h


def test_recording_script_outputs():
    from bcn.commands.script import script_html, script_text
    from bcn.tree import Topic
    t = p("---\ntopic_id: KV7015-U01-T01\ntitle: T\nminutes: 1\nlang: en\n---\n\n# First\n\n> **Say:** One *two*.\n>\n> Three.\n\n---\n\n# Second\n\n> **Say:** Four.\n")
    topic = Topic(Path("/r"), "KV7015", "U01", "T01")
    txt = script_text(t)
    assert "— SLIDE 1 · First —" in txt and "One two.\n\nThree." in txt and "SLIDE 2 · Second" in txt
    page = script_html(topic, t)
    assert page.count("class='divider'") == 2 and "<em>two</em>" in page and "Say:" not in page
    prompt = script_html(topic, t, teleprompter=True)
    assert "<h2>SLIDE 2 · Second</h2>" in prompt and "<style>" not in prompt
