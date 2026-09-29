#!/usr/bin/env python3
"""Generate synthetic editor deliveries (edit/master.mp4 + edit/master.srt) for the example tree.

Stands in for the external editor so the pipeline can be exercised end to end.
The SRT is built from the script's narration at speaking pace, with realistic
edits applied: a removed mistake, auto-caption mishearings, a paraphrase, and a
cut across a slide break. The video is a test pattern with a running timecode.

usage: python3 scripts/make_example_media.py example/
"""

import re
import subprocess
import sys
from pathlib import Path

WPS = 2.45  # words per second, roughly 145 wpm

EDITS = {
    "KV7015-U01-T01": [
        # removed mistake: a benign cut inside slide 3
        ("cut", "Being specific about who you are studying tells you who to recruit, and it tells your reader who your findings apply to. ", ""),
        # auto-captions mishearing jargon
        ("sub", "PICO stands for", "Peek oh stands for"),
        ("sub", "a Likert scale", "a like it scale"),
        # presenter departing from the script
        ("sub", "Is it focused enough to finish in the time you have?", "Is it narrow enough that you can actually get it done in time?"),
    ],
    "KV7015-U01-T02": [
        # a cut spanning the break between slides 4 and 5: needs a human
        ("cut", "Trim the question until it fits.\n\nIt also helps to know what kind of question you are asking. ", ""),
    ],
}


def narration(md: str) -> list[str]:
    return [m.strip() for m in re.findall(r"^> \*\*Say:\*\* (.*)$", md, re.M)]


def cues_for(text: str) -> list[str]:
    sentences = re.findall(r"[^.?!]+[.?!]+", text)
    out = []
    for s in sentences:
        s = s.strip()
        words = s.split()
        while len(" ".join(words)) > 84:
            # split long sentences at a comma or midpoint, as captioners do
            cut = next((i + 1 for i, w in enumerate(words[: len(words) - 3]) if w.endswith(",") and i > 4), len(words) // 2)
            out.append(" ".join(words[:cut]))
            words = words[cut:]
        out.append(" ".join(words))
    return out


def wrap(text: str, width: int = 42) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    lines.append(cur)
    return "\n".join(lines[:2]) if len(lines) <= 2 else lines[0] + "\n" + " ".join(lines[1:])


def tc(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def make(topic_dir: Path) -> None:
    md = (topic_dir / "topic.md").read_text()
    tid = re.search(r"^topic_id:\s*(\S+)", md, re.M).group(1)
    spoken = "\n\n".join(narration(md))
    for kind, a, b in EDITS.get(tid, []):
        assert a in spoken, (tid, a)
        spoken = spoken.replace(a, b)
    t = 0.6
    entries = []
    for para in spoken.split("\n\n"):
        for c in cues_for(para):
            dur = max(1.2, len(c.split()) / WPS)
            entries.append((t, t + dur, wrap(c)))
            t += dur + 0.12
        t += 0.5  # pause between slides
    duration = t + 1.5
    edit = topic_dir / "edit"
    edit.mkdir(exist_ok=True)
    srt = "\n".join(f"{i}\n{tc(s)} --> {tc(e)}\n{txt}\n" for i, (s, e, txt) in enumerate(entries, 1))
    (edit / "master.srt").write_text(srt, encoding="utf-8")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc=size=960x540:rate=25:duration={duration:.2f}",
        "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={duration:.2f}",
        "-vf", "drawtext=text='PRESENTER %{pts\\:hms}':x=40:y=40:fontsize=40:fontcolor=white:box=1:boxcolor=black@0.6",
        "-af", "volume=0.05",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "36", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart",
        str(edit / "master.mp4"),
    ], check=True)
    # The editor exports the pair together; keep their mtimes together too.
    subprocess.run(["touch", "-r", str(edit / "master.mp4"), str(edit / "master.srt")], check=True)
    # A translator's return for any topic that has a sample Mandarin markdown waiting:
    # identical timings, translated text. Import it from the Translation page.
    returned = topic_dir.parents[2] / "translation" / "returned" / "sample"
    if (returned / f"{tid}.zh.md").is_file():
        zh = "\n".join(f"{i}\n{tc(s)} --> {tc(e)}\n第{i}句中文字幕\n" for i, (s, e, _) in enumerate(entries, 1))
        (returned / f"{tid}.zh.srt").write_text(zh, encoding="utf-8")
    print(f"{tid}: {len(entries)} cues, {duration:.1f}s")


if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "example")
    for d in sorted(root.glob("*/U*/T*")):
        if (d / "topic.md").is_file():
            make(d)
