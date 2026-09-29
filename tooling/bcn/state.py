"""Topic state from the filesystem alone: stage, staleness, blockers, next step.

Every stage is decided by files on disk and step result files in build/. Nothing
is remembered anywhere else. Stale (outputs older than inputs) and blocked (a
person is needed) are separate axes from the stage and are never collapsed into it.

Reads stat information and the small JSON step results only. Never opens media,
so it stays fast across a whole programme and never triggers a cloud download.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import coursemap, fsutil, reviewfile
from .config import Config, load, load_theme, resolve_theme_name
from .envelope import Diagnostic, Fail
from .tree import Topic, scan_topic

STAGES = {
    "en": ["planned", "drafted", "validated", "rendered", "recorded", "cued", "packaged"],
    "zh": ["not_sent", "out_for_translation", "returned", "parity_checked", "rendered", "packaged"],
}
STEPS = ["validate", "render", "cues", "subtitles", "compose", "package"]

# Which earlier steps each step checks before running (runner.require_step).
UPSTREAM = {
    ("render", "en"): [("validate", "en")],
    ("cues", "en"): [("validate", "en")],
    ("subtitles", "en"): [("cues", "en")],
    ("compose", "en"): [("render", "en"), ("cues", "en")],
    ("package", "en"): [("validate", "en"), ("render", "en"), ("cues", "en"), ("subtitles", "en")],
    ("render", "zh"): [("validate", "zh")],
    ("subtitles", "zh"): [("validate", "zh")],
    ("compose", "zh"): [("render", "zh")],
    ("package", "zh"): [("validate", "zh"), ("render", "zh"), ("subtitles", "zh"), ("package", "en")],
}


def _iso(t: float | None) -> str | None:
    if t is None:
        return None
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class FileInfo:
    path: Path
    exists: bool
    mtime: float | None = None
    size: int | None = None
    hydration: str = "unknown"

    @classmethod
    def of(cls, p: Path) -> "FileInfo":
        try:
            st = p.stat()
        except OSError:
            return cls(p, False)
        return cls(p, True, st.st_mtime, st.st_size, fsutil.hydration(p, st))


@dataclass
class Step:
    name: str
    lang: str
    env: dict[str, Any] | None
    mtime: float | None
    inputs_newest: float | None

    @property
    def exists(self) -> bool:
        return self.env is not None

    @property
    def ok(self) -> bool:
        return bool(self.env and self.env.get("ok"))

    @property
    def fresh(self) -> bool:
        if self.mtime is None:
            return False
        return self.inputs_newest is None or self.mtime >= self.inputs_newest

    @property
    def result(self) -> dict[str, Any]:
        return ((self.env or {}).get("results") or [{}])[0]

    def errors(self) -> list[dict[str, Any]]:
        return [d for d in (self.env or {}).get("diagnostics", []) if d.get("level") == "error"]


@dataclass
class ModuleContext:
    root: Path
    module: str
    cfg: Config
    course_map: coursemap.CourseMap
    assets: list[coursemap.AssetRequest]
    theme_newest: float | None


_modules: dict[tuple[Path, str], ModuleContext] = {}


def module_context(root: Path, module: str) -> ModuleContext:
    key = (root, module)
    if key not in _modules:
        cfg = load(root, root / module)
        cm = coursemap.load_course_map(root / module, cfg["documents"]["course_map_headings"])
        try:
            theme = load_theme(root, resolve_theme_name(cfg, None))
            theme_newest = fsutil.newest(theme.files())
        except Fail:
            theme_newest = None
        _modules[key] = ModuleContext(root, module, cfg, cm, coursemap.load_assets(root / module), theme_newest)
    return _modules[key]


@dataclass
class LangState:
    lang: str
    stage: str
    stage_index: int
    stale: bool = False
    stale_steps: list[str] = field(default_factory=list)
    blocked: bool = False
    blockers: list[dict[str, Any]] = field(default_factory=list)
    next: str | None = None
    complete: bool = False
    counts: dict[str, int] = field(default_factory=lambda: {"error": 0, "warn": 0, "info": 0})
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "stage": self.stage, "stage_index": self.stage_index, "stages": STAGES[self.lang],
            "stale": self.stale, "stale_steps": self.stale_steps, "blocked": self.blocked,
            "blockers": self.blockers, "next": self.next, "complete": self.complete,
            "diagnostics": self.counts, "steps": self.steps,
        }


class TopicState:
    def __init__(self, t: Topic, ctx: ModuleContext) -> None:
        self.t = t
        self.ctx = ctx
        self.files: dict[str, FileInfo] = {}
        self.steps: dict[tuple[str, str], Step] = {}
        self.scan: list[Diagnostic] = []
        self.pngs: dict[str, list[Path]] = {}

    # -- file facts -----------------------------------------------------------------
    def f(self, key: str, p: Path) -> FileInfo:
        if key not in self.files:
            self.files[key] = FileInfo.of(p)
        return self.files[key]

    def step(self, name: str, lang: str, inputs: list[FileInfo]) -> Step:
        k = (name, lang)
        if k not in self.steps:
            p = self.t.step_file(name, lang)
            fi = self.f(f"build/{p.name}", p)
            env = fsutil.read_json(p) if fi.exists else None
            newest = max((i.mtime for i in inputs if i.exists and i.mtime), default=None)
            step = Step(name, lang, env, fi.mtime, newest)
            # A step that refused to run because an earlier step had not passed says nothing
            # about the topic once that earlier step has run again: treat it as out of date.
            if env and not env.get("ok"):
                codes = {d.get("code") for d in step.errors()}
                if codes == {"STEP_PREREQUISITE"}:
                    for up_name, up_lang in UPSTREAM.get(k, []):
                        up = self.t.step_file(up_name, up_lang)
                        up_m = self.f(f"build/{up.name}", up).mtime
                        if up_m and fi.mtime and up_m > fi.mtime:
                            step.inputs_newest = float("inf")
                            break
            self.steps[k] = step
        return self.steps[k]

    def compute(self) -> dict[str, Any]:
        t = self.t
        mt = self.ctx.course_map.find(t.unit, t.code)
        has_dir = t.dir.is_dir()
        self.scan = scan_topic(t) if has_dir else []
        src = self.f("topic.md", t.src("en"))
        zsrc = self.f("topic.zh.md", t.src("zh"))
        video = self.f("edit/master.mp4", t.video)
        srt = self.f("edit/master.srt", t.srt("en"))
        zsrt = self.f("edit/master.zh.srt", t.srt("zh"))
        prog = self.f("programme.toml", t.root / "programme.toml")
        review = self.f("review.json", t.review_file)
        xl = self.f("translation.json", t.translation_file)
        for lang in ("en", "zh"):
            self.pngs[lang] = t.slide_pngs(lang)

        en = self._english(src, video, srt, prog, review, mt is not None)
        zh = self._mandarin(src, zsrc, srt, zsrt, prog, xl, en)
        unreviewed = self._unreviewed()
        return {
            "topic": t.id,
            "ok": True,
            "skipped": False,
            "module": t.module, "unit": t.unit, "code": t.code, "path": t.rel,
            "title": mt.title if mt else None,
            "minutes": mt.minutes if mt else None,
            "outcomes": mt.outcomes if mt else [],
            "in_course_map": mt is not None,
            "has_dir": has_dir,
            "hydration": self._hydration(),
            "unreviewed_mistranscriptions": unreviewed,
            "en": en.to_json(),
            "zh": zh.to_json(),
            "artifacts": self._artifacts(),
        }

    # -- English --------------------------------------------------------------------
    def _english(self, src: FileInfo, video: FileInfo, srt: FileInfo, prog: FileInfo, review: FileInfo,
                 in_map: bool) -> LangState:
        t = self.t
        st = LangState("en", "planned", 0)
        if not src.exists:
            st.next = "intake" if in_map else None
            if not in_map and self.t.dir.is_dir():
                st.blockers.append({"code": "DOC_TOPIC_NOT_IN_MAP", "message": "The topic directory is not in the course map."})
            self._asset_blockers(st)
            st.blocked = bool(st.blockers)
            return st
        pngs = [self.f(f"build/slides/en/{p.name}", p) for p in self.pngs["en"]]
        pdf = self.f("build/deck.en.pdf", t.deck_pdf("en"))
        cues_csv = self.f(f"build/{t.cues_csv.name}", t.cues_csv)
        subs = self.f(f"build/subtitles/{t.id}.en.srt", t.subtitle_out("en", "srt"))
        manifest = self.f("out/manifest.json", t.manifest("en"))
        theme_fi = FileInfo(Path("theme"), self.ctx.theme_newest is not None, self.ctx.theme_newest)

        validate = self.step("validate", "en", [src])
        render = self.step("render", "en", [src, theme_fi])
        cues = self.step("cues", "en", [src, srt, video, prog])
        subtitles = self.step("subtitles", "en", [srt, prog, src])
        compose = self.step("compose", "en", [video, cues_csv, subs, *pngs])
        package = self.step("package", "en", [src, video, srt, cues_csv, *pngs, subs, prog])

        # Hand-set boundaries and corrections are compared by value, not by review.json's mtime.
        rv = reviewfile.load(t) if review.exists else {"cue_overrides": {}, "transcripts": {}}
        cur_ov = {str(k): v for k, v in sorted(_overrides(rv).items())}
        if cues.exists and cues.result.get("overrides", {}) != cur_ov:
            cues.inputs_newest = float("inf")
        cur_corr = sorted(k for k, v in rv["transcripts"].items() if v.get("decision") == "correct")
        if subtitles.exists and sorted(subtitles.result.get("corrections", [])) != cur_corr:
            subtitles.inputs_newest = float("inf")

        manifest_ok = manifest.exists and self._manifest_intact("manifest.json")
        exist = {
            "drafted": True,
            "validated": validate.ok,
            "rendered": render.ok and bool(pngs) and pdf.exists,
            "recorded": video.exists and srt.exists,
            "cued": cues_csv.exists,
            "packaged": manifest_ok,
        }
        fresh = {
            "validated": validate.fresh,
            "rendered": render.fresh and all((p.mtime or 0) >= (src.mtime or 0) for p in pngs),
            "recorded": True,
            "cued": cues.fresh and (cues_csv.mtime or 0) >= max(src.mtime or 0, srt.mtime or 0, video.mtime or 0),
            "packaged": package.fresh,
        }
        self._walk(st, exist, fresh)

        # Blockers: errors from step results that are still current, plus the tree itself.
        for s in (validate, render, cues, subtitles, compose, package):
            self._count(st, s)
        self._tree_blockers(st, [src, video, srt])
        self._asset_blockers(st)
        st.blocked = bool(st.blockers)

        # Next: the first step whose result is missing, failed or stale.
        order: list[tuple[str, Step | None]] = [("validate", validate), ("render", render), ("await_recording", None),
                                                ("cues", cues), ("subtitles", subtitles), ("package", package)]
        st.next = None
        for name, s in order:
            if s is None:
                if not exist["recorded"]:
                    st.next = name
                    break
                continue
            if not (s.ok and s.fresh):
                st.next = name
                break
        st.complete = st.stage == "packaged" and not st.stale and not st.blocked and st.next is None
        st.steps = self._steps_json("en")
        return st

    # -- Mandarin -------------------------------------------------------------------
    def _mandarin(self, src: FileInfo, zsrc: FileInfo, srt: FileInfo, zsrt: FileInfo, prog: FileInfo,
                  xl: FileInfo, en: LangState) -> LangState:
        t = self.t
        st = LangState("zh", "not_sent", 0)
        record = fsutil.read_json(t.translation_file) if xl.exists else None
        exported = bool(record and record.get("exports"))
        returned = zsrc.exists and zsrt.exists
        pngs = [self.f(f"build/slides/zh/{p.name}", p) for p in self.pngs["zh"]]
        zsubs = self.f(f"build/subtitles/{t.id}.zh.srt", t.subtitle_out("zh", "srt"))
        manifest = self.f("out/manifest.zh.json", t.manifest("zh"))
        en_manifest = self.f("out/manifest.json", t.manifest("en"))
        theme_fi = FileInfo(Path("theme"), self.ctx.theme_newest is not None, self.ctx.theme_newest)

        validate = self.step("validate", "zh", [zsrc, src])
        subtitles = self.step("subtitles", "zh", [zsrt, srt, prog])
        render = self.step("render", "zh", [zsrc, theme_fi])
        compose = self.step("compose", "zh", [zsubs, *pngs])
        package = self.step("package", "zh", [zsrc, zsrt, *pngs, zsubs, en_manifest])

        exist = {
            "out_for_translation": exported or returned,
            "returned": returned,
            "parity_checked": validate.ok and subtitles.ok,
            "rendered": render.ok and bool(pngs),
            "packaged": manifest.exists and self._manifest_intact("manifest.zh.json"),
        }
        fresh = {
            "out_for_translation": True,
            # A translation of an older script is itself stale.
            "returned": (zsrc.mtime or 0) >= (src.mtime or 0) or not returned,
            "parity_checked": validate.fresh and subtitles.fresh,
            "rendered": render.fresh,
            "packaged": package.fresh,
        }
        self._walk(st, exist, fresh)
        for s in (validate, subtitles, render, compose, package):
            self._count(st, s)
        self._tree_blockers(st, [zsrc, zsrt] if returned else [])
        st.blocked = bool(st.blockers)

        if not returned:
            if exported:
                st.next = "await_translation"
            elif en.stage_index >= STAGES["en"].index("recorded") and en.steps.get("validate", {}).get("ok"):
                st.next = "translation_export"
            else:
                st.next = None  # not ready to send
        else:
            st.next = None
            if not fresh["returned"]:
                st.next = "translation_export"
            else:
                for name, s in (("validate", validate), ("subtitles", subtitles), ("render", render), ("package", package)):
                    if not (s.ok and s.fresh):
                        st.next = name
                        break
        st.complete = st.stage == "packaged" and not st.stale and not st.blocked and st.next is None
        st.steps = self._steps_json("zh")
        return st

    # -- shared -----------------------------------------------------------------------
    def _walk(self, st: LangState, exist: dict[str, bool], fresh: dict[str, bool]) -> None:
        """Stage is the furthest stage reached with every earlier stage also reached.

        English starts at drafted (this is only called once topic.md exists);
        Mandarin starts at not_sent. Staleness is recorded for every stage reached.
        """
        stages = STAGES[st.lang]
        idx = 1 if st.lang == "en" else 0
        for i in range(idx + 1, len(stages)):
            name = stages[i]
            if not exist.get(name, False):
                break
            idx = i
            if not fresh.get(name, True):
                st.stale = True
                st.stale_steps.append(name)
        st.stage = stages[idx]
        st.stage_index = idx

    # A step that refused because an input or an earlier step is not there yet is waiting,
    # not blocked: the next step already says what is missing. Only real problems block.
    NOT_READY = {"STEP_PREREQUISITE", "FS_MISSING"}

    def _count(self, st: LangState, s: Step) -> None:
        if not s.exists or not s.fresh:
            return
        for d in (s.env or {}).get("diagnostics", []):
            lvl = d.get("level", "info")
            if lvl == "error" and d.get("code") in self.NOT_READY:
                continue
            st.counts[lvl] = st.counts.get(lvl, 0) + 1
            if lvl == "error":
                st.blockers.append({"code": d.get("code"), "message": d.get("message"), "step": s.name, "slide": d.get("slide")})

    def _tree_blockers(self, st: LangState, inputs: list[FileInfo]) -> None:
        for d in self.scan:
            if d.level == "error":
                st.blockers.append({"code": d.code, "message": d.message, "file": d.file})
                st.counts["error"] += 1
        for fi in inputs:
            if fi.exists and fi.hydration == "cloud":
                rel = str(fi.path.relative_to(self.t.dir))
                st.blockers.append({"code": "FS_NOT_HYDRATED", "message": f"{rel} is cloud-only.", "file": rel})

    def _asset_blockers(self, st: LangState) -> None:
        for a in self.ctx.assets:
            if a.outstanding and self.t.id in a.topics:
                st.blockers.append({"code": "QA_ASSET_OUTSTANDING", "message": f"Asset request '{a.asset}' is {a.status}.",
                                    "file": f"{self.t.module}/assets.md"})

    def _steps_json(self, lang: str) -> dict[str, dict[str, Any]]:
        out = {}
        for (name, l), s in self.steps.items():
            if l != lang:
                continue
            out[name] = {"exists": s.exists, "ok": s.ok if s.exists else None, "fresh": s.fresh if s.exists else None,
                         "time": _iso(s.mtime), "skipped": s.result.get("skipped") if s.exists else None}
        return out

    def _manifest_intact(self, name: str) -> bool:
        """Shallow: every listed file exists at its recorded size. --verify checks hashes."""
        m = fsutil.read_json(self.t.out / name)
        if not m:
            return False
        for f in m.get("files", []):
            fi = self.f(f"out/{f['name']}", self.t.out / f["name"])
            if not fi.exists or fi.size != f.get("bytes"):
                return False
        return True

    def _unreviewed(self) -> int:
        cues = self.steps.get(("cues", "en"))
        if not cues or not cues.exists or not cues.fresh:
            return 0
        report = fsutil.read_json(self.t.cues_report) or {}
        decided = reviewfile.load(self.t)["transcripts"] if self.t.review_file.is_file() else {}
        return sum(1 for d in report.get("divergences", []) if d.get("kind") == "mistranscription" and d.get("id") not in decided)

    def _hydration(self) -> str:
        states = {fi.hydration for fi in self.files.values() if fi.exists}
        if "cloud" in states:
            return "cloud" if states == {"cloud"} else "partial"
        return "local" if states else "unknown"

    def _artifacts(self) -> list[dict[str, Any]]:
        t = self.t
        rows = []

        def add(key: str, p: Path, lang: str | None, stale: bool | None = None, kind: str = "file") -> None:
            fi = self.f(key, p)
            rows.append({"key": key, "path": str(p.relative_to(t.root)), "lang": lang, "kind": kind, "exists": fi.exists,
                         "bytes": fi.size, "mtime": _iso(fi.mtime), "hydration": fi.hydration if fi.exists else None,
                         "stale": stale if fi.exists else None})

        def bumpers(lang: str) -> None:
            src = fsutil.mtime(t.src(lang)) or 0
            for kind, p in (("bumper_card", t.bumper_card("intro", lang)), ("bumper_card", t.bumper_card("outro", lang)),
                            ("bumper", t.bumper("intro", lang)), ("bumper", t.bumper("outro", lang))):
                add(str(p.relative_to(t.dir)), p, lang, (fsutil.mtime(p) or 0) < src if p.is_file() else None, kind=kind)

        def stale_of(step: str, lang: str) -> bool | None:
            s = self.steps.get((step, lang))
            return (not s.fresh) if s and s.exists else None

        add("topic.md", t.src("en"), "en", kind="source")
        add("edit/master.mp4", t.video, "en", kind="source")
        add("edit/master.srt", t.srt("en"), "en", kind="source")
        add("review.json", t.review_file, None, kind="source")
        for step in ("validate", "render", "cues", "subtitles", "compose", "package"):
            add(f"build/{step}.json", t.step_file(step, "en"), "en", stale_of(step, "en"), kind="result")
        add("build/deck.en.pdf", t.deck_pdf("en"), "en", stale_of("render", "en"), kind="deck")
        script_stale = None
        if (t.build / "script.en.pdf").is_file():
            script_stale = (fsutil.mtime(t.build / "script.en.pdf") or 0) < (fsutil.mtime(t.src("en")) or 0)
        for ext in ("pdf", "html", "txt"):
            add(f"build/script.en.{ext}", t.build / f"script.en.{ext}", "en", script_stale, kind="script")
        bumpers("en")
        rows.append({"key": "build/slides/en", "path": str(t.slides_dir("en").relative_to(t.root)), "lang": "en", "kind": "slides",
                     "exists": bool(self.pngs["en"]), "count": len(self.pngs["en"]), "stale": stale_of("render", "en") if self.pngs["en"] else None,
                     "mtime": _iso(max((fsutil.mtime(p) or 0 for p in self.pngs["en"]), default=None)) if self.pngs["en"] else None,
                     "hydration": None, "bytes": None})
        add(f"build/{t.cues_csv.name}", t.cues_csv, "en", stale_of("cues", "en"), kind="cues")
        add("build/cues-report.json", t.cues_report, "en", stale_of("cues", "en"), kind="report")
        add(f"build/subtitles/{t.id}.en.srt", t.subtitle_out("en", "srt"), "en", stale_of("subtitles", "en"), kind="subtitles")
        add("build/draft.mp4", t.draft("en"), "en", stale_of("compose", "en"), kind="draft")
        add("out/manifest.json", t.manifest("en"), "en", stale_of("package", "en"), kind="package")
        add("translation.json", t.translation_file, "zh", kind="source")
        add("topic.zh.md", t.src("zh"), "zh", kind="source")
        add("edit/master.zh.srt", t.srt("zh"), "zh", kind="source")
        for step in ("validate", "subtitles", "render", "compose", "package"):
            add(f"build/{step}.zh.json", t.step_file(step, "zh"), "zh", stale_of(step, "zh"), kind="result")
        rows.append({"key": "build/slides/zh", "path": str(t.slides_dir("zh").relative_to(t.root)), "lang": "zh", "kind": "slides",
                     "exists": bool(self.pngs["zh"]), "count": len(self.pngs["zh"]), "stale": stale_of("render", "zh") if self.pngs["zh"] else None,
                     "mtime": _iso(max((fsutil.mtime(p) or 0 for p in self.pngs["zh"]), default=None)) if self.pngs["zh"] else None,
                     "hydration": None, "bytes": None})
        add("build/draft.zh.mp4", t.draft("zh"), "zh", stale_of("compose", "zh"), kind="draft")
        bumpers("zh")
        add("out/manifest.zh.json", t.manifest("zh"), "zh", stale_of("package", "zh"), kind="package")
        return rows


def _overrides(rv: dict[str, Any]) -> dict[int, float]:
    out = {}
    for k, v in rv.get("cue_overrides", {}).items():
        try:
            out[int(k)] = reviewfile.parse_tc(v["timecode"])
        except (Fail, KeyError, ValueError, TypeError):
            continue
    return out


def all_topics(root: Path, modules: list[str], topics: list[Topic], level: str, rel: str) -> list[Topic]:
    """Topics on disk plus topics listed in the course map that have no directory yet (Planned)."""
    seen = {t.id for t in topics}
    out = list(topics)
    parts = rel.split("/") if rel != "." else []
    for m in modules:
        ctx = module_context(root, m)
        for mt in ctx.course_map.topics:
            if len(parts) >= 2 and mt.unit != parts[1]:
                continue
            if len(parts) >= 3 and mt.code != parts[2]:
                continue
            t = Topic(root, m, mt.unit, mt.code)
            if t.id not in seen:
                out.append(t)
                seen.add(t.id)
    return sorted(out, key=lambda t: t.id)
