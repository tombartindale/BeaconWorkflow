"""ffprobe and ffmpeg, with real progress from ffmpeg's own -progress output."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .envelope import Fail
from .progress import run as run_proc
from .tools import Tools


@dataclass
class MediaInfo:
    duration: float
    width: int | None
    height: int | None
    fps: float | None
    vcodec: str | None
    acodec: str | None
    sample_rate: int | None
    has_audio: bool


def probe(tools: Tools, path: Path, rel: str | None = None) -> MediaInfo:
    code, out, err = run_proc([tools.ffprobe or "ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                               "-show_streams", str(path)], timeout=120)
    if code != 0:
        raise Fail("FS_CORRUPT", f"ffprobe cannot read {rel or path.name}: {err.strip()[:200]}", file=rel or path.name)
    data = json.loads(out or "{}")
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    try:
        duration = float(data.get("format", {}).get("duration") or (v or {}).get("duration") or 0)
    except ValueError:
        duration = 0.0
    fps = None
    if v and v.get("avg_frame_rate") and v["avg_frame_rate"] != "0/0":
        n, _, d = v["avg_frame_rate"].partition("/")
        fps = float(n) / float(d or 1) if float(d or 1) else None
    return MediaInfo(duration, v.get("width") if v else None, v.get("height") if v else None, fps,
                     v.get("codec_name") if v else None, a.get("codec_name") if a else None,
                     int(a["sample_rate"]) if a and a.get("sample_rate") else None, a is not None)


def ffmpeg(tools: Tools, args: list[str], *, duration: float | None, on_pct: Callable[[float], None] | None = None,
           timeout: float | None = None, cwd: str | None = None) -> None:
    """Run ffmpeg, reporting percent complete from -progress against the known output duration."""

    def on_line(line: str) -> None:
        if on_pct and duration and line.startswith("out_time_us="):
            try:
                us = int(line.split("=", 1)[1])
            except ValueError:
                return
            on_pct(min(100.0, us / 1e6 / duration * 100))

    cmd = [tools.ffmpeg or "ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats", *args]
    code, out, err = run_proc(cmd, on_stdout_line=on_line, timeout=timeout, cwd=cwd)
    if code != 0:
        raise Fail("TOOL_FAILED", f"ffmpeg failed (exit {code}): {err.strip()[-400:]}", data={"cmd": cmd[-1], "stderr": err[-3000:]})


def loudness(tools: Tools, path: Path) -> float | None:
    """Integrated loudness (LUFS) of a file's audio, or None if it has none."""
    code, out, err = run_proc([tools.ffmpeg or "ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-vn",
                               "-af", "loudnorm=print_format=json", "-f", "null", "-"], timeout=1800)
    text = err
    start = text.rfind("{")
    end = text.rfind("}")
    if code != 0 or start < 0:
        return None
    try:
        v = float(json.loads(text[start:end + 1])["input_i"])
    except (ValueError, KeyError):
        return None
    return v if v > -70 else None
