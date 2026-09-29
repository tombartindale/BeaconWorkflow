"""bcn package: assemble the delivery folder out/.

The delivered video is compose's output, not the raw editor export: slides, presenter
and bumpers composited at the partner's delivery quality (bcn compose §). The subtitle
file delivered beside it is compose's sidecar, its timings already shifted to match. The
master and cues.csv are never touched by any of this; package only ever reads them.

English builds out/ afresh in a sibling temp directory and swaps it in whole, so a
half-built package never looks complete. Mandarin composes and delivers its own video
(same presenter footage, its own slides, bumpers and subtitles) and writes its own
manifest.zh.json beside the English one. A topic's intro and outro from bcn bumpers, if
it has them, are also delivered as separate files, alongside the merged composed video.
"""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path

from .. import cuesheet, fsutil, srt, tools
from ..config import Config
from ..envelope import Diagnostic, Envelope, Fail, TopicResult, sha256_file, utcnow
from ..markdown import parse
from ..media import probe
from ..progress import TopicProgress
from ..runner import require_input, require_step, run_topics, try_skip
from ..tree import Target, Topic

HELP = "assemble the delivery folder"


def add_args(p: argparse.ArgumentParser) -> None:
    pass


def _file_entry(p: Path) -> dict:
    return {"name": p.name, "bytes": p.stat().st_size, "sha256": sha256_file(p)}


def _deliver_subtitles(sidecar: Path, dest_srt: Path, fmt: str) -> list[Path]:
    """Copies compose's shifted sidecar SRT into place, plus a VTT derived from the same
    shifted cues if the delivery format calls for it. Never re-shifts anything. Each file
    is written atomically, so this is safe whether dest_srt is in fresh staging (package_en)
    or lands beside an already-live package (package_zh)."""
    with fsutil.atomic_path(dest_srt) as tmp:
        shutil.copyfile(sidecar, tmp)
    written = [dest_srt]
    if fmt in ("vtt", "both"):
        cues = srt.parse_file(sidecar, sidecar.name)
        dest_vtt = dest_srt.with_suffix(".vtt")
        fsutil.write_text(dest_vtt, srt.to_vtt(cues))
        written.append(dest_vtt)
    return written


def package_en(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config, force: bool) -> None:
    src = t.src("en")
    for p in (src, t.video, t.srt("en")):
        require_input(t, p, "en", stable=p != src)
    require_step(t, "validate", "en", [src], "bcn validate")
    render = require_step(t, "render", "en", [src], "bcn render")
    require_step(t, "cues", "en", [src, t.srt("en"), t.video], "bcn cues")
    subs_env = require_step(t, "subtitles", "en", [t.srt("en")], "bcn subtitles")
    compose_env = require_step(t, "compose", "en", [t.video, t.cues_csv, t.src("en")], "bcn compose")
    compose_result = compose_env.get("results", [{}])[0]
    if compose_result.get("mode") == "draft":
        raise Fail("STEP_PREREQUISITE", "The last compose run was --draft; that is never delivered.",
                   topic=t.id, lang="en", hint="Run bcn compose again without --draft.")

    p = parse(src, "topic.md", t.id)
    n = len(p.slides)
    pngs = t.slide_pngs("en")
    times = cuesheet.read(t.cues_csv, None, t.cues_csv.name)
    if not (len(pngs) == len(times) == n):
        raise Fail("PKG_COUNT_MISMATCH", f"Slide images: {len(pngs)}, cue sheet rows: {len(times)}, source slides: {n}. They must be equal.",
                   hint="Re-run render and cues against the current topic.md.")
    composed = t.composed("en")
    sidecar = t.composed_srt("en")
    require_input(t, composed, "en", step_hint="bcn compose")
    require_input(t, sidecar, "en", step_hint="bcn compose (without --burn-subtitles)")
    bumpers = _bumpers(t, "en")

    inputs = [src, t.video, t.srt("en"), t.cues_csv, composed, sidecar, *pngs, *bumpers, t.root / "programme.toml"]
    if try_skip(t, r, "package", "en", [t.manifest("en")], inputs, force):
        return

    tl = tools.require(cfg, "ffprobe")
    info = probe(tl, composed, "build/composed.mp4")
    staging = Path(tempfile.mkdtemp(prefix=".out.partial-", dir=t.dir))
    try:
        tp.update(5, "copying video")
        shutil.copyfile(composed, staging / f"{t.id}.mp4")
        tp.update(70, "copying subtitles")
        fmt = cfg["subtitles"]["format"]
        _deliver_subtitles(sidecar, staging / f"{t.id}.en.srt", fmt)
        tp.update(85, "copying slides")
        for i, png in enumerate(pngs, 1):
            shutil.copyfile(png, staging / t.slide_name(i, "en"))
        for s in bumpers:
            shutil.copyfile(s, staging / s.name)
        shutil.copyfile(t.cues_csv, staging / t.cues_csv.name)
        (staging / f"{t.id}.md").write_text(p.stripped_file(), encoding="utf-8")

        # Carry Mandarin files across; status judges their freshness against their own manifest.
        if t.out.is_dir() and t.manifest("zh").is_file():
            zh = fsutil.read_json(t.manifest("zh")) or {}
            for f in zh.get("files", []):
                if (t.out / f["name"]).is_file() and not (staging / f["name"]).exists():
                    shutil.copyfile(t.out / f["name"], staging / f["name"])
            shutil.copyfile(t.manifest("zh"), staging / "manifest.zh.json")

        files = sorted((x for x in staging.iterdir() if x.is_file() and not x.name.startswith("manifest")),
                       key=lambda x: x.name)
        en_files = [f for f in files if not _is_zh(f.name, t)]
        manifest = {
            "topic_id": t.id,
            "lang": "en",
            "slide_count": n,
            "duration": round(info.duration, 3),
            "built": utcnow(),
            "theme": render.get("results", [{}])[0].get("theme"),
            "marp_cli": render.get("results", [{}])[0].get("marp_cli"),
            "chrome": render.get("results", [{}])[0].get("chrome"),
            "subtitles_modified": subs_env.get("results", [{}])[0].get("modified", False),
            "body_offset": compose_result.get("body_offset", 0.0),
            "bumpers_composited": compose_result.get("bumpers", []),
            "files": [_file_entry(f) for f in en_files],
        }
        fsutil.write_json(staging / "manifest.json", manifest)

        # Swap the finished folder in whole.
        old = None
        if t.out.exists():
            old = t.dir / f".out.old-{os.getpid()}"
            os.replace(t.out, old)
        os.replace(staging, t.out)
        if old:
            shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    for f in [t.manifest("en")] + [t.out / e["name"] for e in manifest["files"]]:
        r.artifacts.append(Envelope.artifact_for(t.root, f, "package"))
    r.extra.update({"slides": n, "duration": round(info.duration, 3), "files": len(manifest["files"]) + 1,
                    "body_offset": manifest["body_offset"]})
    tp.update(100, "done")


def _is_zh(name: str, t: Topic) -> bool:
    return name.startswith(f"{t.id}-zh-") or name.endswith((".zh.srt", ".zh.vtt", ".zh.md", ".zh.mp4"))


def _bumpers(t: Topic, lang: str) -> list[Path]:
    """The topic's intro and outro, if bcn bumpers has made them; they must be current to be delivered."""
    found = [p for p in (t.bumper("intro", lang), t.bumper("outro", lang)) if p.is_file()]
    if found:
        require_step(t, "bumpers", lang, [t.src(lang)], f"bcn bumpers{' --lang ' + lang if lang != 'en' else ''}")
    return found


def package_zh(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config, force: bool) -> None:
    src = t.src("zh")
    require_input(t, src, "zh")
    require_input(t, t.srt("zh"), "zh")
    en_manifest = fsutil.read_json(t.manifest("en"))
    if not en_manifest:
        raise Fail("STEP_PREREQUISITE", "The English package does not exist.", hint="Run bcn package first.")
    require_step(t, "validate", "zh", [src, t.src("en")], "bcn validate --lang zh")
    require_step(t, "render", "zh", [src], "bcn render --lang zh")
    require_step(t, "subtitles", "zh", [t.srt("zh"), t.srt("en")], "bcn subtitles --lang zh")
    compose_env = require_step(t, "compose", "zh", [t.video, t.cues_csv, src], "bcn compose --lang zh")
    compose_result = compose_env.get("results", [{}])[0]
    if compose_result.get("mode") == "draft":
        raise Fail("STEP_PREREQUISITE", "The last compose --lang zh run was --draft; that is never delivered.",
                   topic=t.id, lang="zh", hint="Run bcn compose --lang zh again without --draft.")
    n = len(parse(src, src.name, t.id).slides)
    pngs = t.slide_pngs("zh")
    if not (len(pngs) == n == en_manifest["slide_count"]):
        raise Fail("PKG_COUNT_MISMATCH", f"Mandarin slide images: {len(pngs)}, Mandarin slides: {n}, English slides: {en_manifest['slide_count']}.")
    composed = t.composed("zh")
    sidecar = t.composed_srt("zh")
    require_input(t, composed, "zh", step_hint="bcn compose --lang zh")
    require_input(t, sidecar, "zh", step_hint="bcn compose --lang zh (without --burn-subtitles)")
    bumpers = _bumpers(t, "zh")
    inputs = [src, t.srt("zh"), composed, sidecar, *pngs, *bumpers, t.manifest("en")]
    if try_skip(t, r, "package", "zh", [t.manifest("zh")], inputs, force):
        return

    tl = tools.require(cfg, "ffprobe")
    info = probe(tl, composed, "build/composed.zh.mp4")
    written = []
    dest_video = t.out / f"{t.id}.zh.mp4"
    with fsutil.atomic_path(dest_video) as tmp:
        shutil.copyfile(composed, tmp)
    written.append(dest_video)
    # Written straight into t.out, same as the slides and bumpers below: package_zh adds
    # beside an already-live package rather than replacing it whole (each file's own write
    # is still atomic).
    fmt = cfg["subtitles"]["format"]
    written.extend(_deliver_subtitles(sidecar, t.out / f"{t.id}.zh.srt", fmt))
    for i, png in enumerate(pngs, 1):
        dest = t.out / t.slide_name(i, "zh")
        with fsutil.atomic_path(dest) as tmp:
            shutil.copyfile(png, tmp)
        written.append(dest)
    for s in bumpers:
        dest = t.out / s.name
        with fsutil.atomic_path(dest) as tmp:
            shutil.copyfile(s, tmp)
        written.append(dest)
    md = t.out / f"{t.id}.zh.md"
    fsutil.write_text(md, src.read_text(encoding="utf-8"))
    written.append(md)
    manifest = {
        "topic_id": t.id,
        "lang": "zh",
        "slide_count": n,
        "duration": round(info.duration, 3),
        "built": utcnow(),
        "body_offset": compose_result.get("body_offset", 0.0),
        "bumpers_composited": compose_result.get("bumpers", []),
        # The cue sheet is shared: Mandarin has no cues step of its own (same presenter
        # footage, same timing), so <id>.cues.csv is delivered once, by package_en.
        "shares_cue_sheet_with": "manifest.json",
        "files": [_file_entry(f) for f in written],
    }
    fsutil.write_json(t.manifest("zh"), manifest)
    for f in [t.manifest("zh"), *written]:
        r.artifacts.append(Envelope.artifact_for(t.root, f, "package"))
    r.extra.update({"slides": n, "files": len(written) + 1})


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    def fn(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config) -> None:
        (package_en if args.lang == "en" else package_zh)(t, r, tp, cfg, args.force)

    run_topics(env, target, "package", args.lang, fn, jobs=args.jobs)
