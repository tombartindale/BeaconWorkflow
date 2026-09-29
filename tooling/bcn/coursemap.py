"""Module-level documents: course-map.md, assets.md, activity.md, assignment-*.md.

The formats are documented in the README. They are deliberately plain markdown
(headings, bullet lists and pipe tables) so they read well in SharePoint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .envelope import Diagnostic

LO_RE = re.compile(r"\bLO\d+\b")
LO_ITEM_RE = re.compile(r"^\s*[-*]\s+\*\*(LO\d+)\*\*[:.\s-]*(.*)$")
LO_ITEM_LOOSE_RE = re.compile(r"^\s*[-*]\s+\*\*([^*]+)\*\*")
UNIT_HEAD_RE = re.compile(r"^##\s+(\S+)\s*(.*)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
OUTSTANDING_DONE = {"delivered", "done", "received", "complete", "closed"}


@dataclass
class MapTopic:
    unit: str
    code: str
    title: str
    minutes: int | None
    outcomes: list[str]
    line: int


@dataclass
class CourseMap:
    module: str
    path: Path
    outcomes: dict[str, str] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    topics: list[MapTopic] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def topic_ids(self) -> list[str]:
        return [f"{self.module}-{t.unit}-{t.code}" for t in self.topics]

    def find(self, unit: str, code: str) -> MapTopic | None:
        for t in self.topics:
            if t.unit == unit and t.code == code:
                return t
        return None


def _headings(lines: list[str]) -> list[tuple[int, int, str]]:
    out = []
    in_fence = False
    for i, line in enumerate(lines):
        if line.startswith("```"):
            in_fence = not in_fence
        if in_fence:
            continue
        m = HEADING_RE.match(line)
        if m:
            out.append((i + 1, len(m.group(1)), m.group(2)))
    return out


def _table_rows(lines: list[str], start: int) -> list[tuple[int, list[str]]]:
    """Rows of the first pipe table at or after start (0-based), header row excluded."""
    i = start
    while i < len(lines) and not lines[i].lstrip().startswith("|"):
        if lines[i].startswith("#"):
            return []
        i += 1
    if i + 1 >= len(lines):
        return []
    rows = []
    i += 2  # header + separator
    while i < len(lines) and lines[i].lstrip().startswith("|"):
        rows.append((i + 1, [c.strip() for c in lines[i].strip().strip("|").split("|")]))
        i += 1
    return rows


def _check_headings(lines: list[str], required: list[str], rel: str, diags: list[Diagnostic]) -> None:
    present = {h[2].strip().lower() for h in _headings(lines)}
    for want in required:
        if not any(p == want.lower() or p.endswith(" " + want.lower()) for p in present):
            diags.append(Diagnostic("DOC_HEADING_MISSING", f"{rel} has no '{want}' heading.", file=rel,
                                    hint=f"Add a '## {want}' section."))


def load_course_map(module_dir: Path, required_headings: list[str] | None = None) -> CourseMap:
    module = module_dir.name
    path = module_dir / "course-map.md"
    rel = f"{module}/course-map.md"
    cm = CourseMap(module, path)
    if not path.is_file():
        cm.diagnostics.append(Diagnostic("DOC_MISSING", f"{rel} does not exist.", file=rel))
        return cm
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    _check_headings(lines, required_headings or ["Learning outcomes"], rel, cm.diagnostics)

    section = None
    unit = None
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m and len(m.group(1)) == 2:
            text = m.group(2).strip()
            if text.lower().startswith("learning outcomes"):
                section, unit = "outcomes", None
                continue
            um = UNIT_HEAD_RE.match(line)
            first = um.group(1) if um else ""
            if re.match(r"^U\d{2}$", first):
                section, unit = "unit", first
                if unit in cm.units:
                    cm.diagnostics.append(Diagnostic("DOC_DUPLICATE_ID", f"Unit {unit} is listed twice.", file=rel, line=i + 1))
                cm.units[unit] = um.group(2).strip(" :-—")
                for ln, cells in _table_rows(lines, i + 1):
                    _map_row(cm, unit, cells, ln, rel)
                continue
            if re.match(r"^u\d+$", first, re.IGNORECASE):
                cm.diagnostics.append(Diagnostic("DOC_ID_FORMAT", f"Unit id '{first}' must be U followed by two digits.",
                                                 file=rel, line=i + 1))
            section, unit = None, None
            continue
        if section == "outcomes":
            om = LO_ITEM_RE.match(line)
            if om:
                if om.group(1) in cm.outcomes:
                    cm.diagnostics.append(Diagnostic("DOC_DUPLICATE_ID", f"Outcome {om.group(1)} is listed twice.",
                                                     file=rel, line=i + 1))
                cm.outcomes[om.group(1)] = om.group(2).strip()
            elif (lm := LO_ITEM_LOOSE_RE.match(line)):
                cm.diagnostics.append(Diagnostic("DOC_ID_FORMAT", f"Outcome id '{lm.group(1)}' must be LO followed by a number.",
                                                 file=rel, line=i + 1))
    for t in cm.topics:
        for lo in t.outcomes:
            if lo not in cm.outcomes:
                cm.diagnostics.append(Diagnostic("DOC_OUTCOME_UNKNOWN", f"{t.unit}/{t.code} cites {lo}, which is not a listed outcome.",
                                                 file=rel, line=t.line))
    return cm


def _map_row(cm: CourseMap, unit: str, cells: list[str], ln: int, rel: str) -> None:
    if not cells or not cells[0]:
        return
    code = cells[0]
    if not re.match(r"^T\d{2}$", code):
        cm.diagnostics.append(Diagnostic("DOC_ID_FORMAT", f"Topic id '{code}' must be T followed by two digits.", file=rel, line=ln))
        return
    if cm.find(unit, code):
        cm.diagnostics.append(Diagnostic("DOC_DUPLICATE_ID", f"{unit}/{code} is listed twice.", file=rel, line=ln))
        return
    title = cells[1] if len(cells) > 1 else ""
    minutes = None
    if len(cells) > 2 and cells[2]:
        try:
            minutes = int(cells[2])
        except ValueError:
            cm.diagnostics.append(Diagnostic("DOC_ID_FORMAT", f"Minutes '{cells[2]}' for {unit}/{code} is not a whole number.",
                                             file=rel, line=ln))
    outcomes = LO_RE.findall(cells[3]) if len(cells) > 3 else []
    cm.topics.append(MapTopic(unit, code, title, minutes, outcomes, ln))


@dataclass
class AssetRequest:
    asset: str
    topics: list[str]
    status: str
    line: int

    @property
    def outstanding(self) -> bool:
        return self.status.strip().lower() not in OUTSTANDING_DONE


def load_assets(module_dir: Path) -> list[AssetRequest]:
    path = module_dir / "assets.md"
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    out: list[AssetRequest] = []
    for i, line in enumerate(lines):
        if line.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1]):
            header = [c.strip().lower() for c in line.strip().strip("|").split("|")]
            try:
                ia, it, is_ = header.index("asset"), header.index("topic"), header.index("status")
            except ValueError:
                continue
            for ln, cells in _table_rows(lines, i):
                if len(cells) <= max(ia, it, is_):
                    continue
                topics = re.findall(r"[A-Z]{2}\d{4}-U\d{2}-T\d{2}", cells[it])
                out.append(AssetRequest(cells[ia].strip("` "), topics, cells[is_], ln))
    return out


def validate_doc(path: Path, rel: str, required: list[str], outcomes: dict[str, str], unit: str | None = None) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    _check_headings(lines, required, rel, diags)
    for i, line in enumerate(lines):
        for lo in LO_RE.findall(line):
            if outcomes and lo not in outcomes:
                diags.append(Diagnostic("DOC_OUTCOME_UNKNOWN", f"{rel} cites {lo}, which is not in the course map.", file=rel, line=i + 1))
        for bad in re.findall(r"\b[Uu]\d{1}\b|\bu\d{2}\b", line):
            diags.append(Diagnostic("DOC_ID_FORMAT", f"'{bad}' is not a well-formed unit id (U followed by two digits).",
                                    file=rel, line=i + 1))
        for uid in re.findall(r"\bU\d{2}\b", line):
            if unit and uid != unit and path.name == "activity.md" and i < 3:
                diags.append(Diagnostic("DOC_ID_FORMAT", f"{rel} names {uid} but lives in {unit}.", file=rel, line=i + 1))
    return diags
