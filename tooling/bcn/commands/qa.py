"""bcn qa: validate plus the cross-artefact checks that only make sense once everything exists.

Writes <module>/build/qa.json (envelope), <module>/build/qa-report.json and a
human-readable <module>/build/qa-report.txt, plus build/qa.json per topic.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from .. import coursemap, fsutil, reviewfile, srt, tools
from ..config import Config, load
from ..envelope import Diagnostic, Envelope, Fail, TopicResult, format_human
from ..markdown import IMAGE_RE
from ..media import probe
from ..progress import TopicProgress
from ..runner import run_topics
from ..tree import Target, Topic
from . import validate as validate_cmd

HELP = "run every check, emit a report"


def add_args(p: argparse.ArgumentParser) -> None:
    pass


class Durations:
    """Video durations, from a verified manifest where possible, else ffprobe."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.tl: tools.Tools | None = None

    def of(self, t: Topic) -> float | None:
        m = fsutil.read_json(t.manifest("en"))
        if m and m.get("duration") and fsutil.is_fresh([t.manifest("en")], [t.video]):
            return float(m["duration"])
        if not t.video.is_file() or fsutil.hydration(t.video) == "cloud":
            return None
        if self.tl is None:
            self.tl = tools.require(self.cfg, "ffprobe")
        return probe(self.tl, t.video, "edit/master.mp4").duration


def topic_checks(t: Topic, r: TopicResult, cfg: Config, durations: Durations, assets: list[coursemap.AssetRequest],
                 minutes: int | None) -> float | None:
    def d(code: str, msg: str, **kw) -> None:
        r.diagnostics.append(Diagnostic(code, msg, topic=t.id, **kw))

    src = t.src("en")
    # Outstanding asset requests block every topic that references them.
    refs: set[str] = set()
    if src.is_file():
        refs = {Path(m.group(2)).name for m in IMAGE_RE.finditer(src.read_text(encoding="utf-8-sig"))}
    for a in assets:
        if a.outstanding and (t.id in a.topics or a.asset in refs):
            d("QA_ASSET_OUTSTANDING", f"Asset request '{a.asset}' is {a.status}.", file=f"{t.module}/assets.md", line=a.line)

    duration = None
    if t.video.is_file():
        needs = {
            "slides": t.slide_pngs("en"),
            "subtitles": [t.subtitle_out("en", "srt")],
            "cue sheet": [t.cues_csv],
        }
        for what, paths in needs.items():
            if not paths or not all(p.is_file() for p in paths):
                d("QA_ARTEFACT_MISSING", f"The topic has a video but no {what}.",
                  hint={"slides": "Run bcn render.", "subtitles": "Run bcn subtitles.", "cue sheet": "Run bcn cues."}[what])
            elif not fsutil.is_fresh(paths, [src]):
                d("QA_ARTEFACT_STALE", f"The {what} are older than topic.md." if what != "cue sheet" else "The cue sheet is older than topic.md.",
                  file="topic.md")
        try:
            duration = durations.of(t)
        except Fail as f:
            r.diagnostics.append(f.diagnostic)
        if duration and minutes:
            tol = cfg["qa"]["video_minutes_tolerance"]
            if abs(duration / 60 - minutes) > minutes * tol:
                d("QA_VIDEO_DURATION", f"The video is {duration / 60:.1f} minutes; the topic declares {minutes} (± {tol:.0%}).",
                  file="edit/master.mp4")
        if duration and t.srt("en").is_file():
            try:
                cues = srt.parse_file(t.srt("en"), "edit/master.srt")
                if cues and cues[-1].end > duration + 0.5:
                    d("CUE_SRT_PAST_END", f"The final cue ends at {srt.fmt_cue_time(cues[-1].end)}, after the video ({srt.fmt_cue_time(duration)}).",
                      file="edit/master.srt", line=cues[-1].line)
            except Fail as f:
                r.diagnostics.append(f.diagnostic)
        report = fsutil.read_json(t.cues_report) or {}
        decided = reviewfile.load(t)["transcripts"] if t.review_file.is_file() else {}
        open_items = [x for x in report.get("divergences", []) if x.get("kind") == "mistranscription" and x.get("id") not in decided]
        if open_items:
            d("QA_UNREVIEWED_MISTRANSCRIPTION", f"{len(open_items)} suspected mis-transcriptions are unreviewed, e.g. \"{open_items[0]['srt']}\" for \"{open_items[0]['script']}\".",
              file="build/cues-report.json", hint="Review them in the Diagnostics view or with bcn review.")

    # Mandarin variant: parity, identical timings, no overflow, no narration.
    if t.src("zh").is_file():
        vz = fsutil.read_json(t.step_file("validate", "zh"))
        if not vz or not fsutil.is_fresh([t.step_file("validate", "zh")], [t.src("zh"), src]):
            from ..rules import validate_topic
            _, diags = validate_topic(cfg, t, "zh")
            r.diagnostics.extend(x for x in diags if x.code in ("MD_PARITY_COUNT", "MD_PARITY_BREAK", "MD_SAY_IN_ZH") and x.level == "error")
        else:
            r.diagnostics.extend(Diagnostic(x["code"], x["message"], topic=t.id, lang="zh", file=x.get("file"), line=x.get("line"),
                                            slide=x.get("slide"))
                                 for x in vz.get("diagnostics", []) if x["code"] in ("MD_PARITY_COUNT", "MD_PARITY_BREAK", "MD_SAY_IN_ZH") and x["level"] == "error")
        if t.srt("zh").is_file() and t.srt("en").is_file():
            try:
                a, b = srt.parse_file(t.srt("zh"), "edit/master.zh.srt"), srt.parse_file(t.srt("en"), "edit/master.srt")
                if len(a) != len(b):
                    d("SRT_ZH_CUE_COUNT", f"The Mandarin SRT has {len(a)} cues; the English has {len(b)}.", lang="zh", file="edit/master.zh.srt")
                elif any(abs(x.start - y.start) > 0.0015 or abs(x.end - y.end) > 0.0015 for x, y in zip(a, b)):
                    d("SRT_ZH_TIMING", "The Mandarin SRT timings differ from the English.", lang="zh", file="edit/master.zh.srt")
            except Fail as f:
                r.diagnostics.append(f.diagnostic)
        rz = fsutil.read_json(t.step_file("render", "zh")) or {}
        flagged = sorted({x.get("slide") for x in rz.get("diagnostics", []) if x.get("code") in ("RENDER_OVERFLOW", "RENDER_SAFE_AREA")})
        if flagged:
            d("QA_ZH_OVERFLOW", f"The Mandarin render overflows on slide {', '.join(map(str, flagged))}.", lang="zh", file="topic.zh.md")
    return duration


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    # 1. validate, exactly as bcn validate, recorded as its step result.
    v_env = Envelope("validate", target.rel, target.root)

    def v_fn(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config) -> None:
        validate_cmd.check_topic(t, r, tp, cfg, "en")

    run_topics(v_env, target, "validate", "en", v_fn, jobs=args.jobs)
    env.diagnostics.extend(validate_cmd.module_documents(target))

    by_module: dict[str, list[Topic]] = defaultdict(list)
    for t in target.topics:
        by_module[t.module].append(t)
    v_results = {r.topic: r for r in v_env.results}

    for m in target.modules:
        mdir = target.root / m
        cfg = load(target.root, mdir)
        cm = coursemap.load_course_map(mdir, cfg["documents"]["course_map_headings"])
        assets = coursemap.load_assets(mdir)
        durations = Durations(cfg)
        whole = target.level in ("root", "module")
        mod_diags: list[Diagnostic] = []
        on_disk = {t.id for t in by_module[m]}
        if whole:
            for mt in cm.topics:
                tid = f"{m}-{mt.unit}-{mt.code}"
                if tid not in on_disk:
                    mod_diags.append(Diagnostic("QA_TOPIC_NO_DIR", f"{tid} is in the course map but has no directory.",
                                                topic=tid, file=f"{m}/course-map.md", line=mt.line))
            units = sorted(set(cm.units) | {t.unit for t in by_module[m]})
            for u in units:
                if not (mdir / u / "activity.md").is_file():
                    mod_diags.append(Diagnostic("QA_ACTIVITY_MISSING", f"{m}/{u} has no activity.md.", file=f"{m}/{u}"))
            covered = {lo for mt in cm.topics for lo in mt.outcomes}
            for lo in cm.outcomes:
                if lo not in covered:
                    mod_diags.append(Diagnostic("QA_OUTCOME_UNCOVERED", f"{lo} is not covered by any topic.", file=f"{m}/course-map.md"))

        unit_minutes: dict[str, float] = defaultdict(float)
        for t in by_module[m]:
            r = TopicResult(t.id, t.rel)
            vr = v_results.get(t.id)
            if vr:
                r.diagnostics.extend(vr.diagnostics)
            mt = cm.find(t.unit, t.code)
            if mt is None:
                r.diagnostics.append(Diagnostic("QA_DIR_NOT_IN_MAP", f"{t.rel} is not in the course map.", topic=t.id, file=f"{m}/course-map.md"))
            try:
                dur = topic_checks(t, r, cfg, durations, assets, mt.minutes if mt else None)
            except Fail as f:
                r.diagnostics.append(f.diagnostic)
                dur = None
            if dur:
                unit_minutes[t.unit] += dur / 60
                r.extra["video_minutes"] = round(dur / 60, 2)
            r.ok = not any(x.level == "error" for x in r.diagnostics)
            env.results.append(r)
            fsutil.write_json(t.step_file("qa", "en"), env.topic_envelope(r))
        if whole:
            q = cfg["qa"]
            for u in sorted(set(cm.units) | set(unit_minutes)):
                total = unit_minutes.get(u, 0.0)
                if not q["unit_minutes_min"] <= total <= q["unit_minutes_max"]:
                    mod_diags.append(Diagnostic("QA_UNIT_DURATION", f"{m}/{u} has {total:.1f} minutes of video; expected {q['unit_minutes_min']}–{q['unit_minutes_max']}.",
                                                file=f"{m}/{u}"))
        env.diagnostics.extend(mod_diags)
        env.extra.setdefault("unit_minutes", {}).update({f"{m}/{u}": round(v, 2) for u, v in unit_minutes.items()})

        if whole:
            data = env.to_json()
            mine = [r for r in data["results"] if r["topic"].startswith(m + "-")]
            report = {**data, "target": m, "results": mine,
                      "diagnostics": [x for x in data["diagnostics"] if (x.get("topic") or "").startswith(m + "-") or (x.get("file") or "").startswith(m)]}
            report["ok"] = all(r["ok"] for r in mine) and not any(x["level"] == "error" for x in report["diagnostics"])
            fsutil.write_json(mdir / "build" / "qa-report.json", report)
            fsutil.write_json(mdir / "build" / "qa.json", report)
            fsutil.write_text(mdir / "build" / "qa-report.txt", format_human(report))
            for name in ("qa-report.json", "qa-report.txt"):
                env.artifacts.append(env.artifact(mdir / "build" / name, "report"))
