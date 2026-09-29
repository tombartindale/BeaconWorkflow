import pytest

from bcn import srt
from bcn.envelope import Fail
from bcn.tree import conflict_of, is_noise, resolve, scan_topic


def test_srt_variants():
    text = "﻿1\r\n00:00:01,000 --> 00:00:02,500\r\n<i>Hello</i>\r\nthere\r\n\r\n00:00:03.000 --> 00:00:04.000\r\nNo index\r\n"
    c = srt.parse_text(text)
    assert [x.start for x in c] == [1.0, 3.0] and c[0].plain == "Hello there" and c[1].line == 6


def test_srt_bad_line():
    with pytest.raises(Fail) as e:
        srt.parse_text("1\nnot a time\nx\n")
    assert e.value.diagnostic.code == "SRT_PARSE" and e.value.diagnostic.line == 2


def test_noise_and_conflicts():
    for n in (".DS_Store", "._topic.md", "~$doc.docx", "x.swp", "Icon\r", "notes~"):
        assert is_noise(n)
    exp = {"topic.md", "master.srt"}
    assert conflict_of("topic-Toms-MacBook-Pro.md", exp) == "topic.md"
    assert conflict_of("topic (1).md", exp) == "topic.md"
    assert conflict_of("master (conflicted copy).srt", exp) == "master.srt"
    assert conflict_of("topic.zh.md", exp) is None
    assert conflict_of("notes.md", exp) is None


def test_scan_topic(tree):
    d = tree / "KV7015" / "U01" / "T01"
    (d / "topic-Laptop.md").write_text("x")
    (d / "notes.txt").write_text("x")
    (d / ".DS_Store").write_text("x")
    codes = {x.code: x.level for x in scan_topic(resolve(str(d)).topics[0])}
    assert codes == {"FS_SYNC_CONFLICT": "error", "FS_UNEXPECTED_FILE": "info"}


def test_resolve_levels(tree):
    assert resolve(str(tree)).level == "root"
    assert resolve(str(tree / "KV7015")).level == "module"
    t = resolve(str(tree / "KV7015" / "U01" / "T01"))
    assert t.level == "topic" and t.topics[0].id == "KV7015-U01-T01"
    with pytest.raises(Fail):
        resolve(str(tree.parent))
