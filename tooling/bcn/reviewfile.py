"""review.json: human decisions that must survive as part of the content.

Lives in the topic folder beside topic.md, not in build/, because build/ is
disposable and these are not reproducible from anything else. Holds:

  cue_overrides   slide -> timecode set by hand where cues could not place a boundary
  transcripts     divergence id -> accept (SRT is fine) or correct (SRT text should read ...)
  acknowledged    finding fingerprint -> a person has judged this validation finding acceptable
"""

from __future__ import annotations

import re
from typing import Any

from . import fsutil
from .envelope import Fail, utcnow
from .tree import Topic

TC_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})(?:[.,](\d{1,3}))?$")


def parse_tc(s: str) -> float:
    m = TC_RE.match(s.strip())
    if not m:
        raise Fail("USAGE", f"'{s}' is not a timecode like 00:01:12.480.")
    h, mi, se, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(se) + int((ms or "0").ljust(3, "0")) / 1000


def load(t: Topic) -> dict[str, Any]:
    data = fsutil.read_json(t.review_file) if t.review_file.is_file() else None
    data = data if isinstance(data, dict) else {}
    data.setdefault("schema", 1)
    data.setdefault("cue_overrides", {})
    data.setdefault("transcripts", {})
    data.setdefault("acknowledged", {})
    return data


def save(t: Topic, data: dict[str, Any]) -> None:
    fsutil.write_json(t.review_file, data)


def overrides(t: Topic) -> dict[int, float]:
    return {int(k): parse_tc(v["timecode"]) for k, v in load(t)["cue_overrides"].items()}


def set_override(t: Topic, slide: int, tc: str, who: str) -> None:
    parse_tc(tc)
    data = load(t)
    data["cue_overrides"][str(slide)] = {"timecode": tc, "by": who, "at": utcnow()}
    save(t, data)


def unset_override(t: Topic, slide: int) -> None:
    data = load(t)
    data["cue_overrides"].pop(str(slide), None)
    save(t, data)


def decide(t: Topic, item: dict[str, Any], decision: str, text: str | None, who: str) -> None:
    data = load(t)
    data["transcripts"][item["id"]] = {
        "decision": decision,
        "text": text if decision == "correct" else None,
        "script": item.get("script"),
        "srt": item.get("srt"),
        "cue": item.get("cue"),
        "by": who,
        "at": utcnow(),
    }
    save(t, data)


def acknowledge(t: Topic, fingerprint: str, diag: dict[str, Any], note: str | None, who: str) -> None:
    data = load(t)
    data["acknowledged"][fingerprint] = {"code": diag.get("code"), "message": diag.get("message"), "note": note or None,
                                         "by": who, "at": utcnow()}
    save(t, data)


def unacknowledge(t: Topic, fingerprint: str) -> bool:
    data = load(t)
    found = data["acknowledged"].pop(fingerprint, None) is not None
    save(t, data)
    return found
