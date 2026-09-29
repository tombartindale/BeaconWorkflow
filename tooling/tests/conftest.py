"""Fixtures: a minimal, valid programme tree built fresh for each test."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SAY = ("This is narration sentence number {n} for slide {s}, written so that it reads naturally and "
       "carries enough words to pass the per slide limits without trouble. It explains the idea on the slide "
       "in plain terms, gives one example, and then moves on to the next point in the argument.")


def topic_md(topic_id: str = "KV7015-U01-T01", slides: int = 6, minutes: int = 4, lang: str = "en",
             say: bool = True, title: str = "A topic title") -> str:
    parts = [f"---\ntopic_id: {topic_id}\ntitle: {title}\nminutes: {minutes}\nlang: {lang}\n---\n"]
    for s in range(1, slides + 1):
        body = f"# Slide {s} heading\n\n- Point one\n- Point two\n"
        if say:
            body += "\n> **Say:** " + " ".join(SAY.format(n=k, s=s) for k in (1,)) + " " + SAY.format(n=2, s=s) + "\n"
        parts.append(body)
    return "\n---\n\n".join(parts)


COURSE_MAP = """# KV7015 Test module

## Learning outcomes

- **LO1** Do the first thing.
- **LO2** Do the second thing.

## U01 First unit

| Topic | Title | Minutes | Outcomes |
| --- | --- | --- | --- |
| T01 | A topic title | 4 | LO1 |
| T02 | Another | 3 | LO2 |
"""


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "prog"
    (root / "KV7015" / "U01" / "T01").mkdir(parents=True)
    (root / "programme.toml").write_text('[programme]\nname = "test"\n')
    (root / "KV7015" / "course-map.md").write_text(COURSE_MAP)
    (root / "KV7015" / "U01" / "activity.md").write_text("# U01 Activity\n\n## Task\n\nDo it.\n\n## Outcomes\n\nLO1\n")
    (root / "KV7015" / "U01" / "T01" / "topic.md").write_text(topic_md())
    return root


def age(path: Path, seconds: float) -> None:
    """Move a file's mtime into the past."""
    t = time.time() - seconds
    os.utime(path, (t, t))


def make_video(path: Path, seconds: float) -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s=64x36:r=5:d={seconds}",
                    "-f", "lavfi", "-i", f"anullsrc=r=8000:cl=mono", "-t", str(seconds), "-c:v", "libx264", "-preset",
                    "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)
