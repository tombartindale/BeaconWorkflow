from bcn.config import load
from bcn.rules import validate_topic
from bcn.tree import Topic

from .conftest import topic_md


def run(root, lang="en"):
    t = Topic(root, "KV7015", "U01", "T01")
    _, diags = validate_topic(load(root, root / "KV7015"), t, lang)
    return [d.code for d in diags if d.level == "error"], diags


def write(root, text, name="topic.md"):
    (root / "KV7015" / "U01" / "T01" / name).write_text(text)


def test_valid_topic_passes(tree):
    errs, _ = run(tree)
    assert errs == []


def test_topic_id_must_match_path(tree):
    write(tree, topic_md(topic_id="KV7015-U01-T02"))
    assert "MD_TOPIC_ID_PATH" in run(tree)[0]
    write(tree, topic_md(topic_id="kv7015-u01-t01"))
    assert "MD_TOPIC_ID_FORMAT" in run(tree)[0]


def test_word_count_and_slide_count(tree):
    write(tree, topic_md(slides=6, minutes=9))
    assert "MD_WORD_COUNT" in run(tree)[0]
    write(tree, topic_md(slides=4, minutes=2))
    assert "MD_SLIDE_COUNT" in run(tree)[0]


def test_forbidden_deictic_and_dates(tree):
    text = topic_md().replace("gives one example", "as you can see gives one example before the deadline on 12 March")
    write(tree, text)
    errs, diags = run(tree)
    assert {"MD_DEICTIC", "MD_FORBIDDEN", "MD_DATE"} <= set(errs)
    deictic = next(d for d in diags if d.code == "MD_DEICTIC")
    assert deictic.line and deictic.slide == 1


def test_missing_asset_and_alt(tree):
    write(tree, topic_md().replace("- Point one", "![](assets/nope.png)\n- Point one", 1))
    errs, _ = run(tree)
    assert "MD_ASSET_MISSING" in errs and "MD_IMAGE_ALT" in errs


def test_wrong_case_asset(tree):
    d = tree / "KV7015" / "U01" / "T01" / "assets"
    d.mkdir()
    (d / "Chart.png").write_bytes(b"x")
    write(tree, topic_md().replace("- Point one", "![A chart](assets/chart.png)\n- Point one", 1))
    errs, _ = run(tree)
    # On a case-insensitive filesystem the file "exists"; the exact-name check must still catch it.
    assert "FS_WRONG_CASE" in errs or "MD_ASSET_MISSING" in errs


def test_mandarin_parity_and_no_narration(tree):
    write(tree, topic_md(lang="zh", say=False), "topic.zh.md")
    errs, _ = run(tree, "zh")
    assert errs == []
    write(tree, topic_md(lang="zh", say=False, slides=5), "topic.zh.md")
    assert "MD_PARITY_COUNT" in run(tree, "zh")[0]
    write(tree, topic_md(lang="zh", say=True), "topic.zh.md")
    assert "MD_SAY_IN_ZH" in run(tree, "zh")[0]


def test_mandarin_skips_english_checks(tree):
    # A zh file never gets word counts or deictic checks, even with odd minutes.
    write(tree, topic_md(lang="zh", say=False, minutes=40), "topic.zh.md")
    errs, _ = run(tree, "zh")
    assert "MD_WORD_COUNT" not in errs and "MD_SAY_MISSING" not in errs


def test_chinese_dates(tree):
    write(tree, topic_md(lang="zh", say=False).replace("- Point one", "- 2026年10月2日截止", 1), "topic.zh.md")
    assert "MD_DATE" in run(tree, "zh")[0]
