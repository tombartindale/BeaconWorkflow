"""bcn package: assemble the delivery folder out/.

English builds out/ afresh in a sibling temp directory and swaps it in whole, so
a half-built package never looks complete. Mandarin adds its files beside the
English and writes its own manifest.zh.json. A topic's intro and outro from bcn
bumpers, if it has them, go in as separate files: the master is never changed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path

from .. import cuesheet, fsutil, tools
from ..config import Config
from ..envelope import Diagnostic, Envelope, Fail, TopicResult, sha256_file, utcnow
from ..markdown import parse
from ..media import ffmpeg, probe
from ..progress import TopicProgress
from ..runner import require_input, require_step, run_topics, try_skip
from ..tree import Target, Topic

HELP = "assemble the delivery folder"


def add_args(p: argparse.ArgumentParser) -> None:
    pass


def _file_entry(p: Path) -> dict:
    return {"name": p.name, "bytes": p.stat().st_size, "sha256": sha256_file(p)}


def _needs_transcode(info, d: dict) -> list[str]:
    reasons = []
    if info.vcodec != d["video_codec"]:
        reasons.append(f"video codec {info.vcodec} ≠ {d['video_codec']}")
    if (info.width, info.height) != (d["width"], d["height"]):
        reasons.append(f"size {info.width}x{info.height} ≠ {d['width']}x{d['height']}")
    if info.fps and abs(info.fps - d["fps"]) > 0.01:
        reasons.append(f"frame rate {info.fps:g} ≠ {d['fps']}")
    if info.acodec and info.acodec != d["audio_codec"]:
        reasons.append(f"audio codec {info.acodec} ≠ {d['audio_codec']}")
    return reasons


def package_en(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config, force: bool) -> None:
    src = t.src("en")
    for p in (src, t.video, t.srt("en")):
        require_input(t, p, "en", stable=p != src)
    require_step(t, "validate", "en", [src], "bcn validate")
    render = require_step(t, "render", "en", [src], "bcn render")
    require_step(t, "cues", "en", [src, t.srt("en"), t.video], "bcn cues")
    subs_env = require_step(t, "subtitles", "en", [t.srt("en")], "bcn subtitles")

    p = parse(src, "topic.md", t.id)
    n = len(p.slides)
    pngs = t.slide_pngs("en")
    times = cuesheet.read(t.cues_csv, None, t.cues_csv.name)
    if not (len(pngs) == len(times) == n):
        raise Fail("PKG_COUNT_MISMATCH", f"Slide images: {len(pngs)}, cue sheet rows: {len(times)}, source slides: {n}. They must be equal.",
                   hint="Re-run render and cues against the current topic.md.")
    fmt = cfg["subtitles"]["format"]
    subs = [t.subtitle_out("en", "srt")] + ([t.subtitle_out("en", "vtt")] if fmt in ("vtt", "both") else [])
    for s in subs:
        require_input(t, s, "en", step_hint="bcn subtitles")
    bumpers = _bumpers(t, "en")

    inputs = [src, t.video, t.srt("en"), t.cues_csv, *pngs, *subs, *bumpers, t.root / "programme.toml"]
    if try_skip(t, r, "package", "en", [t.manifest("en")], inputs, force):
        return

    tl = tools.require(cfg, "ffprobe", *(["ffmpeg"] if cfg["delivery"]["transcode"] else []))
    info = probe(tl, t.video, "edit/master.mp4")
    staging = Path(tempfile.mkdtemp(prefix=".out.partial-", dir=t.dir))
    try:
        tp.update(5, "copying video")
        video_out = staging / f"{t.id}.mp4"
        transcode = None
        d = cfg["delivery"]
        reasons = _needs_transcode(info, d) if d["transcode"] else []
        if reasons:
            ffmpeg(tl, ["-i", str(t.video), "-vf", f"scale={d['width']}:{d['height']},fps={d['fps']}",
                        "-c:v", "libx264" if d["video_codec"] == "h264" else d["video_codec"], "-b:v", d["video_bitrate"],
                        "-pix_fmt", "yuv420p", "-c:a", d["audio_codec"], "-b:a", d["audio_bitrate"], "-movflags", "+faststart",
                        str(video_out)], duration=info.duration, on_pct=lambda x: tp.update(5 + x * 0.8, "transcoding"))
            transcode = {"reasons": reasons, "original_sha256": sha256_file(t.video), "transcoded_sha256": sha256_file(video_out),
                         "spec": {k: v for k, v in d.items() if k != "transcode"}}
            r.diagnostics.append(Diagnostic("PKG_TRANSCODED", f"Video transcoded to the delivery spec: {'; '.join(reasons)}.",
                                            topic=t.id, lang="en", file="edit/master.mp4"))
        else:
            shutil.copyfile(t.video, video_out)  # copied through unmodified
        tp.update(85, "copying slides")
        for i, png in enumerate(pngs, 1):
            shutil.copyfile(png, staging / t.slide_name(i, "en"))
        for s in [*subs, *bumpers]:
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
            "transcode": transcode,
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
                    "transcoded": transcode is not None})
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
    n = len(parse(src, src.name, t.id).slides)
    pngs = t.slide_pngs("zh")
    if not (len(pngs) == n == en_manifest["slide_count"]):
        raise Fail("PKG_COUNT_MISMATCH", f"Mandarin slide images: {len(pngs)}, Mandarin slides: {n}, English slides: {en_manifest['slide_count']}.")
    fmt = cfg["subtitles"]["format"]
    subs = [t.subtitle_out("zh", "srt")] + ([t.subtitle_out("zh", "vtt")] if fmt in ("vtt", "both") else [])
    bumpers = _bumpers(t, "zh")
    inputs = [src, t.srt("zh"), *pngs, *subs, *bumpers, t.manifest("en")]
    if try_skip(t, r, "package", "zh", [t.manifest("zh")], inputs, force):
        return
    written = []
    for i, png in enumerate(pngs, 1):
        dest = t.out / t.slide_name(i, "zh")
        with fsutil.atomic_path(dest) as tmp:
            shutil.copyfile(png, tmp)
        written.append(dest)
    for s in [*subs, *bumpers]:
        dest = t.out / s.name
        with fsutil.atomic_path(dest) as tmp:
            shutil.copyfile(s, tmp)
        written.append(dest)
    md = t.out / f"{t.id}.zh.md"
    fsutil.write_text(md, src.read_text(encoding="utf-8"))
    written.append(md)
    shared = [e for e in en_manifest["files"] if e["name"] in (f"{t.id}.mp4", f"{t.id}.cues.csv")]
    manifest = {
        "topic_id": t.id,
        "lang": "zh",
        "slide_count": n,
        "duration": en_manifest.get("duration"),
        "built": utcnow(),
        "inherits": {"from": "manifest.json", "files": shared,
                     "note": "Mandarin reuses the English video, cue sheet and subtitle timings unchanged."},
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
