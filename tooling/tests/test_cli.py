"""End-to-end behaviour of the CLI contract: envelope shape, exit codes, state on disk."""

import json
from pathlib import Path

from bcn.cli import main

from .conftest import age, make_video, topic_md


def bcn(capsys, *args):
    code = main([*args, "--quiet"])
    out = capsys.readouterr().out
    return code, json.loads(out)


def test_envelope_shape_and_step_file(tree, capsys):
    code, env = bcn(capsys, "validate", str(tree / "KV7015/U01/T01"))
    assert code == 0 and env["ok"] is True
    for k in ("tool", "schema", "target", "ok", "started", "duration_ms", "results", "artifacts", "diagnostics"):
        assert k in env
    assert env["target"] == "KV7015/U01/T01"
    step = json.loads((tree / "KV7015/U01/T01/build/validate.json").read_text())
    assert step["ok"] is True and step["results"][0]["topic"] == "KV7015-U01-T01"


def test_directory_run_continues_past_failures(tree, capsys):
    t2 = tree / "KV7015/U01/T02"
    t2.mkdir()
    (t2 / "topic.md").write_text(topic_md("KV7015-U01-T02", slides=2))
    code, env = bcn(capsys, "validate", str(tree / "KV7015"))
    assert code == 1 and env["ok"] is False
    assert [r["ok"] for r in env["results"]] == [True, False]


def test_usage_error_is_an_envelope(capsys):
    code = main(["validate"])
    env = json.loads(capsys.readouterr().out)
    assert code == 2 and env["diagnostics"][0]["code"] == "USAGE"


def test_missing_input_exit_code(tree, capsys):
    code, env = bcn(capsys, "cues", str(tree / "KV7015/U01/T01"))
    assert code == 3 and env["diagnostics"][0]["code"] in ("FS_MISSING", "STEP_PREREQUISITE")


def test_cues_refuses_mandarin(tree, capsys):
    code, env = bcn(capsys, "cues", str(tree / "KV7015/U01/T01"), "--lang", "zh")
    assert code == 2


def test_status_stages_and_staleness(tree, capsys):
    t = tree / "KV7015/U01/T01"
    _, env = bcn(capsys, "status", str(tree))
    rows = {r["topic"]: r for r in env["results"]}
    assert rows["KV7015-U01-T01"]["en"]["stage"] == "drafted"
    assert rows["KV7015-U01-T01"]["en"]["next"] == "validate"
    assert rows["KV7015-U01-T02"]["en"]["stage"] == "planned"  # in the course map, no directory
    age(t / "topic.md", 60)
    bcn(capsys, "validate", str(t))
    _, env = bcn(capsys, "status", str(t))
    en = env["results"][0]["en"]
    assert en["stage"] == "validated" and not en["stale"] and en["next"] == "render"
    # Editing the source after validation leaves the stage but marks it stale.
    (t / "topic.md").write_text(topic_md() + "\n")
    _, env = bcn(capsys, "status", str(t))
    en = env["results"][0]["en"]
    assert en["stage"] == "validated" and en["stale"] and en["next"] == "validate"
    assert not list((t / "build").glob("status*.json")), "status must never write"


def test_status_blocks_on_sync_conflict(tree, capsys):
    (tree / "KV7015/U01/T01/topic-Laptop.md").write_text("x")
    _, env = bcn(capsys, "status", str(tree / "KV7015/U01/T01"))
    en = env["results"][0]["en"]
    assert en["blocked"] and en["blockers"][0]["code"] == "FS_SYNC_CONFLICT"


def test_cues_end_to_end(tree, capsys):
    t = tree / "KV7015/U01/T01"
    # An SRT that tracks the script exactly, one cue per sentence.
    from bcn.markdown import parse
    p = parse(t / "topic.md")
    blocks, time_ = [], 0.5
    for s in p.slides:
        for sentence in [x.strip() + "." for x in s.say_text.split(".") if x.strip()]:
            n = len(blocks) + 1
            dur = len(sentence.split()) / 2.5
            blocks.append(f"{n}\n00:{int(time_ // 60):02d}:{time_ % 60:06.3f} --> 00:{int((time_ + dur) // 60):02d}:{(time_ + dur) % 60:06.3f}\n{sentence}\n".replace(".", ",", 0))
            time_ += dur + 0.1
    srt_text = "\n".join(b.replace(" --> ", " --> ") for b in blocks)
    srt_text = srt_text.replace(".", ",").replace(",\n", ".\n")  # timecodes use commas, text keeps its full stops
    (t / "edit").mkdir()
    make_video(t / "edit/master.mp4", time_ + 2)
    (t / "edit/master.srt").write_text(srt_text)
    for f in ("topic.md", "edit/master.mp4", "edit/master.srt"):
        age(t / f, 30)
    bcn(capsys, "validate", str(t))
    code, env = bcn(capsys, "cues", str(t))
    assert code == 0, env["diagnostics"]
    csv = (t / "build/KV7015-U01-T01.cues.csv").read_text().splitlines()
    assert csv[0] == "slide,timecode" and csv[1] == "1,00:00:00.000" and len(csv) == 7
    assert env["results"][0]["min_confidence"] == 1.0
    # Idempotent: a second run skips and leaves the sheet as it was.
    before = (t / "build/KV7015-U01-T01.cues.csv").stat().st_mtime
    code, env = bcn(capsys, "cues", str(t))
    assert code == 0 and env["results"][0]["skipped"] is True
    assert (t / "build/KV7015-U01-T01.cues.csv").stat().st_mtime == before

    # A mishearing is reported, reviewed, and corrected in the delivered subtitles.
    assert "sentence number 1 for slide 3" in srt_text
    (t / "edit/master.srt").write_text(srt_text.replace("sentence number 1 for slide 3", "sentence lumber 1 for slide 3", 1))
    for f in ("edit/master.mp4", "edit/master.srt"):
        age(t / f, 10)
    for f in ("build/cues.json", "build/cues-report.json", "build/KV7015-U01-T01.cues.csv"):
        age(t / f, 20)  # the previous result predates the new SRT
    code, env = bcn(capsys, "cues", str(t))
    items = [d for d in env["diagnostics"] if d["code"] == "CUE_MISTRANSCRIPTION"]
    assert items and items[0]["data"]["script"] == "number" and items[0]["data"]["srt"] == "lumber"
    code, env = bcn(capsys, "review", str(t), "--item", items[0]["data"]["id"], "--correct", "number", "--by", "test")
    assert env["results"][0]["unreviewed"] == 0
    code, env = bcn(capsys, "subtitles", str(t))
    assert code == 0 and env["results"][0]["modified"] is True
    out = (t / "build/subtitles/KV7015-U01-T01.en.srt").read_text()
    assert "lumber" not in out and "number 1 for slide 3" in out


def test_schema_and_codes(capsys):
    code, env = bcn(capsys, "schema", "status")
    assert code == 0 and env["json_schema"]["properties"]["tool"]["const"] == "status"
    code, env = bcn(capsys, "codes")
    assert any(c["code"] == "CUE_LOW_CONFIDENCE" for c in env["codes"])


def test_intake_refuses_to_overwrite(tree, capsys):
    paste = tree.parent / "paste.md"
    paste.write_text("```markdown\n" + topic_md().replace("Point one", "Point uno") + "```\n")
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(paste))
    r = env["results"][0]
    assert code == 1 and r["action"] == "exists_differs" and "Point uno" in r["diff"]
    assert "Point uno" not in (tree / "KV7015/U01/T01/topic.md").read_text()
    paste.write_text(topic_md("KV7015-U01-T02"))
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(paste))
    assert env["results"][0]["action"] == "written" and (tree / "KV7015/U01/T02/topic.md").is_file()
    paste.write_text(topic_md("KV7015-U09-T01"))
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(paste))
    assert env["results"][0]["action"] == "refused"
