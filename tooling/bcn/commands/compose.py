"""bcn compose: a draft composite video, for checking, never for delivery.

Slides held for the intervals in cues.csv, the presenter composited against them
according to the configured layout, subtitles burned in, watermarked DRAFT, at
reduced resolution. Output is build/draft.mp4 and never goes near out/.

Bumpers never change the cue sheet: the body is composed on its own timeline,
where cues.csv is correct as written, and the intro and outro are joined around
it afterwards. The intro's duration is reported as body_offset.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from .. import cuesheet, fsutil, tools
from ..config import Config, load_theme, resolve_theme_name
from ..envelope import Diagnostic, Envelope, Fail, TopicResult
from ..markdown import parse
from ..media import ffmpeg, loudness, probe
from ..progress import TopicProgress
from ..runner import require_input, require_step, run_topics, try_skip
from ..tree import Target, Topic

HELP = "draft composite video"

FONT_CANDIDATES = ["/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--no-bumpers", action="store_true", help="skip intro and outro, for iterating on timings")


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def _esc(s: str) -> str:
    """Escape a value for use inside an ffmpeg filter argument."""
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")


def filter_graph(c: dict, duration: float, subs_name: str, sub_font: str, watermark_font: str | None,
                 fonts_dir: Path | None = None) -> str:
    W, H, fps = _even(c["width"]), _even(c["height"]), c["fps"]
    parts = []
    if c["layout"] == "side_by_side":
        sw = _even(W * 0.64)
        sh = _even(sw * 9 / 16)
        pw, ph = W - sw, sh
        y = (H - sh) // 2
        parts.append(f"color=c=black:s={W}x{H}:r={fps}:d={duration:.3f}[bg]")
        parts.append(f"[1:v]scale={sw}:{sh}:force_original_aspect_ratio=decrease,pad={sw}:{sh}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1[slides]")
        parts.append(f"[0:v]scale={pw}:{ph}:force_original_aspect_ratio=increase,crop={pw}:{ph},fps={fps},setsar=1[pres]")
        parts.append(f"[bg][slides]overlay=x=0:y={y}:eof_action=repeat[a]")
        parts.append(f"[a][pres]overlay=x={sw}:y={y}:eof_action=repeat[comp]")
    elif c["layout"] == "inset":
        iw = _even(W * c["inset_scale"])
        m = c["inset_margin"]
        pos = c["inset_position"]
        x = f"W-w-{m}" if "right" in pos else str(m)
        y = f"H-h-{m}" if "bottom" in pos else str(m)
        parts.append(f"[1:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1[slides]")
        parts.append(f"[0:v]scale={iw}:-2,fps={fps},setsar=1[pres]")
        parts.append(f"[slides][pres]overlay=x={x}:y={y}:eof_action=repeat[comp]")
    else:
        raise Fail("CONFIG_INVALID", f"compose.layout '{c['layout']}' is not one of inset, side_by_side.", file="programme.toml")
    style = f"FontName={sub_font},FontSize={c.get('subtitle_font_size', 16)},Outline=1,Shadow=0,MarginV=12"
    wm = c["watermark"]
    draw = ""
    if wm:
        ff = f"fontfile='{_esc(watermark_font)}':" if watermark_font else ""
        draw = (f",drawtext={ff}text='{_esc(wm)}':x=w-tw-{_even(W * 0.02)}:y=h-th-{_even(H * 0.02)}:"
                f"fontsize={_even(H * 0.06)}:fontcolor=white@0.85:box=1:boxcolor=0xc0392b@0.75:boxborderw={_even(H * 0.012)}")
    fd = f":fontsdir='{_esc(str(fonts_dir))}'" if fonts_dir and fonts_dir.is_dir() else ""
    parts.append(f"[comp]subtitles=filename='{_esc(subs_name)}'{fd}:force_style='{_esc(style)}'{draw},format=yuv420p[v]")
    return ";".join(parts)


def _encode_args(c: dict) -> list[str]:
    return ["-c:v", "libx264", "-preset", c["preset"], "-crf", str(c["crf"]), "-pix_fmt", "yuv420p", "-r", str(c["fps"]),
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart"]


def compose_topic(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config, args: argparse.Namespace) -> None:
    lang = args.lang
    c = cfg["compose"]
    require_input(t, t.video, stable=True)
    if not t.cues_csv.is_file():
        raise Fail("STEP_PREREQUISITE", "There is no cue sheet.", topic=t.id, hint="Run bcn cues first.")
    # A draft is how a low-confidence cue sheet gets checked, so compose accepts a
    # cue sheet whose cues run failed, as long as it is current.
    if not fsutil.is_fresh([t.cues_csv], [t.src("en"), t.srt("en"), t.video]):
        raise Fail("STEP_PREREQUISITE", "The cue sheet is older than the script, SRT or video.", hint="Run bcn cues again.")
    require_step(t, "render", lang, [t.src(lang)], f"bcn render{' --lang ' + lang if lang != 'en' else ''}")
    n = len(parse(t.src(lang), t.src(lang).name, t.id).slides)
    pngs = [t.slides_dir(lang) / t.slide_name(i, lang) for i in range(1, n + 1)]
    for p in pngs:
        require_input(t, p, lang, step_hint=f"bcn render{' --lang ' + lang if lang != 'en' else ''}")
    subs = t.subtitle_out(lang, "srt")
    if not subs.is_file():
        subs = t.srt(lang)
    require_input(t, subs, lang)

    bumpers: dict[str, Path] = {}
    if not args.no_bumpers:
        for kind in ("intro", "outro"):
            rel = cfg["bumpers"][kind]
            if rel:
                p = (t.root / rel).resolve()
                if not str(p).startswith(str(t.root.resolve())) or not p.is_file():
                    r.diagnostics.append(Diagnostic("COMPOSE_BUMPER", f"The {kind} bumper '{rel}' does not exist inside the programme root; it is skipped.",
                                                    topic=t.id, lang=lang, file="programme.toml"))
                else:
                    bumpers[kind] = p

    out = t.draft(lang)
    inputs = [t.video, t.cues_csv, subs, t.root / "programme.toml", *pngs, *bumpers.values()]
    if try_skip(t, r, "compose", lang, [out], inputs, args.force):
        return

    tl = tools.require(cfg, "ffmpeg", "ffprobe")
    times = cuesheet.read(t.cues_csv, n, t.cues_csv.name)
    info = probe(tl, t.video, "edit/master.mp4")
    duration = info.duration
    if times[-1] >= duration:
        raise Fail("CUE_SHEET_INVALID", "The last slide starts after the video ends.", file=t.cues_csv.name)

    theme = load_theme(t.root, resolve_theme_name(cfg, args.theme))
    sub_font = theme.fonts[lang][0]
    wm_font = next((f for f in FONT_CANDIDATES if Path(f).is_file()), None)

    work = t.build / f".compose-{lang}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        # Slide track on the body's own timeline: cues.csv is correct as written here.
        lines = ["ffconcat version 1.0"]
        for i, p in enumerate(pngs):
            end = times[i + 1] if i + 1 < len(times) else duration
            lines += [f"file '{p.as_posix()}'", f"duration {end - times[i]:.3f}"]
        lines.append(f"file '{pngs[-1].as_posix()}'")
        (work / "slides.ffconcat").write_text("\n".join(lines) + "\n")
        shutil.copyfile(subs, work / "subs.srt")

        share = 0.85 if bumpers else 1.0
        body = work / "body.mp4"
        graph = filter_graph(c, duration, "subs.srt", sub_font, wm_font, theme.fonts_dir)
        tp.update(1, "encoding")
        ffmpeg(tl, ["-i", str(t.video), "-f", "concat", "-safe", "0", "-i", "slides.ffconcat",
                    "-filter_complex", graph, "-map", "[v]", "-map", "0:a?", "-t", f"{duration:.3f}",
                    *_encode_args(c), str(body)],
               duration=duration, on_pct=lambda pct: tp.update(pct * share, "encoding"), cwd=str(work))

        body_offset = 0.0
        if bumpers:
            tp.update(86, "bumpers")
            body_lufs = loudness(tl, t.video)
            segs = []
            W, H, fps = _even(c["width"]), _even(c["height"]), c["fps"]
            for kind in ("intro", "outro"):
                if kind not in bumpers:
                    continue
                src = bumpers[kind]
                bi = probe(tl, src, str(src.relative_to(t.root)))
                seg = work / f"{kind}.mp4"
                vf = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1,format=yuv420p"
                a_in = [] if bi.has_audio else ["-f", "lavfi", "-t", f"{bi.duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
                af = f"loudnorm=I={body_lufs:.1f}:TP=-1.5:LRA=11,aresample=48000" if (body_lufs is not None and bi.has_audio) else "aresample=48000"
                amap = "0:a" if bi.has_audio else "1:a"
                ffmpeg(tl, ["-i", str(src), *a_in, "-vf", vf, "-af", af, "-map", "0:v", "-map", amap,
                            "-t", f"{bi.duration:.3f}", *_encode_args(c), str(seg)], duration=bi.duration)
                if kind == "intro":
                    body_offset = bi.duration
                segs.append((kind, seg))
            order = [s for k, s in segs if k == "intro"] + [body] + [s for k, s in segs if k == "outro"]
            (work / "join.ffconcat").write_text("ffconcat version 1.0\n" + "".join(f"file '{p.name}'\n" for p in order))
            joined = work / "joined.mp4"
            ffmpeg(tl, ["-f", "concat", "-safe", "0", "-i", "join.ffconcat", "-c", "copy", "-movflags", "+faststart", str(joined)],
                   duration=None, cwd=str(work))
            body = joined

        with fsutil.atomic_path(out) as tmp:
            shutil.move(str(body), tmp)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    r.artifacts.append(Envelope.artifact_for(t.root, out, "draft"))
    r.extra.update({"layout": c["layout"], "resolution": [_even(c["width"]), _even(c["height"])],
                    "body_offset": round(body_offset, 3), "bumpers": sorted(bumpers), "duration": round(duration, 3)})
    tp.update(100, "done")


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    def fn(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config) -> None:
        compose_topic(t, r, tp, cfg, args)

    run_topics(env, target, "compose", args.lang, fn, jobs=args.jobs)
