"""Reading cues.csv. One row per slide, ascending, first row zero."""

from __future__ import annotations

import csv
from pathlib import Path

from .envelope import Fail
from .reviewfile import parse_tc


def read(path: Path, expected_slides: int | None = None, rel: str = "cues.csv") -> list[float]:
    try:
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
    except OSError as e:
        raise Fail("FS_MISSING", f"Cannot read {rel}: {e}", file=rel) from e
    if not rows or [c.strip() for c in rows[0]] != ["slide", "timecode"]:
        raise Fail("CUE_SHEET_INVALID", f"{rel} must start with the header slide,timecode.", file=rel, line=1)
    times: list[float] = []
    for i, row in enumerate(rows[1:], 2):
        if not row:
            continue
        if len(row) != 2 or not row[0].strip().isdigit() or int(row[0]) != len(times) + 1:
            raise Fail("CUE_SHEET_INVALID", f"{rel} row {i} should be slide {len(times) + 1}.", file=rel, line=i)
        try:
            t = parse_tc(row[1])
        except Fail:
            raise Fail("CUE_SHEET_INVALID", f"{rel} row {i} has a malformed timecode '{row[1]}'.", file=rel, line=i)
        if times and t <= times[-1]:
            raise Fail("CUE_SHEET_INVALID", f"{rel} row {i} is not later than the row before.", file=rel, line=i)
        times.append(t)
    if not times or times[0] != 0:
        raise Fail("CUE_SHEET_INVALID", f"{rel} must begin at 00:00:00.000.", file=rel, line=2)
    if expected_slides is not None and len(times) != expected_slides:
        raise Fail("CUE_SHEET_INVALID", f"{rel} has {len(times)} rows; the source has {expected_slides} slides.", file=rel)
    return times
