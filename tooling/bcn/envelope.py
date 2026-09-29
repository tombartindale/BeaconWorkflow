"""The one output shape every command emits.

stdout carries exactly one envelope. --human formats that same object; it never
takes a different path through the code.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import codes

SCHEMA_VERSION = 1

EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_USAGE = 2
EXIT_MISSING = 3
EXIT_TOOL = 4
EXIT_CANCELLED = 5

# Which error codes map to which exit code. Anything unlisted is a validation failure.
_EXIT_FOR_CODE = {
    "USAGE": EXIT_USAGE,
    "PATH_OUTSIDE_ROOT": EXIT_USAGE,
    "ROOT_NOT_FOUND": EXIT_MISSING,
    "FS_MISSING": EXIT_MISSING,
    "FS_NOT_HYDRATED": EXIT_MISSING,
    "STEP_PREREQUISITE": EXIT_MISSING,
    "TOOL_MISSING": EXIT_TOOL,
    "TOOL_VERSION": EXIT_TOOL,
    "TOOL_FAILED": EXIT_TOOL,
    "RENDER_FAILED": EXIT_TOOL,
    "CANCELLED": EXIT_CANCELLED,
}
# When several classes of failure occur in one run, the most fundamental wins.
_EXIT_PRIORITY = [EXIT_CANCELLED, EXIT_USAGE, EXIT_TOOL, EXIT_MISSING, EXIT_VALIDATION]


def utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Diagnostic:
    code: str
    message: str
    level: str | None = None
    topic: str | None = None
    file: str | None = None
    line: int | None = None
    slide: int | None = None
    hint: str | None = None
    lang: str | None = None
    data: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.code not in codes.CODES:
            raise ValueError(f"unregistered diagnostic code {self.code}")
        if self.level is None:
            self.level = codes.level_of(self.code)

    def to_json(self) -> dict[str, Any]:
        d = {
            "level": self.level,
            "code": self.code,
            "topic": self.topic,
            "lang": self.lang,
            "file": self.file,
            "line": self.line,
            "slide": self.slide,
            "message": self.message,
            "hint": self.hint,
        }
        if self.data:
            d["data"] = self.data
        return d


class Fail(Exception):
    """Abort the current topic with a diagnostic. The run continues with the next topic."""

    def __init__(self, code: str, message: str, **kw: Any) -> None:
        super().__init__(message)
        self.diagnostic = Diagnostic(code, message, **kw)


class Cancelled(Exception):
    pass


@dataclass
class Artifact:
    path: str
    kind: str
    bytes: int
    sha256: str

    def to_json(self) -> dict[str, Any]:
        return {"path": self.path, "kind": self.kind, "bytes": self.bytes, "sha256": self.sha256}


@dataclass
class TopicResult:
    """Everything one command produced for one topic."""

    topic: str
    rel: str
    ok: bool = True
    skipped: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        d: dict[str, Any] = {"topic": self.topic, "ok": self.ok, "skipped": self.skipped}
        d.update(self.extra)
        return d


class Envelope:
    def __init__(self, tool: str, target: str, root: Path | None) -> None:
        self.tool = tool
        self.target = target
        self.root = root
        self.started = utcnow()
        self._t0 = time.monotonic()
        self.results: list[TopicResult] = []
        self.diagnostics: list[Diagnostic] = []  # run-level, not tied to a topic result
        self.artifacts: list[Artifact] = []
        self.extra: dict[str, Any] = {}
        self.cancelled = False

    # -- building -----------------------------------------------------------
    def rel(self, path: Path) -> str:
        if self.root is None:
            return str(path)
        try:
            return str(path.resolve().relative_to(self.root.resolve()))
        except ValueError:
            return str(path)

    def artifact(self, path: Path, kind: str) -> Artifact:
        return Artifact(self.rel(path), kind, path.stat().st_size, sha256_file(path))

    @staticmethod
    def artifact_for(root: Path, path: Path, kind: str) -> Artifact:
        return Artifact(str(path.relative_to(root)), kind, path.stat().st_size, sha256_file(path))

    def all_diagnostics(self) -> list[Diagnostic]:
        out = list(self.diagnostics)
        for r in self.results:
            out.extend(r.diagnostics)
        return out

    @property
    def ok(self) -> bool:
        if self.cancelled:
            return False
        if any(d.level == "error" for d in self.diagnostics):
            return False
        return all(r.ok for r in self.results)

    def exit_code(self) -> int:
        if self.ok:
            return EXIT_OK
        found = {_EXIT_FOR_CODE.get(d.code, EXIT_VALIDATION) for d in self.all_diagnostics() if d.level == "error"}
        if self.cancelled:
            found.add(EXIT_CANCELLED)
        for code in _EXIT_PRIORITY:
            if code in found:
                return code
        return EXIT_VALIDATION

    def to_json(self) -> dict[str, Any]:
        arts = list(self.artifacts)
        for r in self.results:
            arts.extend(r.artifacts)
        d: dict[str, Any] = {
            "tool": self.tool,
            "schema": SCHEMA_VERSION,
            "target": self.target,
            "ok": self.ok,
            "started": self.started,
            "duration_ms": int((time.monotonic() - self._t0) * 1000),
            "results": [r.to_json() for r in self.results],
            "artifacts": [a.to_json() for a in arts],
            "diagnostics": [x.to_json() for x in self.all_diagnostics()],
        }
        if self.cancelled:
            d["cancelled"] = True
        d.update(self.extra)
        return d

    def topic_envelope(self, r: TopicResult) -> dict[str, Any]:
        """The envelope as it would read had the command been run on this topic alone."""
        return {
            "tool": self.tool,
            "schema": SCHEMA_VERSION,
            "target": r.rel,
            "ok": r.ok,
            "started": self.started,
            "duration_ms": int((time.monotonic() - self._t0) * 1000),
            "results": [r.to_json()],
            "artifacts": [a.to_json() for a in r.artifacts],
            "diagnostics": [x.to_json() for x in r.diagnostics],
        }


# -- output -------------------------------------------------------------------

def emit(env: Envelope, human: bool) -> int:
    data = env.to_json()
    if human:
        sys.stdout.write(format_human(data))
    else:
        sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()
    return env.exit_code()


def _table(headers: list[str], rows: list[list[Any]], max_width: int = 70) -> str:
    def cell(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, bool):
            return "yes" if v else "no"
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        s = str(v).replace("\n", " ")
        return s if len(s) <= max_width else s[: max_width - 1] + "…"

    cells = [[cell(v) for v in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in cells:
        for i, c in enumerate(row):
            widths[i] = max(widths[i], len(c))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep = "  ".join("-" * w for w in widths)
    body = ["  ".join(c.ljust(widths[i]) for i, c in enumerate(row)) for row in cells]
    return "\n".join([line, sep, *body]) + "\n"


def format_human(data: dict[str, Any]) -> str:
    out = []
    status = "OK" if data["ok"] else ("CANCELLED" if data.get("cancelled") else "FAILED")
    out.append(f"bcn {data['tool']}  {data['target']}  {status}  ({data['duration_ms']} ms)\n\n")
    results = data.get("results") or []
    if results:
        keys: list[str] = []
        for r in results:
            for k, v in r.items():
                if k not in keys and not isinstance(v, (dict, list)):
                    keys.append(k)
        out.append(_table(keys, [[r.get(k) for k in keys] for r in results]))
        out.append("\n")
    diags = data.get("diagnostics") or []
    if diags:
        rows = [
            [d["level"], d["code"], d.get("topic"), d.get("lang"), d.get("file"), d.get("line"), d.get("slide"), d["message"]]
            for d in diags
        ]
        out.append(_table(["level", "code", "topic", "lang", "file", "line", "slide", "message"], rows, 90))
        out.append("\n")
    else:
        out.append("No diagnostics.\n")
    return "".join(out)
