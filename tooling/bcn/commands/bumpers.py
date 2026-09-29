"""bcn bumpers: the intro and outro for a topic, each a still card with fades.

The intro is the module code above the topic's title (from the front matter of
topic.md or topic.zh.md), laid over the theme's background video (or on its background colour if it names
none). The outro is the logo the theme names, on its own. Cards are rendered to PNG
with the pinned Chrome; ffmpeg holds each for the durations in the theme's [bumper]
table with a silent stereo track so it joins cleanly to the edit. The intro starts
on its first frame and fades to black; the outro fades from and to black. Size and frame rate follow [delivery] in programme.toml,
so the bumpers match the master.

Outputs, in build/bumpers/: <id>.intro-card.<lang>.png, <id>.outro-card.<lang>.png,
<id>.intro.<lang>.mp4 and <id>.outro.<lang>.mp4. compose wraps the draft in them and
package delivers them beside the master. The master and the cue sheet are never changed.
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
from pathlib import Path

from .. import fsutil, tools
from ..config import Config, Theme, load_theme, resolve_theme_name
from ..envelope import Diagnostic, Envelope, Fail, TopicResult
from ..markdown import parse
from ..media import ffmpeg
from ..progress import TopicProgress, run as run_proc
from ..runner import require_input, run_topics, try_skip
from ..tree import Target, Topic

HELP = "intro and outro title-card videos"

# Title size range in pixels at the slide resolution: it steps down until the title fits.
TITLE_PX = {"en": (112, 56), "zh": (124, 64)}


def add_args(p: argparse.ArgumentParser) -> None:
    pass


def _font_stack(fonts: list[str]) -> str:
    return ", ".join(f'"{f}"' if f not in ("sans-serif", "serif") else f for f in fonts)


def font_faces_css(theme: Theme) -> str:
    return "".join(
        f"@font-face {{ font-family: \"{f['family']}\"; src: url(\"{(theme.dir / f['file']).as_uri()}\"); "
        f"font-weight: {f.get('weight', 400)}; font-style: {f.get('style', 'normal')}; }}\n"
        for f in theme.font_faces or [])


def _page(theme: Theme, lang: str, body: str, extra_css: str = "", transparent: bool = False) -> str:
    b = theme.bumper
    W, H = theme.width, theme.height
    background = "transparent" if transparent else b["background"]
    faces = font_faces_css(theme)
    return f"""<!doctype html><html lang="{'zh-Hans' if lang == 'zh' else 'en'}"><head><meta charset="utf-8"><style>
{faces}
html, body {{ margin: 0; width: {W}px; height: {H}px; overflow: hidden; background: {background}; }}
main {{ width: {W}px; height: {H}px; box-sizing: border-box; padding: {round(H * 0.1)}px {round(W * 0.1)}px;
        display: flex; flex-direction: column; align-items: center; justify-content: center; gap: {round(H * 0.05)}px; }}
{extra_css}
</style></head><body><main>{body}</main></body></html>
"""


def intro_card_html(theme: Theme, title: str, lang: str, over_video: bool = False, module: str = "") -> str:
    """The intro: the module code above the topic title. No logo; that is the outro.

    Over a background video the card is transparent and the title carries a soft
    shadow, so it stays legible whatever the video does behind it.
    """
    b = theme.bumper
    W, H = theme.width, theme.height
    zh = "word-break: normal; line-break: strict;" if lang == "zh" else ""
    shadow = "text-shadow: 0 2px 24px rgba(0, 0, 0, .55);" if over_video else ""
    css = f"""
#module {{ font-family: {_font_stack(theme.fonts['en'])}; font-weight: 500; font-size: {round(H * 0.04)}px;
           letter-spacing: 0.18em; color: {b['color']}; opacity: 0.85; {shadow} }}
#box {{ width: {round(W * 0.78)}px; max-height: {round(H * 0.5)}px; display: flex; justify-content: center; }}
#title {{ margin: 0; font-family: {_font_stack(theme.fonts[lang])}; font-weight: {theme.title_weight}; color: {b['color']};
          line-height: 1.2; text-align: center; text-wrap: balance; {zh} {shadow} }}"""
    code = f'<div id="module">{html.escape(module)}</div>' if module else ""
    return _page(theme, lang, f'{code}<div id="box"><h1 id="title">{html.escape(title)}</h1></div>', css,
                 transparent=over_video)


def outro_card_html(theme: Theme, lang: str) -> str:
    """The outro: the logo alone, centred. Blank background if the theme names no logo."""
    b = theme.bumper
    W = theme.width
    logo = f"<img id='logo' src='{theme.asset(b['logo']).as_uri()}' alt=''>" if b["logo"] else ""
    css = f"#logo {{ height: {b['logo_height']:.0f}px; max-width: {round(W * 0.6)}px; object-fit: contain; }}"
    return _page(theme, lang, logo, css)


def _video_args(d: dict, seconds: float, fade: float, background: str, still: str, video: Path | None = None,
                fade_in: bool = True) -> list[str]:
    W, H, fps = d["width"], d["height"], d["fps"]
    fades = (f"fade=t=in:st=0:d={fade:.3f}," if fade_in else "") + \
        f"fade=t=out:st={seconds - fade:.3f}:d={fade:.3f},format=yuv420p"
    if video:
        # The card (transparent PNG) over the background video, which is scaled to fill the
        # frame, loops if it is shorter than the bumper, and brings no sound of its own.
        inputs = ["-stream_loop", "-1", "-i", str(video), "-loop", "1", "-framerate", str(fps), "-i", still]
        graph = (f"[0:v]fps={fps},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[bg];"
                 f"[1:v]scale={W}:{H},format=rgba[card];[bg][card]overlay=0:0,{fades}[v]")
        audio_in = "2:a"
    else:
        inputs = ["-loop", "1", "-framerate", str(fps), "-i", still]
        graph = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={background},"
                 f"setsar=1,{fades}[v]")
        audio_in = "1:a"
    return [*inputs, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-filter_complex", graph, "-map", "[v]", "-map", audio_in, "-t", f"{seconds:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-tune", "stillimage", "-pix_fmt", "yuv420p",
            "-r", str(fps), "-c:a", "aac", "-b:a", d["audio_bitrate"], "-ar", "48000", "-ac", "2", "-movflags", "+faststart"]


def bumpers_topic(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config, lang: str, theme_flag: str | None,
                  force: bool) -> None:
    src = t.src(lang)
    require_input(t, src, lang)
    theme = load_theme(t.root, resolve_theme_name(cfg, theme_flag))
    intro_card, outro_card = t.bumper_card("intro", lang), t.bumper_card("outro", lang)
    intro, outro = t.bumper("intro", lang), t.bumper("outro", lang)
    outputs = [intro_card, outro_card, intro, outro]
    # The single shared card from before intro and outro had cards of their own.
    old_card = t.build / "bumpers" / f"{t.id}.card.{lang}.png"
    inputs = [src, t.root / "programme.toml", *theme.files(), *theme.bumper_media()]
    if not old_card.exists() and try_skip(t, r, "bumpers", lang, outputs, inputs, force):
        return
    title = str(parse(src, src.name, t.id).front.get("title", "")).strip()
    if not title:
        raise Fail("BUMPER_NO_TITLE", f"{src.name} has no title in its front matter.", file=src.name)
    if not theme.bumper["logo"]:
        r.diagnostics.append(Diagnostic("BUMPER_NO_LOGO", "The outro card is blank: the theme names no logo.", topic=t.id,
                                        lang=lang, file=f"themes/{theme.name}/theme.toml",
                                        hint="Put the logo in the theme directory and set logo under [bumper] in theme.toml."))
    tl = tools.require(cfg, "marp", "chrome", "ffmpeg")
    b, d = theme.bumper, cfg["delivery"]

    work = t.build / f".bumpers-{lang}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        video = theme.asset(b["background_video"]) if b["background_video"] else None
        (work / "intro-card.html").write_text(intro_card_html(theme, title, lang, over_video=bool(video), module=t.module), encoding="utf-8")
        (work / "outro-card.html").write_text(outro_card_html(theme, lang), encoding="utf-8")
        hi, lo = TITLE_PX[lang]
        fit: dict = {}
        for i, kind in enumerate(("intro", "outro")):
            tp.update(5 + 10 * i, f"rendering {kind} card")
            transparent = ["transparent"] if kind == "intro" and video else []
            code, out, err = run_proc([tl.node or "node", str(tools.NODE_DIR / "card.mjs"), str(work / f"{kind}-card.html"),
                                       str(work / f"{kind}-card.png"), str(theme.width), str(theme.height), str(hi), str(lo),
                                       *transparent],
                                      env={"CHROME_PATH": tl.chrome or ""}, cwd=str(tools.NODE_DIR), timeout=180)
            if code != 0 or not (work / f"{kind}-card.png").is_file():
                raise Fail("RENDER_FAILED", f"Rendering the {kind} card failed (exit {code}).", file=src.name,
                           data={"stderr": err[-2000:]})
            if kind == "intro":
                fit = json.loads(out.strip().splitlines()[-1])
        if not fit["fits"]:
            r.diagnostics.append(Diagnostic("BUMPER_TITLE_FIT", f"The title does not fit the card even at {fit['font_size']}px.",
                                            topic=t.id, lang=lang, file=src.name, hint="Shorten the title."))
        for i, (kind, seconds) in enumerate((("intro", b["intro_seconds"]), ("outro", b["outro_seconds"]))):
            tp.update(20 + 40 * i, f"encoding {kind}")
            ffmpeg(tl, [*_video_args(d, seconds, b["fade_seconds"], b["background"], f"{kind}-card.png",
                                     video if kind == "intro" else None, fade_in=kind != "intro"), f"{kind}.mp4"],
                   duration=seconds, on_pct=lambda pct, i=i, kind=kind: tp.update(20 + 40 * i + pct * 0.4, f"encoding {kind}"), cwd=str(work))
        for name, dest in (("intro-card.png", intro_card), ("outro-card.png", outro_card), ("intro.mp4", intro), ("outro.mp4", outro)):
            with fsutil.atomic_path(dest) as tmp:
                shutil.move(str(work / name), tmp)
        old_card.unlink(missing_ok=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    for f, kind in ((intro_card, "bumper_card"), (outro_card, "bumper_card"), (intro, "bumper"), (outro, "bumper")):
        r.artifacts.append(Envelope.artifact_for(t.root, f, kind))
    r.extra.update({"title": title, "logo": bool(b["logo"]), "background_video": bool(b["background_video"]),
                    "font_size": fit["font_size"],
                    "intro_seconds": b["intro_seconds"], "outro_seconds": b["outro_seconds"],
                    "resolution": [d["width"], d["height"]], "fps": d["fps"]})
    tp.update(100, "done")


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    def fn(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config) -> None:
        bumpers_topic(t, r, tp, cfg, args.lang, args.theme, args.force)

    run_topics(env, target, "bumpers", args.lang, fn, jobs=args.jobs)
