"""bcn compose: the delivered video. Slides composited against the presenter, with the
topic's bumpers baked in, at the partner's delivery quality. This is what package copies
into out/.

Subtitles are not burned in by default: a sidecar SRT is written instead, its timings
shifted by the intro's duration so it lines up with the composed file's own timeline.
edit/master.srt itself is never touched, and cues.csv keeps the master's own timing.

--draft trades all of that for a small, fast, watermarked, burned-in-subtitle file for
checking cue timing quickly: never delivered, never confused with the real thing.

Bumpers never change the cue sheet: the body is composed on its own timeline, where
cues.csv is correct as written, and the intro and outro are joined around it afterwards.
The intro's duration is reported as body_offset, in the envelope and in the delivered
manifest, so the sidecar SRT's shift is always traceable to a real number. The topic's
own bumpers from bcn bumpers are used when current, else the files named in [bumpers].
"""

from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path

from .. import cuesheet, fsutil, srt, tools
from ..config import Config, Theme, load_theme, resolve_theme_name
from ..envelope import Diagnostic, Envelope, Fail, TopicResult
from ..markdown import parse
from ..media import ffmpeg, loudness, probe
from ..progress import TopicProgress, run as run_proc
from ..runner import require_input, require_step, run_topics, try_skip
from ..tree import Target, Topic

HELP = "composite video: what package delivers"

FONT_CANDIDATES = ["/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


@dataclass
class EncodeSpec:
    """What compose actually builds to: [delivery] by default, [compose]'s draft_* under --draft."""
    width: int
    height: int
    fps: int
    watermark: str
    video_codec: str
    audio_codec: str
    crf: int
    preset: str
    bitrate: tuple[str, str] | None  # (video, audio); None in draft mode, which uses crf instead


def encode_spec(cfg: Config, draft: bool) -> EncodeSpec:
    c = cfg["compose"]
    if draft:
        return EncodeSpec(c["draft_width"], c["draft_height"], c["draft_fps"], c["draft_watermark"],
                          "h264", "aac", c["draft_crf"], c["draft_preset"], bitrate=None)
    d = cfg["delivery"]
    return EncodeSpec(d["width"], d["height"], d["fps"], "", d["video_codec"], d["audio_codec"],
                      crf=18, preset="slow", bitrate=(d["video_bitrate"], d["audio_bitrate"]))


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--no-bumpers", action="store_true", help="skip intro and outro, for iterating on timings")
    p.add_argument("--draft", action="store_true",
                   help="a small, fast, watermarked file with subtitles burned in, for checking cue timing; "
                        "never delivered")
    p.add_argument("--burn-subtitles", action="store_true",
                   help="burn subtitles into the video instead of writing a sidecar SRT")


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def _esc(s: str) -> str:
    """Escape a value for use inside an ffmpeg filter argument."""
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")


def watermark_logo_html(theme: Theme, width: int, height: int) -> str:
    """A page the exact size of the watermark itself, transparent, the theme's logo filling
    it. Unlike bumpers' outro card (a full slide-sized frame with the logo centred in it), this
    page has no frame of its own to crop out: card.mjs can screenshot it directly."""
    logo = theme.asset(theme.bumper["logo"]) if theme.bumper else None
    img = f"<img src='{logo.as_uri()}' alt=''>" if logo else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html, body {{ margin: 0; width: {width}px; height: {height}px; overflow: hidden; background: transparent; }}
img {{ display: block; width: 100%; height: 100%; object-fit: contain; }}
</style></head><body>{img}</body></html>
"""


def filter_graph(c: dict, es: EncodeSpec, duration: float, burn_subs: str | None, sub_font: str,
                 watermark_font: str | None, fonts_dir: Path | None = None, safe_right: float = 0.5,
                 logo_input: str | None = None) -> str:
    """burn_subs is the SRT filename to burn in, or None to leave the video plain (sidecar mode).

    safe_right is the theme's safe_area.right (the fraction of the frame kept clear of slide
    content for side_by_side): the presenter fills exactly that share, full height, cropped to
    fit with no letterboxing, on the assumption the footage is already framed for it.

    logo_input is the ffmpeg input label (e.g. "2:v") of a pre-rendered, transparent logo PNG
    to draw over the bottom-right corner of the whole frame, side_by_side only: a watermark on
    top of the presenter, not the small logo bumpers already put on the outro card.
    """
    W, H, fps = _even(es.width), _even(es.height), es.fps
    parts = []
    if c["layout"] == "side_by_side":
        sw = W - _even(W * safe_right)  # where the presenter's share starts: the theme's safe_right
        pw = W - sw                     # the presenter's share: exactly the width the theme reserves
        # The slide PNG is already the full 1920x1080 frame with its content confined to the
        # left by the theme's own safe_area.right (bcn render enforces this), so it is used as
        # the full-size base layer directly, with only the presenter needing any scaling.
        parts.append(f"[1:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1[slides]")
        parts.append(f"[0:v]scale={pw}:{H}:force_original_aspect_ratio=increase,crop={pw}:{H},fps={fps},setsar=1[pres]")
        if logo_input:
            m = _even(H * 0.03)
            parts.append(f"[slides][pres]overlay=x={sw}:y=0:eof_action=repeat[comp0]")
            parts.append(f"[comp0][{logo_input}]overlay=x=W-w-{m}:y=H-h-{m}[comp]")
        else:
            parts.append(f"[slides][pres]overlay=x={sw}:y=0:eof_action=repeat[comp]")
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
    draw = ""
    if es.watermark:
        ff = f"fontfile='{_esc(watermark_font)}':" if watermark_font else ""
        draw = (f",drawtext={ff}text='{_esc(es.watermark)}':x=w-tw-{_even(W * 0.02)}:y=h-th-{_even(H * 0.02)}:"
                f"fontsize={_even(H * 0.06)}:fontcolor=white@0.85:box=1:boxcolor=0xc0392b@0.75:boxborderw={_even(H * 0.012)}")
    if burn_subs:
        style = f"FontName={sub_font},FontSize={c.get('subtitle_font_size', 16)},Outline=1,Shadow=0,MarginV=12"
        fd = f":fontsdir='{_esc(str(fonts_dir))}'" if fonts_dir and fonts_dir.is_dir() else ""
        parts.append(f"[comp]subtitles=filename='{_esc(burn_subs)}'{fd}:force_style='{_esc(style)}'{draw},format=yuv420p[v]")
    else:
        parts.append(f"[comp]null{draw},format=yuv420p[v]" if draw else "[comp]format=yuv420p[v]")
    return ";".join(parts)


def _encode_args(es: EncodeSpec) -> list[str]:
    quality = ["-b:v", es.bitrate[0]] if es.bitrate else ["-crf", str(es.crf)]
    audio_bitrate = es.bitrate[1] if es.bitrate else "128k"
    vcodec = "libx264" if es.video_codec == "h264" else es.video_codec
    return ["-c:v", vcodec, "-preset", es.preset, *quality, "-pix_fmt", "yuv420p", "-r", str(es.fps),
            "-c:a", es.audio_codec, "-b:a", audio_bitrate, "-ar", "48000", "-ac", "2", "-movflags", "+faststart"]


def _shift_srt(subs: Path, offset: float) -> str:
    """subs shifted forward by offset, for the sidecar delivered beside the composed video.

    edit/master.srt (or the prepared subtitle file) is only ever read here, never written:
    this returns text for a new file. If offset is 0 the cues are unchanged in value, only
    reformatted, which is harmless (SRT_CORRECTION-style tools compare parsed cues, not bytes).
    """
    cues = srt.parse_file(subs, str(subs.name))
    shifted = [srt.Cue(c.index, c.start + offset, c.end + offset, c.text, c.line) for c in cues]
    return srt.to_srt(shifted)


def compose_topic(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config, args: argparse.Namespace) -> None:
    lang = args.lang
    c = cfg["compose"]
    draft = args.draft
    burn_in = draft or args.burn_subtitles
    es = encode_spec(cfg, draft)
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

    # The topic's own bumpers (bcn bumpers) come first; the programme-wide files in [bumpers] are the fallback.
    bumpers: dict[str, Path] = {}
    if not args.no_bumpers:
        for kind in ("intro", "outro"):
            made = t.bumper(kind, lang)
            if made.is_file():
                if fsutil.is_fresh([made], [t.src(lang)]):
                    bumpers[kind] = made
                    continue
                r.diagnostics.append(Diagnostic("COMPOSE_BUMPER", f"The topic's {kind} is older than {t.src(lang).name}; it is skipped.",
                                                topic=t.id, lang=lang, file=str(made.relative_to(t.dir)),
                                                hint=f"Run bcn bumpers{' --lang ' + lang if lang != 'en' else ''} again."))
            rel = cfg["bumpers"][kind]
            if rel:
                p = (t.root / rel).resolve()
                if not str(p).startswith(str(t.root.resolve())) or not p.is_file():
                    r.diagnostics.append(Diagnostic("COMPOSE_BUMPER", f"The {kind} bumper '{rel}' does not exist inside the programme root; it is skipped.",
                                                    topic=t.id, lang=lang, file="programme.toml"))
                else:
                    bumpers[kind] = p

    out = t.draft(lang) if draft else t.composed(lang)
    sidecar = None if (draft or burn_in) else t.composed_srt(lang)
    outputs = [out, *([sidecar] if sidecar else [])]
    inputs = [t.video, t.cues_csv, subs, t.root / "programme.toml", *pngs, *bumpers.values()]
    if try_skip(t, r, "compose", lang, outputs, inputs, args.force):
        return

    theme = load_theme(t.root, resolve_theme_name(cfg, args.theme))
    # A watermark of the theme's logo over the presenter, side_by_side only: drawn on top of
    # everything, unlike bumpers' outro card, which sits on its own separate slide.
    want_logo = c["layout"] == "side_by_side" and bool(theme.bumper) and bool(theme.bumper.get("logo"))
    tl = tools.require(cfg, "ffmpeg", "ffprobe", *(["chrome"] if want_logo else []))
    times = cuesheet.read(t.cues_csv, n, t.cues_csv.name)
    info = probe(tl, t.video, "edit/master.mp4")
    duration = info.duration
    if times[-1] >= duration:
        raise Fail("CUE_SHEET_INVALID", "The last slide starts after the video ends.", file=t.cues_csv.name)

    sub_font = theme.subtitle_font(lang)
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
        if burn_in:
            shutil.copyfile(subs, work / "subs.srt")

        logo_args: list[str] = []
        logo_input = None
        if want_logo:
            # A small page sized to the watermark itself (unlike the outro card, which is a
            # full 1920x1080 frame with the logo centred in it): the image just fills it.
            logo_h = _even(_even(es.height) * 0.08)
            logo_w = _even(logo_h * 3)
            (work / "logo.html").write_text(watermark_logo_html(theme, logo_w, logo_h), encoding="utf-8")
            logo_code, _logo_out, logo_err = run_proc(
                [tl.node or "node", str(tools.NODE_DIR / "card.mjs"), str(work / "logo.html"),
                 str(work / "logo.png"), str(logo_w), str(logo_h), "1000", "1000", "transparent"],
                env={"CHROME_PATH": tl.chrome or ""}, cwd=str(tools.NODE_DIR), timeout=180)
            if logo_code != 0 or not (work / "logo.png").is_file():
                r.diagnostics.append(Diagnostic("COMPOSE_BUMPER", f"Rendering the logo watermark failed (exit {logo_code}); composing without it.",
                                                topic=t.id, lang=lang, data={"stderr": logo_err[-500:]}))
            else:
                logo_args = ["-i", str(work / "logo.png")]
                logo_input = "2:v"

        share = 0.85 if bumpers else 1.0
        body = work / "body.mp4"
        graph = filter_graph(c, es, duration, "subs.srt" if burn_in else None, sub_font, wm_font, theme.fonts_dir,
                             theme.safe_right, logo_input)
        tp.update(1, "encoding")
        ffmpeg(tl, ["-i", str(t.video), "-f", "concat", "-safe", "0", "-i", "slides.ffconcat", *logo_args,
                    "-filter_complex", graph, "-map", "[v]", "-map", "0:a?", "-t", f"{duration:.3f}",
                    *_encode_args(es), str(body)],
               duration=duration, on_pct=lambda pct: tp.update(pct * share, "encoding"), cwd=str(work))

        body_offset = 0.0
        if bumpers:
            tp.update(86, "bumpers")
            body_lufs = loudness(tl, t.video)
            segs = []
            W, H, fps = _even(es.width), _even(es.height), es.fps
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
                            "-t", f"{bi.duration:.3f}", *_encode_args(es), str(seg)], duration=bi.duration)
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
        if sidecar:
            with fsutil.atomic_path(sidecar) as tmp:
                Path(tmp).write_text(_shift_srt(subs, body_offset), encoding="utf-8")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    r.artifacts.append(Envelope.artifact_for(t.root, out, "draft" if draft else "composed"))
    if sidecar:
        r.artifacts.append(Envelope.artifact_for(t.root, sidecar, "composed_subtitles"))
    r.extra.update({"mode": "draft" if draft else "full", "subtitles": "burned" if burn_in else "sidecar",
                    "layout": c["layout"], "resolution": [_even(es.width), _even(es.height)],
                    "body_offset": round(body_offset, 3), "bumpers": sorted(bumpers), "duration": round(duration, 3)})
    tp.update(100, "done")


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    def fn(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config) -> None:
        compose_topic(t, r, tp, cfg, args)

    run_topics(env, target, "compose", args.lang, fn, jobs=args.jobs)
