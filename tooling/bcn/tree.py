"""The working tree: where the root is, what a path targets, where each artefact lives.

Enumeration is by allowlist. Within a topic directory the tooling knows exactly
which names it expects and ignores everything else. Names are compared against
the directory listing exactly, because APFS is case-insensitive and will happily
open topic.md when asked for Topic.md.
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .envelope import Diagnostic, Fail

MODULE_RE = re.compile(r"^[A-Z]{2}\d{4}$")
UNIT_RE = re.compile(r"^U\d{2}$")
TOPIC_RE = re.compile(r"^T\d{2}$")
TOPIC_ID_RE = re.compile(r"^[A-Z]{2}\d{4}-U\d{2}-T\d{2}$")

LANGS = ("en", "zh")

# macOS and sync-client noise. Never reported, never parsed.
NOISE = [
    ".DS_Store", "._*", "Icon\r", ".localized", ".Spotlight-V100", ".fseventsd",
    ".TemporaryItems", ".Trashes", "__MACOSX", "~$*", "*~", "*.swp", ".~lock.*#",
    ".*.partial*", ".*.tmp", ".out.old-*",
]

TOPIC_FILES = {"topic.md", "topic.zh.md", "review.json", "translation.json"}
TOPIC_DIRS = {"assets", "edit", "build", "out", ".history"}
EDIT_FILES = {"master.mp4", "master.srt", "master.zh.srt"}
_CONFLICT_SEP = r"[-_ (]"


def is_noise(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, pat) for pat in NOISE)


def conflict_of(name: str, expected: set[str]) -> str | None:
    """The expected file this name is a sync conflict copy of, if any.

    OneDrive appends a machine name ("topic-Toms-MacBook-Pro.md"), Finder and
    Dropbox append a counter or "(conflicted copy)".
    """
    for exp in expected:
        stem, dot, ext = exp.partition(".")
        pattern = rf"^{re.escape(stem)}{_CONFLICT_SEP}.+\.{re.escape(ext)}$" if dot else rf"^{re.escape(stem)}{_CONFLICT_SEP}.+$"
        if re.match(pattern, name, re.IGNORECASE):
            return exp
    return None


def find_root(start: Path) -> Path:
    p = start.resolve()
    if p.is_file():
        p = p.parent
    for candidate in (p, *p.parents):
        if (candidate / "programme.toml").is_file():
            return candidate
    raise Fail("ROOT_NOT_FOUND", f"No programme.toml at or above {start}.",
               hint="Run bcn against a path inside a programme root.")


@dataclass(frozen=True)
class Topic:
    root: Path
    module: str
    unit: str
    code: str

    @property
    def id(self) -> str:
        return f"{self.module}-{self.unit}-{self.code}"

    @property
    def dir(self) -> Path:
        return self.root / self.module / self.unit / self.code

    @property
    def rel(self) -> str:
        return f"{self.module}/{self.unit}/{self.code}"

    @property
    def module_dir(self) -> Path:
        return self.root / self.module

    @property
    def build(self) -> Path:
        return self.dir / "build"

    @property
    def out(self) -> Path:
        return self.dir / "out"

    def src(self, lang: str = "en") -> Path:
        return self.dir / ("topic.md" if lang == "en" else f"topic.{lang}.md")

    @property
    def assets(self) -> Path:
        return self.dir / "assets"

    @property
    def video(self) -> Path:
        return self.dir / "edit" / "master.mp4"

    def srt(self, lang: str = "en") -> Path:
        return self.dir / "edit" / ("master.srt" if lang == "en" else f"master.{lang}.srt")

    def step_file(self, step: str, lang: str = "en") -> Path:
        return self.build / (f"{step}.json" if lang == "en" else f"{step}.{lang}.json")

    def slides_dir(self, lang: str = "en") -> Path:
        return self.build / "slides" / lang

    def slide_name(self, n: int, lang: str = "en") -> str:
        return f"{self.id}-s{n:02d}.png" if lang == "en" else f"{self.id}-{lang}-s{n:02d}.png"

    def slide_pngs(self, lang: str = "en") -> list[Path]:
        d = self.slides_dir(lang)
        if not d.is_dir():
            return []
        pat = re.compile(rf"^{re.escape(self.id)}{'' if lang == 'en' else '-' + lang}-s\d{{2}}\.png$")
        return sorted(p for p in d.iterdir() if pat.match(p.name))

    def deck_pdf(self, lang: str = "en") -> Path:
        return self.build / f"deck.{lang}.pdf"

    def render_html(self, lang: str = "en") -> Path:
        return self.build / f"deck.{lang}.html"

    @property
    def cues_csv(self) -> Path:
        return self.build / f"{self.id}.cues.csv"

    @property
    def cues_report(self) -> Path:
        return self.build / "cues-report.json"

    def subtitle_out(self, lang: str, ext: str) -> Path:
        return self.build / "subtitles" / f"{self.id}.{lang}.{ext}"

    def draft(self, lang: str = "en") -> Path:
        return self.build / ("draft.mp4" if lang == "en" else f"draft.{lang}.mp4")

    def bumper_card(self, kind: str, lang: str = "en") -> Path:
        """The still behind a bumper: 'intro' is the topic title, 'outro' the logo."""
        return self.build / "bumpers" / f"{self.id}.{kind}-card.{lang}.png"

    def bumper(self, kind: str, lang: str = "en") -> Path:
        """kind is "intro" or "outro"."""
        return self.build / "bumpers" / f"{self.id}.{kind}.{lang}.mp4"

    @property
    def review_file(self) -> Path:
        return self.dir / "review.json"

    @property
    def translation_file(self) -> Path:
        return self.dir / "translation.json"

    def manifest(self, lang: str = "en") -> Path:
        return self.out / ("manifest.json" if lang == "en" else f"manifest.{lang}.json")


def topic_from_id(root: Path, topic_id: str) -> Topic:
    if not TOPIC_ID_RE.match(topic_id):
        raise Fail("MD_TOPIC_ID_FORMAT", f"'{topic_id}' is not a topic id.")
    m, u, t = topic_id.split("-")
    return Topic(root, m, u, t)


def _listdir(d: Path) -> list[str]:
    try:
        return sorted(os.listdir(d))
    except OSError:
        return []


def _child_dirs(d: Path, rx: re.Pattern[str], diags: list[Diagnostic], kind: str) -> list[str]:
    out = []
    for name in _listdir(d):
        if is_noise(name) or not (d / name).is_dir():
            continue
        if rx.match(name):
            out.append(name)
        elif rx.match(name.upper()):
            diags.append(Diagnostic("FS_WRONG_CASE", f"{kind} directory '{name}' should be '{name.upper()}'.",
                                    file=str(d / name), hint="Rename it. The tools compare names exactly."))
    return out


@dataclass
class Target:
    root: Path
    path: Path
    level: str  # "root", "module", "unit", "topic"
    rel: str
    modules: list[str]
    topics: list[Topic]
    diagnostics: list[Diagnostic]


def resolve(path_arg: str) -> Target:
    path = Path(path_arg).expanduser()
    if not path.exists():
        raise Fail("FS_MISSING", f"{path_arg} does not exist.")
    path = path.resolve()
    if path.is_file():
        path = path.parent
    root = find_root(path)
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        raise Fail("PATH_OUTSIDE_ROOT", f"{path_arg} is outside the programme root {root}.")
    if len(parts) > 3:
        # Inside a topic directory (e.g. edit/): target the topic.
        parts = parts[:3]
    diags: list[Diagnostic] = []
    checks = [MODULE_RE, UNIT_RE, TOPIC_RE]
    for i, part in enumerate(parts):
        if not checks[i].match(part):
            if checks[i].match(part.upper()):
                raise Fail("FS_WRONG_CASE", f"'{part}' should be '{part.upper()}'.", file="/".join(parts[: i + 1]),
                           hint="Rename the directory. The tools compare names exactly.")
            raise Fail("USAGE", f"{path_arg} is not a programme, module, unit or topic directory.")
    level = ["root", "module", "unit", "topic"][len(parts)]
    modules = [parts[0]] if parts else _child_dirs(root, MODULE_RE, diags, "Module")
    topics: list[Topic] = []
    for m in modules:
        units = [parts[1]] if len(parts) > 1 else _child_dirs(root / m, UNIT_RE, diags, "Unit")
        for u in units:
            codes_ = [parts[2]] if len(parts) > 2 else _child_dirs(root / m / u, TOPIC_RE, diags, "Topic")
            topics.extend(Topic(root, m, u, c) for c in codes_)
    rel = "/".join(parts) if parts else "."
    return Target(root, path, level, rel, modules, topics, diags)


def scan_topic(t: Topic) -> list[Diagnostic]:
    """Unexpected files (info), sync conflicts and wrong-cased names (error)."""
    diags: list[Diagnostic] = []

    def check(d: Path, files: set[str], dirs: set[str], rel: str) -> None:
        names = _listdir(d)
        exact = set(names)
        for name in names:
            if is_noise(name):
                continue
            p = d / name
            relname = f"{rel}{name}"
            if name in files or name in dirs:
                continue
            lower = {x.lower(): x for x in files | dirs}
            if name.lower() in lower and lower[name.lower()] not in exact:
                diags.append(Diagnostic("FS_WRONG_CASE", f"'{relname}' should be named '{rel}{lower[name.lower()]}'.",
                                        topic=t.id, file=relname, hint="Rename it. The tools compare names exactly."))
                continue
            conflict = conflict_of(name, files) if p.is_file() else None
            if conflict:
                diags.append(Diagnostic(
                    "FS_SYNC_CONFLICT", f"'{relname}' looks like a sync conflict copy of {rel}{conflict}.",
                    topic=t.id, file=relname,
                    hint=f"Decide which version is right, keep it as {rel}{conflict}, and delete the other."))
                continue
            diags.append(Diagnostic("FS_UNEXPECTED_FILE", f"'{relname}' is not a file the tooling uses; it is ignored.",
                                    topic=t.id, file=relname))

    check(t.dir, TOPIC_FILES, TOPIC_DIRS, "")
    if (t.dir / "edit").is_dir():
        check(t.dir / "edit", EDIT_FILES, set(), "edit/")
    return diags
