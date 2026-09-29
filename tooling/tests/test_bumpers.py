"""bcn bumpers: title-card intro and outro, and how compose, package and status treat them."""

import json
import re
import shutil

import pytest

from bcn import tools
from bcn.commands.package import _bumpers
from bcn.config import BUNDLED_THEMES, load, load_theme
from bcn.envelope import Fail
from bcn.tree import Topic

from .conftest import age
from .test_cli import bcn


def _needs_tools(root):
    try:
        tools.require(load(root), "marp", "chrome", "ffmpeg")
    except Fail as e:
        pytest.skip(f"pinned tools not installed: {e.diagnostic.message}")


def _isolated_theme(tree):
    """A copy of the bundled theme under the test programme, without the project's own logo and
    background video, so the tests do not depend on what is in the real assets/ folder."""
    d = tree / "themes" / "default"
    shutil.copytree(BUNDLED_THEMES / "default", d)
    toml = d / "theme.toml"
    text = re.sub(r'(?m)^background_video = .*$', 'background_video = ""', toml.read_text())
    toml.write_text(re.sub(r'(?m)^logo = .*$', 'logo = ""', text))
    return toml


def test_bumpers_end_to_end(tree, capsys):
    _needs_tools(tree)
    _isolated_theme(tree)
    t = Topic(tree, "KV7015", "U01", "T01")
    code, env = bcn(capsys, "bumpers", str(t.dir))
    assert code == 0 and env["ok"] is True, env["diagnostics"]
    r = env["results"][0]
    assert r["title"] == "A topic title" and r["logo"] is False
    assert [d["code"] for d in env["diagnostics"]] == ["BUMPER_NO_LOGO"]
    cards = (t.bumper_card("intro", "en"), t.bumper_card("outro", "en"))
    for p in (*cards, t.bumper("intro", "en"), t.bumper("outro", "en")):
        assert p.is_file() and p.stat().st_size > 0
    assert cards[0].read_bytes() != cards[1].read_bytes(), "intro is the title, outro the logo: different cards"
    info = tools.locate(load(tree))
    from bcn.media import probe
    v = probe(info, t.bumper("intro", "en"))
    assert (v.width, v.height, round(v.fps)) == (1920, 1080, 25) and v.has_audio
    assert abs(v.duration - 5.0) < 0.1

    # Unchanged: skipped. Edited script: status marks the bumpers stale.
    code, env = bcn(capsys, "bumpers", str(t.dir))
    assert env["results"][0]["skipped"] is True
    for p in (*cards, t.bumper("intro", "en"), t.bumper("outro", "en")):
        age(p, 60)
    t.src("en").write_text(t.src("en").read_text().replace("A topic title", "A new title"))
    code, env = bcn(capsys, "status", str(t.dir))
    rows = {a["key"]: a for a in env["results"][0]["artifacts"]}
    assert rows[f"build/bumpers/{t.id}.intro.en.mp4"]["stale"] is True
    assert rows[f"build/bumpers/{t.id}.intro.zh.mp4"]["exists"] is False


def test_bumpers_need_a_title(tree, capsys):
    t = Topic(tree, "KV7015", "U01", "T01")
    t.src("en").write_text(t.src("en").read_text().replace("title: A topic title", "title: "))
    code, env = bcn(capsys, "bumpers", str(t.dir))
    assert code != 0 and "BUMPER_NO_TITLE" in [d["code"] for d in env["diagnostics"]]


def test_package_refuses_stale_bumpers(tree):
    t = Topic(tree, "KV7015", "U01", "T01")
    assert _bumpers(t, "en") == []  # none made: nothing required
    for kind in ("intro", "outro"):
        t.bumper(kind, "en").parent.mkdir(parents=True, exist_ok=True)
        t.bumper(kind, "en").write_bytes(b"x")
    with pytest.raises(Fail) as e:
        _bumpers(t, "en")  # files but no passing bumpers run
    assert e.value.diagnostic.code == "STEP_PREREQUISITE"
    t.step_file("bumpers", "en").write_text(json.dumps({"ok": True}))
    assert _bumpers(t, "en") == [t.bumper("intro", "en"), t.bumper("outro", "en")]


def test_theme_bumper_settings(tree):
    toml = _isolated_theme(tree)
    base = toml.read_text()
    assert load_theme(tree, "default").bumper["fade_seconds"] == 0.75
    toml.write_text(base.replace('logo = ""', 'logo = "missing.png"'))
    with pytest.raises(Fail, match="logo"):
        load_theme(tree, "default")
    toml.write_text(base.replace("fade_seconds = 0.75", "fade_seconds = 3"))
    with pytest.raises(Fail, match="twice fade_seconds"):
        load_theme(tree, "default")


def test_logo_must_be_drawable(tree):
    d = _isolated_theme(tree).parent
    base = (d / "theme.toml").read_text()
    (d / "logo.eps").write_text("%!PS-Adobe-3.1 EPSF-3.0")
    (d / "theme.toml").write_text(base.replace('logo = ""', 'logo = "logo.eps"'))
    with pytest.raises(Fail, match="PNG or SVG|cannot draw"):
        load_theme(tree, "default")
