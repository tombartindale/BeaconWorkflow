"""bcn cues: match the script to the SRT and emit the slide cue sheet."""

from __future__ import annotations

import argparse
import getpass

from .. import fsutil, reviewfile, srt, tools
from ..align import align
from ..config import Config
from ..envelope import Diagnostic, Envelope, Fail, TopicResult, sha256_file, utcnow
from ..markdown import parse
from ..media import probe
from ..progress import TopicProgress
from ..runner import require_input, require_step, run_topics, try_skip
from ..tree import Target, Topic

HELP = "match script to SRT, emit the slide cue sheet"


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--dump-narration", action="store_true", help="write build/narration.txt for inspection")
    p.add_argument("--set", action="append", default=[], metavar="SLIDE=TIMECODE",
                   help="place a slide boundary by hand (recorded in review.json)")
    p.add_argument("--unset", action="append", default=[], type=int, metavar="SLIDE",
                   help="remove a hand-placed boundary")
    p.add_argument("--by", default=None, help="who is setting a timecode (default: login name)")


def pair_checks(t: Topic, cues: list[srt.Cue], duration: float, cfg: Config) -> list[Diagnostic]:
    c = cfg["cues"]
    out: list[Diagnostic] = []
    rel = "edit/master.srt"
    for a, b in zip(cues, cues[1:]):
        if b.start <= a.start:
            out.append(Diagnostic("CUE_SRT_ORDER", f"Cue {b.index} starts at {srt.fmt_cue_time(b.start)}, not after cue {a.index} at {srt.fmt_cue_time(a.start)}.",
                                  topic=t.id, file=rel, line=b.line,
                                  hint="The SRT does not match this edit, or it was hand-edited. Ask the editor to re-export the pair."))
            break
    for cue in cues:
        if cue.end < cue.start:
            out.append(Diagnostic("CUE_SRT_ORDER", f"Cue {cue.index} ends before it starts.", topic=t.id, file=rel, line=cue.line))
            break
    if cues:
        last = cues[-1]
        if last.end > duration + 0.5:
            out.append(Diagnostic("CUE_SRT_PAST_END", f"The final cue ends at {srt.fmt_cue_time(last.end)}; the video is {srt.fmt_cue_time(duration)} long.",
                                  topic=t.id, file=rel, line=last.line,
                                  hint="The SRT and the video are not a matching pair. Re-export both from the same edit."))
        elif duration - last.end > c["srt_tail_warn_s"]:
            out.append(Diagnostic("CUE_SRT_SHORT", f"The final cue ends at {srt.fmt_cue_time(last.end)}, {duration - last.end:.0f}s before the video ends.",
                                  topic=t.id, file=rel, line=last.line,
                                  hint="Check the SRT covers the whole edit."))
    m_srt, m_vid = fsutil.mtime(t.srt()), fsutil.mtime(t.video)
    tol = c["srt_video_mtime_tolerance_s"]
    if m_srt is not None and m_vid is not None and abs(m_srt - m_vid) > tol:
        newer = "video" if m_vid > m_srt else "SRT"
        out.append(Diagnostic("CUE_PAIR_MISMATCH", f"The SRT and video were modified {abs(m_srt - m_vid) / 3600:.1f} hours apart; the {newer} is newer.",
                              topic=t.id, file=rel,
                              hint="If the video was re-cut its SRT must be re-exported too. If both are right, touch them together."))
    return out


def cues_topic(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config, args: argparse.Namespace) -> None:
    src, srt_path, video = t.src("en"), t.srt("en"), t.video
    require_input(t, src)
    require_input(t, srt_path, stable=True)
    require_input(t, video, stable=True)
    require_step(t, "validate", "en", [src], "bcn validate")

    who = args.by or getpass.getuser()
    for spec in args.set:
        slide, _, tc = spec.partition("=")
        if not slide.isdigit() or not tc:
            raise Fail("USAGE", f"--set expects SLIDE=TIMECODE, got '{spec}'.")
        reviewfile.set_override(t, int(slide), tc, who)
    for slide in args.unset:
        reviewfile.unset_override(t, slide)

    outputs = [t.cues_report]
    inputs = [src, srt_path, video, t.root / "programme.toml"]
    # review.json also holds transcript decisions, which must not make cues stale;
    # compare the hand-set boundaries themselves rather than the file's mtime.
    current_ov = {str(k): v for k, v in sorted(reviewfile.overrides(t).items())}
    prev = fsutil.read_json(t.step_file("cues"))
    prev_ov = ((prev or {}).get("results") or [{}])[0].get("overrides", {})
    changed_ov = prev is not None and prev_ov != current_ov
    if try_skip(t, r, "cues", "en", outputs, inputs, args.force or changed_ov or args.dump_narration):
        return

    tl = tools.require(cfg, "ffprobe")
    tp.update(5, "reading SRT and video")
    cues = srt.parse_file(srt_path, "edit/master.srt")
    info = probe(tl, video, "edit/master.mp4")
    if info.duration <= 0:
        raise Fail("FS_CORRUPT", "edit/master.mp4 reports no duration.", file="edit/master.mp4")
    pair = pair_checks(t, cues, info.duration, cfg)
    r.diagnostics.extend(pair)
    if any(d.level == "error" for d in pair):
        # A mismatched pair makes every timecode meaningless. Stop before writing any.
        return

    p = parse(src, "topic.md", t.id)
    narration = [s.say_text for s in p.slides]
    if args.dump_narration:
        lines = []
        for s in p.slides:
            lines.append(f"## slide {s.index}\n{s.say_text}\n")
        fsutil.write_text(t.build / "narration.txt", "\n".join(lines))
        r.artifacts.append(Envelope.artifact_for(t.root, t.build / "narration.txt", "narration"))

    tp.update(30, "aligning")
    c = cfg["cues"]
    ov = reviewfile.overrides(t)
    bad_ov = [k for k in ov if not 1 <= k <= len(p.slides)]
    for k in bad_ov:
        r.diagnostics.append(Diagnostic("CUE_SHEET_INVALID", f"review.json sets a timecode for slide {k}, but there are {len(p.slides)} slides.",
                                        topic=t.id, file="review.json"))
    if 1 in ov and ov[1] != 0:
        r.diagnostics.append(Diagnostic("CUE_SHEET_INVALID", "Slide 1 must start at 00:00:00.000; its override is ignored.",
                                        topic=t.id, file="review.json", level="warn"))
        ov.pop(1)
    for k, v in ov.items():
        if v > info.duration:
            r.diagnostics.append(Diagnostic("CUE_SHEET_INVALID", f"Hand-set timecode for slide {k} is after the end of the video.",
                                            topic=t.id, file="review.json", slide=k))
    al = align(narration, cues, window=c["window"], mis_max_tokens=c["mistranscription_max_tokens"],
               mis_similarity=c["mistranscription_similarity"], overrides=ov)
    tp.update(80, "writing cue sheet")

    decisions = reviewfile.load(t)["transcripts"]
    thr = c["min_confidence"]
    for b in al.boundaries:
        if b.timecode is None or b.confidence < thr:
            r.diagnostics.append(Diagnostic(
                "CUE_LOW_CONFIDENCE",
                f"Slide {b.slide} boundary matched at {b.confidence:.2f} confidence" + (f" ({b.reason})." if b.reason else "."),
                topic=t.id, file="edit/master.srt", slide=b.slide, line=cues[b.cue - 1].line if b.cue else None,
                hint="The edit may have removed the passage the break sat in. Watch the draft, then set the timecode by hand: "
                     f"bcn cues {t.rel} --set {b.slide}=HH:MM:SS.mmm",
                data={"confidence": b.confidence, "timecode": b.timecode}))
        elif b.mid_cue:
            r.diagnostics.append(Diagnostic("CUE_MID_CUE", f"Slide {b.slide} starts part way through cue {b.cue}; the slide will change up to one cue early.",
                                            topic=t.id, file="edit/master.srt", slide=b.slide, line=cues[b.cue - 1].line if b.cue else None))
    for dv in al.divergences:
        line = cues[dv.cue - 1].line if dv.cue else None
        if dv.kind == "cut" and dv.spans_boundary and all(k in ov for k in dv.boundary_slides):
            r.diagnostics.append(Diagnostic("CUE_CUT", f"A cut spans the break before slide {', '.join(map(str, dv.boundary_slides))}, placed by hand: \"{dv.script[:80]}\".",
                                            topic=t.id, file="edit/master.srt", slide=dv.slide, line=line, data=dv.to_json()))
        elif dv.kind == "cut" and dv.spans_boundary:
            r.diagnostics.append(Diagnostic("CUE_CUT_AT_BOUNDARY", f"A cut spans the break before slide {', '.join(map(str, dv.boundary_slides))}: \"{dv.script[:80]}\".",
                                            topic=t.id, file="edit/master.srt", slide=dv.slide, line=line,
                                            hint="A human needs to place that boundary. Watch the draft and use --set.",
                                            data=dv.to_json()))
        elif dv.kind == "cut":
            r.diagnostics.append(Diagnostic("CUE_CUT", f"Slide {dv.slide}: \"{dv.script[:80]}\" is not in the SRT.",
                                            topic=t.id, file="edit/master.srt", slide=dv.slide, line=line, data=dv.to_json()))
        elif dv.kind == "mistranscription":
            dec = decisions.get(dv.id)
            r.diagnostics.append(Diagnostic("CUE_MISTRANSCRIPTION", f"Slide {dv.slide}: SRT reads \"{dv.srt}\" where the script reads \"{dv.script}\".",
                                            level="info" if dec else "warn",
                                            topic=t.id, file="edit/master.srt", slide=dv.slide, line=line,
                                            hint="The partner translates from this SRT. Accept or correct it in review." if not dec else f"Reviewed: {dec['decision']}.",
                                            data={**dv.to_json(), "review": dec}))
        elif dv.kind == "paraphrase":
            r.diagnostics.append(Diagnostic("CUE_PARAPHRASE", f"Slide {dv.slide}: presenter said \"{dv.srt[:80]}\" instead of \"{dv.script[:80]}\".",
                                            topic=t.id, file="edit/master.srt", slide=dv.slide, line=line,
                                            hint="The approved script is what was signed off; the producer should know.",
                                            data=dv.to_json()))
        else:
            r.diagnostics.append(Diagnostic("CUE_INSERTION", f"Slide {dv.slide}: SRT has \"{dv.srt[:80]}\", which is not in the script.",
                                            topic=t.id, file="edit/master.srt", slide=dv.slide, line=line, data=dv.to_json()))
    if al.divergence_ratio > c["max_divergence"]:
        r.diagnostics.append(Diagnostic(
            "CUE_HEAVY_DIVERGENCE",
            f"{al.divergence_ratio:.0%} of the script is absent from or differs from the SRT; the limit is {c['max_divergence']:.0%}.",
            topic=t.id, file="edit/master.srt",
            hint="This almost always means the wrong SRT, the wrong topic, or a recording that needs redoing."))

    report = {
        "topic": t.id,
        "generated": utcnow(),
        "srt": {"path": "edit/master.srt", "cues": len(cues), "sha256": sha256_file(srt_path)},
        "video": {"path": "edit/master.mp4", "duration": info.duration},
        "thresholds": {"min_confidence": thr, "max_divergence": c["max_divergence"]},
        "script_tokens": al.script_tokens,
        "srt_tokens": al.srt_tokens,
        "divergence_ratio": al.divergence_ratio,
        "matched_ratio": al.matched_ratio,
        "boundaries": [b.to_json() for b in al.boundaries],
        "divergences": [{**d.to_json(), "review": decisions.get(d.id)} for d in al.divergences],
    }
    fsutil.write_json(t.cues_report, report)
    r.artifacts.append(Envelope.artifact_for(t.root, t.cues_report, "report"))

    if any(b.timecode is None for b in al.boundaries):
        # Without a timecode for every slide there is no complete cue sheet, and a
        # partial one must not exist. Remove any previous sheet so nothing uses it.
        if t.cues_csv.exists():
            t.cues_csv.unlink()
        return
    rows = ["slide,timecode"] + [f"{b.slide},{srt.fmt_cue_time(b.timecode or 0.0)}" for b in al.boundaries]
    fsutil.write_text(t.cues_csv, "\n".join(rows) + "\n")
    r.artifacts.append(Envelope.artifact_for(t.root, t.cues_csv, "cues"))
    r.extra.update({
        "slides": len(al.boundaries),
        "min_confidence": min(b.confidence for b in al.boundaries),
        "divergence_ratio": al.divergence_ratio,
        "mistranscriptions": sum(1 for d in al.divergences if d.kind == "mistranscription"),
        "unreviewed": sum(1 for d in al.divergences if d.kind == "mistranscription" and d.id not in decisions),
        "manual": sorted(ov),
    })
    r.extra["overrides"] = {str(k): v for k, v in sorted(ov.items())}
    tp.update(100, "done")


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    if args.lang != "en":
        raise Fail("USAGE", "cues has no --lang zh. Mandarin inherits the English cue sheet and SRT timings unchanged.")
    if (args.set or args.unset) and target.level != "topic":
        raise Fail("USAGE", "--set and --unset need a single topic directory.")

    def fn(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config) -> None:
        cues_topic(t, r, tp, cfg, args)

    run_topics(env, target, "cues", "en", fn, jobs=args.jobs)
