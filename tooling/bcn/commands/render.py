"""bcn render: topic markdown to a numbered PNG sequence and a PDF, via pinned Marp."""

from __future__ import annotations

import argparse
import json
import re
import shutil

from .. import fsutil, tools
from ..config import Config, Theme, load_theme, resolve_theme_name
from ..envelope import Diagnostic, Envelope, Fail, TopicResult
from ..markdown import IMAGE_RE, ParsedTopic, parse
from ..progress import TopicProgress, run as run_proc
from ..runner import require_input, require_step, run_topics, try_skip
from ..tree import Target, Topic

HELP = "markdown to slide images and PDF"


def add_args(p: argparse.ArgumentParser) -> None:
    pass


def _yaml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def marp_source(p: ParsedTopic, theme: Theme, lang: str) -> str:
    """Our front matter out, Marp's in, narration stripped, asset paths rebased.

    Marp directives never go in files humans edit; they are generated here.
    """
    fonts = ", ".join(f'"{f}"' if " " in f and f not in ("sans-serif", "serif") else f for f in theme.fonts[lang])
    size = theme.content_font_size.get(lang)
    faces = "".join(
        f"@font-face {{ font-family: \"{f['family']}\"; src: url(\"{(theme.dir / f['file']).as_uri()}\"); font-weight: {f.get('weight', 400)}; }} "
        for f in theme.font_faces or [])
    # The width of the strip reserved on the right, not where it starts: safe_area.right in
    # theme.toml is "keep clear from this fraction of the width onward", e.g. 0.5 reserves
    # the right half, so the padding is the remaining fraction of the frame.
    safe_right_px = round(theme.width * (1 - theme.safe_right))
    style = faces + f"section {{ font-family: {fonts}; --bcn-safe-bottom: {theme.safe_bottom}px; --bcn-safe-right: {safe_right_px}px;"
    if size:
        style += f" --bcn-font-size: {size}px;"
    style += " }"
    style += f" section h1, section h2, section h3 {{ font-weight: {theme.title_weight}; }}"
    if lang == "zh":
        style += " section { word-break: normal; line-break: strict; }"
    body = p.stripped_body()
    # The temp file lives in build/.render-<lang>/, two levels below the topic.
    body = IMAGE_RE.sub(lambda m: m.group(0).replace(f"({m.group(2)}", f"(../../{m.group(2)}", 1)
                        if m.group(2).startswith("assets/") else m.group(0), body)
    fm = [
        "---",
        "marp: true",
        f"theme: {theme.name}",
        f"lang: {'zh-Hans' if lang == 'zh' else 'en'}",
        f"title: {_yaml_str(p.front.get('title', ''))}",
        "style: " + _yaml_str(style),
        "---",
        "",
    ]
    return "\n".join(fm) + body


def render_topic(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config, lang: str, theme_flag: str | None,
                 force: bool) -> None:
    src = t.src(lang)
    require_input(t, src, lang)
    inputs = [src] + ([t.src("en")] if lang != "en" else [])
    require_step(t, "validate", lang, inputs, f"bcn validate{' --lang ' + lang if lang != 'en' else ''}")
    theme = load_theme(t.root, resolve_theme_name(cfg, theme_flag))
    p = parse(src, src.name, t.id)
    n = len(p.slides)
    assets = [t.dir / im.path for s in p.slides for im in s.images]
    outputs = [t.slides_dir(lang) / t.slide_name(i, lang) for i in range(1, n + 1)] + [t.deck_pdf(lang)]
    step_inputs = inputs + assets + theme.files()
    if try_skip(t, r, "render", lang, outputs, step_inputs, force):
        return
    tl = tools.require(cfg, "marp", "chrome")
    for a in assets:
        require_input(t, a, lang)

    work = t.build / f".render-{lang}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    try:
        deck_md = work / "deck.md"
        deck_md.write_text(marp_source(p, theme, lang), encoding="utf-8")
        env = {"CHROME_PATH": tl.chrome or ""}
        base = [tl.node or "node", tl.marp or "", "--no-config-file", "--theme-set", str(theme.dir), "--allow-local-files"]
        scale = theme.image_scale
        seen = [0]

        def on_png(line: str) -> None:
            if re.search(r"=> .*\.\d{3}\.png", line):
                seen[0] += 1
                tp.update(60.0 * seen[0] / max(n, 1), f"slide {seen[0]}/{n}")

        tp.update(1, "rendering slides")
        cmd = base + ["--images", "png"] + (["--image-scale", str(scale)] if scale else []) + ["-o", str(work / "deck.png"), str(deck_md)]
        code, out, err = run_proc(cmd, env=env, cwd=str(t.dir), on_stdout_line=on_png,
                                     on_stderr_line=on_png, timeout=600)
        if code != 0:
            raise Fail("RENDER_FAILED", f"Marp PNG export failed (exit {code}).", file=src.name, data={"stderr": err[-2000:]},
                       hint="See data.stderr. Check the pinned Chrome runs and the theme CSS is valid.")
        tp.update(60, "rendering PDF")
        code, out, err = run_proc(base + ["--pdf", "-o", str(work / "deck.pdf"), str(deck_md)], env=env, cwd=str(t.dir), timeout=600)
        if code != 0 or not (work / "deck.pdf").is_file():
            raise Fail("RENDER_FAILED", f"Marp PDF export failed (exit {code}).", file=src.name, data={"stderr": err[-2000:]})

        pngs = sorted(work.glob("deck.*.png"))
        numbers = [int(m.group(1)) for x in pngs if (m := re.match(r"deck\.(\d{3})\.png$", x.name))]
        if numbers != list(range(1, len(numbers) + 1)) or len(numbers) != n:
            raise Fail("RENDER_SLIDE_COUNT", f"Marp produced {len(numbers)} slide images {numbers[:3]}…; the source has {n} slides.",
                       file=src.name, hint="A stray --- or an empty slide usually causes this.")
        if lang != "en":
            en_n = len(parse(t.src("en"), "topic.md", t.id).slides)
            if n != en_n:
                raise Fail("RENDER_SLIDE_COUNT", f"Mandarin render has {n} slides; English has {en_n}.", file=src.name)
        for i, png in enumerate(pngs, 1):
            dims = fsutil.png_size(png)
            want = (int(theme.width * (scale or 1)), int(theme.height * (scale or 1)))
            if dims != want:
                raise Fail("RENDER_RESOLUTION", f"Slide {i} rendered at {dims}; the theme declares {want}.", slide=i,
                           file=f"themes/{theme.name}/theme.toml")

        tp.update(80, "measuring overflow")
        code, out, err = run_proc([tl.node or "node", str(tools.NODE_DIR / "measure.mjs"), str(deck_md), str(theme.css),
                                   str(theme.width), str(theme.height), str(theme.safe_bottom), str(work / "deck.html")],
                                  env=env, cwd=str(tools.NODE_DIR), timeout=300)
        if code != 0:
            raise Fail("RENDER_FAILED", f"Overflow measurement failed (exit {code}).", data={"stderr": err[-2000:]})
        measured = json.loads(out.strip().splitlines()[-1])
        level = "error" if lang != "en" else "warn"  # advisory for English, blocking for Mandarin
        flagged: list[int] = []
        for issue in measured["issues"]:
            s = issue["slide"]
            line = p.slides[s - 1].start_line if s <= n else None
            what = f"<{issue['element']}> \"{issue['text']}\""
            if issue["kind"] == "safe_area":
                msg = f"Slide {s}: {what} reaches y={issue['bottom']}px, inside the subtitle safe area (starts at {issue['limit']}px)."
                code_ = "RENDER_SAFE_AREA"
            elif "right" in issue:
                msg = f"Slide {s}: {what} overflows the content box to the right ({issue['right']}px > {issue['limit']}px)."
                code_ = "RENDER_OVERFLOW"
            else:
                msg = f"Slide {s}: {what} overflows the content box (bottom {issue['bottom']}px > {issue['limit']}px)."
                code_ = "RENDER_OVERFLOW"
            r.diagnostics.append(Diagnostic(code_, msg, level=level, topic=t.id, lang=lang, file=src.name, line=line,
                                            slide=s, hint="Shorten the text, split the slide in both languages, or reduce the image.",
                                            data=issue))
            flagged.append(s)

        # Place outputs: every slide atomically, then remove any leftovers beyond the new count.
        dest = t.slides_dir(lang)
        dest.mkdir(parents=True, exist_ok=True)
        for i, png in enumerate(pngs, 1):
            with fsutil.atomic_path(dest / t.slide_name(i, lang)) as tmp:
                shutil.copyfile(png, tmp)
        keep = {t.slide_name(i, lang) for i in range(1, n + 1)}
        for old in t.slide_pngs(lang):
            if old.name not in keep:
                old.unlink()
        with fsutil.atomic_path(t.deck_pdf(lang)) as tmp:
            shutil.copyfile(work / "deck.pdf", tmp)
        with fsutil.atomic_path(t.render_html(lang)) as tmp:
            shutil.copyfile(work / "deck.html", tmp)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    for i in range(1, n + 1):
        r.artifacts.append(Envelope.artifact_for(t.root, dest / t.slide_name(i, lang), "slide"))
    r.artifacts.append(Envelope.artifact_for(t.root, t.deck_pdf(lang), "deck"))
    r.extra.update({
        "slides": n,
        "theme": theme.name,
        "marp_cli": tools.MARP_CLI_VERSION,
        "chrome": tl.versions.get("chrome") if tl.versions else None,
        "overflow_slides": sorted(set(flagged)),
        "resolution": [theme.width, theme.height],
        "safe_bottom": theme.safe_bottom,
    })
    tp.update(100, "done")


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    def fn(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config) -> None:
        render_topic(t, r, tp, cfg, args.lang, args.theme, args.force)

    run_topics(env, target, "render", args.lang, fn, jobs=args.jobs)
