"""Script-to-SRT alignment: the heart of bcn cues.

A text-to-text alignment over normalised tokens. The recording is expected to
track the script almost word for word, so the defaults are strict and every
divergence is reported and classified:

  cut                a span of script absent from the SRT (expected where a mistake was removed)
  mistranscription   a short span that differs but sounds alike (auto-captions mishearing jargon)
  paraphrase         a comparable span with different wording (the presenter went off script)
  insertion          words in the SRT that are not in the script

Timing resolution is cue-level: each slide starts at the start of the cue
containing the first surviving word after its marker. No interpolation.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher

from .srt import Cue
from .text import sounds_alike, surface_span, tokens


@dataclass
class Boundary:
    slide: int
    timecode: float | None
    confidence: float
    source: str = "matched"  # matched | manual | first
    cue: int | None = None
    script_token: int | None = None
    srt_token: int | None = None
    skipped_words: int = 0
    mid_cue: bool = False
    window_match: float = 1.0
    reason: str = ""

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Divergence:
    id: str
    kind: str
    slide: int
    script: str
    srt: str
    cue: int | None
    time: float | None
    script_tokens: int
    srt_tokens: int
    spans_boundary: bool = False
    boundary_slides: list[int] = field(default_factory=list)
    similarity: float | None = None

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Alignment:
    boundaries: list[Boundary]
    divergences: list[Divergence]
    script_tokens: int
    srt_tokens: int
    divergence_ratio: float
    matched_ratio: float
    narration: list[str] = field(default_factory=list)


def _div_id(kind: str, a: list[str], b: list[str]) -> str:
    h = hashlib.sha1(f"{kind}|{' '.join(a)}|{' '.join(b)}".encode()).hexdigest()[:10]
    return f"{kind[:2]}-{h}"


def _coalesce(ops: list[tuple[str, int, int, int, int]], gap: int) -> list[tuple[str, int, int, int, int]]:
    """Merge differences separated by at most `gap` matching tokens into one region.

    difflib splits a reworded sentence into scattered replace/insert/delete
    fragments around incidental shared words ("the", "in"). A human reads it as
    one paraphrase, so classify it as one.
    """
    out: list[list] = []
    for tag, i1, i2, j1, j2 in ops:
        if tag != "equal" and out and out[-1][0] != "equal":
            prev = out.pop()
            out.append(["replace", prev[1], i2, prev[3], j2])
        elif (tag != "equal" and len(out) >= 2 and out[-1][0] == "equal"
              and out[-1][2] - out[-1][1] <= gap and out[-2][0] != "equal"):
            out.pop()
            prev = out.pop()
            out.append(["replace", prev[1], i2, prev[3], j2])
        else:
            out.append([tag, i1, i2, j1, j2])
    result = []
    for tag, i1, i2, j1, j2 in out:
        if tag == "replace" and i1 == i2:
            tag = "insert"
        elif tag == "replace" and j1 == j2:
            tag = "delete"
        result.append((tag, i1, i2, j1, j2))
    return result


def align(narration: list[str], cues: list[Cue], *, window: int = 8, mis_max_tokens: int = 5,
          mis_similarity: float = 0.6, overrides: dict[int, float] | None = None) -> Alignment:
    overrides = overrides or {}
    # Script side: tokens with slide provenance.
    s_tok: list[str] = []
    s_owner: list[int] = []
    s_surface: list[str] = []
    s_slide: list[int] = []
    markers: list[int] = []
    for i, text in enumerate(narration, 1):
        markers.append(len(s_tok))
        toks, owner, surf = tokens(text)
        base = len(s_surface)
        s_tok += toks
        s_owner += [o + base for o in owner]
        s_surface += surf
        s_slide += [i] * len(toks)

    # SRT side: tokens with cue provenance.
    r_tok: list[str] = []
    r_owner: list[int] = []
    r_surface: list[str] = []
    r_cue: list[int] = []
    r_first: list[bool] = []
    for ci, c in enumerate(cues):
        toks, owner, surf = tokens(c.plain)
        base = len(r_surface)
        r_first += [k == 0 for k in range(len(toks))]
        r_tok += toks
        r_owner += [o + base for o in owner]
        r_surface += surf
        r_cue += [ci] * len(toks)

    sm = SequenceMatcher(None, s_tok, r_tok, autojunk=False)
    ops = sm.get_opcodes()
    match: dict[int, int] = {}
    for tag, i1, i2, j1, _j2 in ops:
        if tag == "equal":
            for k in range(i2 - i1):
                match[i1 + k] = j1 + k

    def matched_in(lo: int, hi: int) -> float:
        lo, hi = max(0, lo), min(len(s_tok), hi)
        if hi <= lo:
            return 0.0
        return sum(1 for i in range(lo, hi) if i in match) / (hi - lo)

    # -- divergences --------------------------------------------------------------
    divs: list[Divergence] = []
    diff_script = 0

    def slide_of(i: int) -> int:
        if not s_slide:
            return 1
        return s_slide[min(i, len(s_slide) - 1)]

    def spans(i1: int, i2: int) -> list[int]:
        """Slides whose break lies strictly inside [i1, i2)."""
        return [k for k, m in enumerate(markers, 1) if k > 1 and i1 < m < i2]

    for tag, i1, i2, j1, j2 in _coalesce(ops, gap=2):
        if tag == "equal":
            continue
        a, b = s_tok[i1:i2], r_tok[j1:j2]
        la, lb = len(a), len(b)
        if tag == "delete":
            kind = "cut"
        elif tag == "insert":
            kind = "insertion"
        elif la >= 4 and la >= 3 * lb:
            kind = "cut"
        elif max(la, lb) <= mis_max_tokens and sounds_alike(a, b) >= mis_similarity:
            kind = "mistranscription"
        elif lb >= 4 and lb >= 3 * la:
            kind = "insertion"
        else:
            kind = "paraphrase"
        if tag != "insert":
            diff_script += la
        cue_idx = r_cue[j1] if j1 < len(r_cue) else (r_cue[j1 - 1] if j1 > 0 and r_cue else None)
        sim = round(sounds_alike(a, b), 2) if a and b else None
        divs.append(Divergence(
            id=_div_id(kind, a, b),
            kind=kind,
            slide=slide_of(i1),
            script=surface_span(s_surface, s_owner, i1, i2) if la else "",
            srt=surface_span(r_surface, r_owner, j1, j2) if lb else "",
            cue=(cue_idx + 1) if cue_idx is not None else None,
            time=cues[cue_idx].start if cue_idx is not None else None,
            script_tokens=la,
            srt_tokens=lb,
            spans_boundary=tag != "insert" and bool(spans(i1, i2)),
            boundary_slides=spans(i1, i2) if tag != "insert" else [],
            similarity=sim,
        ))

    # -- boundaries ---------------------------------------------------------------
    bounds: list[Boundary] = []
    for k, m in enumerate(markers, 1):
        if k in overrides:
            bounds.append(Boundary(k, overrides[k], 1.0, source="manual", reason="set by hand"))
            continue
        if k == 1:
            bounds.append(Boundary(1, 0.0, 1.0, source="first", reason="first row is always zero"))
            continue
        end = markers[k] if k < len(markers) else len(s_tok)
        i = next((x for x in range(m, end) if x in match), None)
        if i is None:
            bounds.append(Boundary(k, None, 0.0, script_token=m,
                                   reason="no word of this slide's narration survives in the SRT"))
            continue
        j = match[i]
        ci = r_cue[j]
        win = matched_in(m - window, m + window)
        conf = win
        reason = []
        skipped = i - m
        if skipped:
            if m - 1 >= 0 and (m - 1) not in match:
                conf *= 0.3
                reason.append(f"a cut spans the slide break ({skipped} words of this slide and the end of the previous one are missing)")
            else:
                conf *= max(0.6, 1 - 0.04 * skipped)
                reason.append(f"the first {skipped} words of this slide were cut")
        mid = not r_first[j]
        if mid:
            conf *= 0.85
            reason.append("the break falls part way through a cue")
        bounds.append(Boundary(k, cues[ci].start, round(conf, 3), cue=ci + 1, script_token=m, srt_token=j,
                               skipped_words=skipped, mid_cue=mid, window_match=round(win, 3),
                               reason="; ".join(reason)))

    # Two slides cannot start at the same moment.
    last = None
    for b in bounds:
        if b.timecode is None:
            continue
        if last is not None and b.timecode <= last and b.source != "manual":
            b.confidence = min(b.confidence, 0.2)
            b.reason = (b.reason + "; " if b.reason else "") + "starts no later than the previous slide"
        last = b.timecode if last is None else max(last, b.timecode)

    total = len(s_tok) or 1
    return Alignment(bounds, divs, len(s_tok), len(r_tok), round(diff_script / total, 4),
                     round(len(match) / total, 4), narration)
