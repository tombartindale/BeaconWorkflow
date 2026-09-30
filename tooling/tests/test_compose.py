"""bcn compose: the delivered video (full mode, default) and the checking draft (--draft),
and how package depends on it.
"""

from __future__ import annotations

import json

from bcn import srt, tools
from bcn.config import load
from bcn.markdown import parse
from bcn.media import probe
from bcn.tree import Topic

from .conftest import age, make_video
from .test_bumpers import _isolated_theme, _needs_tools
from .test_cli import bcn


def _build_up_to_cues(tree, capsys, t: Topic):
    """validate, render, then a real edited video and a matching SRT (same construction as
    test_cli.test_cues_end_to_end, one sentence per cue), so a genuine bcn cues run produces a
    high-confidence sheet. Everything compose needs, without re-testing the matching algorithm.
    """
    _needs_tools(tree)
    bcn(capsys, "validate", str(t.dir))
    code, env = bcn(capsys, "render", str(t.dir))
    assert code == 0, env["diagnostics"]

    p = parse(t.src("en"))
    blocks, time_ = [], 0.5
    for s in p.slides:
        for sentence in [x.strip() + "." for x in s.say_text.split(".") if x.strip()]:
            n = len(blocks) + 1
            dur = len(sentence.split()) / 2.5
            blocks.append(f"{n}\n00:{int(time_ // 60):02d}:{time_ % 60:06.3f} --> "
                          f"00:{int((time_ + dur) // 60):02d}:{(time_ + dur) % 60:06.3f}\n{sentence}\n")
            time_ += dur + 0.1
    srt_text = "\n".join(blocks).replace(".", ",", 0).replace(" --> ", " --> ")
    srt_text = srt_text.replace(".", ",").replace(",\n", ".\n")  # timecodes use commas, text keeps full stops
    (t.dir / "edit").mkdir(exist_ok=True)
    make_video(t.video, time_ + 2)
    t.srt("en").write_text(srt_text)
    for f in (t.src("en"), t.video, t.srt("en")):
        age(f, 30)
    code, env = bcn(capsys, "cues", str(t.dir))
    assert code == 0, env["diagnostics"]
    code, env = bcn(capsys, "subtitles", str(t.dir))
    assert code == 0, env["diagnostics"]


def _with_bumpers(tree, capsys, t: Topic) -> None:
    _isolated_theme(tree)
    code, env = bcn(capsys, "bumpers", str(t.dir))
    assert code == 0, env["diagnostics"]


def test_compose_full_mode_delivery_quality(tree, capsys):
    t = Topic(tree, "KV7015", "U01", "T01")
    _build_up_to_cues(tree, capsys, t)
    code, env = bcn(capsys, "compose", str(t.dir))
    assert code == 0, env["diagnostics"]
    r = env["results"][0]
    assert r["mode"] == "full" and r["subtitles"] == "sidecar"
    assert t.composed("en").is_file() and t.composed("en").stat().st_size > 0
    assert t.composed_srt("en").is_file()
    assert not t.draft("en").is_file(), "full mode writes composed.mp4, never draft.mp4"

    info = tools.locate(load(tree))
    v = probe(info, t.composed("en"))
    d = load(tree)["delivery"]
    assert (v.width, v.height) == (d["width"], d["height"])
    assert abs(v.fps - d["fps"]) < 0.5

    # No bumpers were made, so there is no offset and the sidecar's cues are unshifted.
    assert r["body_offset"] == 0.0
    cues = srt.parse_file(t.composed_srt("en"))
    original = srt.parse_file(t.srt("en"))
    assert [c.start for c in cues] == [c.start for c in original]


def test_compose_sidecar_shifted_by_intro_duration(tree, capsys):
    t = Topic(tree, "KV7015", "U01", "T01")
    _build_up_to_cues(tree, capsys, t)
    _with_bumpers(tree, capsys, t)
    code, env = bcn(capsys, "compose", str(t.dir))
    assert code == 0, env["diagnostics"]
    r = env["results"][0]
    assert r["bumpers"] == ["intro", "outro"]
    assert r["body_offset"] > 0

    info = tools.locate(load(tree))
    intro = probe(info, t.bumper("intro", "en"))
    assert abs(r["body_offset"] - intro.duration) < 0.05

    original = srt.parse_file(t.srt("en"))
    shifted = srt.parse_file(t.composed_srt("en"))
    assert len(shifted) == len(original)
    for o, s in zip(original, shifted):
        assert abs(s.start - (o.start + r["body_offset"])) < 0.01
        assert abs(s.end - (o.end + r["body_offset"])) < 0.01
        assert s.text == o.text  # only the timing moves

    # edit/master.srt itself is never touched.
    assert srt.parse_file(t.srt("en")) == original

    composed = probe(info, t.composed("en"))
    assert composed.duration > intro.duration  # intro + body + outro, joined


def test_compose_draft_mode_unchanged_behaviour(tree, capsys):
    t = Topic(tree, "KV7015", "U01", "T01")
    _build_up_to_cues(tree, capsys, t)
    code, env = bcn(capsys, "compose", str(t.dir), "--draft")
    assert code == 0, env["diagnostics"]
    r = env["results"][0]
    assert r["mode"] == "draft" and r["subtitles"] == "burned"
    assert t.draft("en").is_file()
    assert not t.composed("en").is_file() and not t.composed_srt("en").is_file()

    info = tools.locate(load(tree))
    v = probe(info, t.draft("en"))
    c = load(tree)["compose"]
    assert (v.width, v.height) == (c["draft_width"], c["draft_height"])


def test_compose_burn_subtitles_in_full_mode_skips_sidecar(tree, capsys):
    t = Topic(tree, "KV7015", "U01", "T01")
    _build_up_to_cues(tree, capsys, t)
    code, env = bcn(capsys, "compose", str(t.dir), "--burn-subtitles")
    assert code == 0, env["diagnostics"]
    r = env["results"][0]
    assert r["mode"] == "full" and r["subtitles"] == "burned"
    assert t.composed("en").is_file()
    assert not t.composed_srt("en").is_file()


def test_package_requires_full_compose(tree, capsys):
    t = Topic(tree, "KV7015", "U01", "T01")
    _build_up_to_cues(tree, capsys, t)

    # No compose yet.
    code, env = bcn(capsys, "package", str(t.dir))
    assert code != 0 and env["diagnostics"][0]["code"] == "STEP_PREREQUISITE"

    # A --draft compose exists but is never delivered.
    bcn(capsys, "compose", str(t.dir), "--draft")
    code, env = bcn(capsys, "package", str(t.dir))
    assert code != 0
    assert any(d["code"] == "STEP_PREREQUISITE" and "draft" in d["message"] for d in env["diagnostics"])

    # A full compose unblocks it.
    code, env = bcn(capsys, "compose", str(t.dir))
    assert code == 0, env["diagnostics"]
    code, env = bcn(capsys, "package", str(t.dir))
    assert code == 0, env["diagnostics"]
    assert (t.out / f"{t.id}.mp4").is_file()
    assert (t.out / f"{t.id}.en.srt").is_file()
    manifest = json.loads(t.manifest("en").read_text())
    assert manifest["body_offset"] == 0.0
    assert "transcode" not in manifest and "transcoded" not in env["results"][0]

    delivered = srt.parse_file(t.out / f"{t.id}.en.srt")
    composed_sidecar = srt.parse_file(t.composed_srt("en"))
    assert [(c.start, c.end) for c in delivered] == [(c.start, c.end) for c in composed_sidecar]


def test_package_delivers_composed_bumpers_and_offset(tree, capsys):
    t = Topic(tree, "KV7015", "U01", "T01")
    _build_up_to_cues(tree, capsys, t)
    _with_bumpers(tree, capsys, t)
    bcn(capsys, "compose", str(t.dir))
    code, env = bcn(capsys, "package", str(t.dir))
    assert code == 0, env["diagnostics"]
    manifest = json.loads(t.manifest("en").read_text())
    assert manifest["body_offset"] > 0
    assert sorted(manifest["bumpers_composited"]) == ["intro", "outro"]
    # Bumpers are still delivered as their own files too, beside the merged composed video.
    names = {f["name"] for f in manifest["files"]}
    assert f"{t.id}.intro.en.mp4" in names and f"{t.id}.outro.en.mp4" in names
    assert (t.out / f"{t.id}.intro.en.mp4").is_file()


def test_status_next_is_compose_before_package(tree, capsys):
    t = Topic(tree, "KV7015", "U01", "T01")
    _build_up_to_cues(tree, capsys, t)
    _, env = bcn(capsys, "status", str(t.dir))
    assert env["results"][0]["en"]["next"] == "compose"

    bcn(capsys, "compose", str(t.dir))
    _, env = bcn(capsys, "status", str(t.dir))
    assert env["results"][0]["en"]["next"] == "package"

    # A later touch of a compose input that cues does not itself depend on (the cue sheet
    # cues produces, which compose consumes) leaves compose's result older than its input,
    # without disturbing cues: status sends you back to compose specifically, not further up.
    for p in (t.composed("en"), t.composed_srt("en"), t.step_file("compose", "en")):
        age(p, 60)
    t.cues_csv.touch()
    _, env = bcn(capsys, "status", str(t.dir))
    rows = {a["key"]: a for a in env["results"][0]["artifacts"]}
    assert rows["build/composed.mp4"]["stale"] is True
    assert env["results"][0]["en"]["next"] == "compose"


def test_status_next_is_compose_for_draft_only(tree, capsys):
    """A --draft compose satisfies nothing about delivery: status must not treat it as done."""
    t = Topic(tree, "KV7015", "U01", "T01")
    _build_up_to_cues(tree, capsys, t)
    bcn(capsys, "compose", str(t.dir), "--draft")
    _, env = bcn(capsys, "status", str(t.dir))
    assert env["results"][0]["en"]["next"] == "compose"
