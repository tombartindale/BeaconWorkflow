"""SRT reading and writing. Timings are authoritative and are never altered here."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .envelope import Fail

TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")
TAG_RE = re.compile(r"<[^>]+>|\{\\[^}]*\}")


@dataclass
class Cue:
    index: int       # 1-based position in the file
    start: float     # seconds
    end: float
    text: str        # may contain newlines
    line: int        # line number of the timing line

    @property
    def plain(self) -> str:
        return TAG_RE.sub("", self.text).replace("\n", " ").strip()


def _secs(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def fmt_time(t: float, sep: str = ",") -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def fmt_cue_time(t: float) -> str:
    """cues.csv form: 00:01:12.480"""
    return fmt_time(t, ".")


def parse_text(text: str, rel: str = "master.srt") -> list[Cue]:
    lines = text.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[Cue] = []
    i = 0
    n = len(lines)
    while i < n:
        if not lines[i].strip():
            i += 1
            continue
        # Optional numeric index line, then the timing line.
        j = i
        if lines[j].strip().isdigit() and j + 1 < n:
            j += 1
        m = TIME_RE.match(lines[j])
        if not m:
            raise Fail("SRT_PARSE", f"Expected a cue timing line at line {j + 1}, found {lines[j].strip()[:60]!r}.",
                       file=rel, line=j + 1)
        start, end = _secs(*m.groups()[:4]), _secs(*m.groups()[4:])
        body = []
        k = j + 1
        while k < n and lines[k].strip():
            body.append(lines[k].rstrip())
            k += 1
        cues.append(Cue(len(cues) + 1, start, end, "\n".join(body), j + 1))
        i = k
    return cues


def parse_file(path: Path, rel: str | None = None) -> list[Cue]:
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise Fail("FS_MISSING", f"Cannot read {rel or path.name}: {e}", file=rel or path.name) from e
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    return parse_text(text, rel or path.name)


def to_srt(cues: list[Cue]) -> str:
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{fmt_time(c.start)} --> {fmt_time(c.end)}\n{c.text}\n")
    return "\n".join(out)


def to_vtt(cues: list[Cue]) -> str:
    out = ["WEBVTT", ""]
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{fmt_time(c.start, '.')} --> {fmt_time(c.end, '.')}\n{c.text}\n")
    return "\n".join(out)


def verify_head(path: Path) -> bool:
    """--verify: the first cue parses and its timecodes are well formed. Reads only the head."""
    with open(path, "rb") as f:
        head = f.read(4096).decode("utf-8-sig", errors="replace")
    for line in head.replace("\r", "").split("\n")[:6]:
        m = TIME_RE.match(line)
        if m:
            s, e = _secs(*m.groups()[:4]), _secs(*m.groups()[4:])
            return 0 <= s <= e and all(int(x) < 60 for x in (m.group(2), m.group(3), m.group(6), m.group(7)))
    return False
