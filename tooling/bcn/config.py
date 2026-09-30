"""programme.toml, module.toml and theme.toml.

Every value that the spec marks as unconfirmed (delivery spec, composite layout,
match thresholds) has a placeholder default here and is overridable in
programme.toml. Nothing about the look lives here: that belongs to the theme.
"""

from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .envelope import Fail

PACKAGE_DIR = Path(__file__).resolve().parent
TOOLING_DIR = PACKAGE_DIR.parent
BUNDLED_THEMES = TOOLING_DIR / "themes"

DEFAULTS: dict[str, Any] = {
    "programme": {"name": "", "theme": "default"},
    "validate": {
        "words_per_minute": 145,
        "word_tolerance": 0.15,
        "slides_min": 6,
        "slides_max": 16,
        "narration_min_words": 30,
        "narration_max_words": 200,
        "title_max_chars": 60,
        # Weekday names ("Friday") as dates. Off by default: they are usually examples, not schedules.
        "date_weekdays": False,
        # Per-check level: {"MD_DATE" = "warn"} or "off". Warnings never block.
        "severity": {},
        "forbidden": ["semester", "deadline", "next week"],
        # Country, institution and agency names that tie content to one place, so it can be
        # flagged before reuse elsewhere. Empty by default: populate with the programme's own
        # university, funders, government bodies, and any countries named in examples.
        "localization": [],
        # Off by default: a heuristic (capitalised word sequences, honorifics), not NLP, so it
        # flags candidates for a human rather than confirmed hits. Turn on with [validate.severity]
        # left alone (error) or set to "warn" if the false-positive rate is too high to block on.
        "names": False,
        # Capitalised phrases the heuristic would otherwise flag: recurring product/brand names,
        # course titles, or anything else legitimately proper-cased in this programme.
        "names_allow": [],
        "deictic": [
            "here on the left",
            "here on the right",
            "as you can see",
            "this arrow",
            "on screen now",
            "on the screen",
            "on this slide",
            "in this diagram",
            "shown here",
            "look at this",
        ],
    },
    "validate_zh": {
        "slide_chars_max": 220,
        "forbidden": [],
        "localization": [],
    },
    "documents": {
        # Extra headings to insist on. Units, topics and outcomes are always checked.
        "course_map_headings": [],
        "activity_headings": [],
        "activity_types": ["quiz", "task"],
        "assignment_headings": ["Brief", "Outcomes", "Assessment criteria"],
    },
    "cues": {
        "min_confidence": 0.8,
        "max_divergence": 0.10,
        "window": 8,
        "srt_video_mtime_tolerance_s": 6 * 3600,
        "srt_tail_warn_s": 30.0,
        "mistranscription_max_tokens": 5,
        "mistranscription_similarity": 0.6,
    },
    "subtitles": {
        "format": "srt",  # "srt", "vtt" or "both": placeholder until the partner confirms
        "normalise": False,  # rewrap lines; never touches timings
        "max_line_chars": 42,
        "max_lines": 2,
        "max_cue_seconds": 7.0,
        "zh_max_line_chars": 16,
    },
    "delivery": {
        # Placeholder: the partner's delivery specification is unconfirmed.
        # What bcn compose encodes to in full (non-draft) mode: the delivered video is
        # always built to this spec from scratch, so there is no separate "transcode if
        # it doesn't match" step any more.
        "video_codec": "h264",
        "audio_codec": "aac",
        "width": 1920,
        "height": 1080,
        "fps": 25,
        "video_bitrate": "8M",
        "audio_bitrate": "192k",
    },
    "compose": {
        # Placeholder: the partner's composite layout is not agreed.
        "layout": "inset",  # "inset" or "side_by_side"
        "inset_scale": 0.3,
        "inset_position": "top-right",  # top-left, top-right, bottom-left, bottom-right; bottom sits over subtitles
        "inset_margin": 16,
        "subtitle_font_size": 16,  # libass units at the default 288-line script resolution; --burn-subtitles only
        # Full mode (the default) delivers, so it encodes at [delivery]'s own resolution/fps/codec/
        # bitrate and subtitles go out as a shifted sidecar file, never burned in. --draft is for
        # quickly checking cue timing only: it is never delivered, so it keeps its own small, fast,
        # watermarked settings below, independent of [delivery].
        "draft_width": 960,
        "draft_height": 540,
        "draft_fps": 25,
        "draft_watermark": "DRAFT",
        "draft_crf": 30,
        "draft_preset": "veryfast",
    },
    "bumpers": {"intro": "", "outro": ""},
    "sync": {
        # The shared folder (e.g. OneDrive/SharePoint) holding one folder per module.
        # Empty means sync is not set up for this programme root.
        "remote": "",
        # Where finished delivery packages go, inside each remote module folder.
        "delivery_dir": "delivery",
    },
    "qa": {
        "unit_minutes_min": 85,
        "unit_minutes_max": 110,
        "video_minutes_tolerance": 0.15,
    },
    "tools": {
        # Empty means "use the copy vendored under tooling/". Override only deliberately.
        "ffmpeg": "",
        "ffprobe": "",
        "node": "",
        "chrome": "",
    },
}

MODULE_OVERRIDABLE = {"theme", "bumpers"}


def _merge(base: dict[str, Any], over: dict[str, Any], where: str) -> dict[str, Any]:
    out = copy.deepcopy(base)
    if not base:
        return copy.deepcopy(over)  # an empty default table is free-form, e.g. [validate.severity]
    for k, v in over.items():
        if k not in out:
            raise Fail("CONFIG_INVALID", f"Unknown key '{k}' in {where}.", file=where,
                       hint="Check the spelling against the documented keys.")
        if isinstance(out[k], dict):
            if not isinstance(v, dict):
                raise Fail("CONFIG_INVALID", f"'{k}' in {where} must be a table.", file=where)
            out[k] = _merge(out[k], v, where)
        else:
            out[k] = v
    return out


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise Fail("CONFIG_INVALID", f"{path.name}: {e}", file=str(path)) from e


@dataclass
class Config:
    root: Path
    data: dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.data[key]


_cache: dict[tuple[Path, str | None], Config] = {}


def load(root: Path, module_dir: Path | None = None) -> Config:
    key = (root, str(module_dir) if module_dir else None)
    if key in _cache:
        return _cache[key]
    data = _merge(DEFAULTS, _load_toml(root / "programme.toml"), "programme.toml")
    if module_dir is not None and (module_dir / "module.toml").is_file():
        mod = _load_toml(module_dir / "module.toml")
        bad = set(mod) - MODULE_OVERRIDABLE
        if bad:
            raise Fail("CONFIG_INVALID", f"module.toml may only set theme and bumpers, not {', '.join(sorted(bad))}.",
                       file=str(module_dir / "module.toml"))
        if "theme" in mod:
            data["programme"]["theme"] = mod["theme"]
        if "bumpers" in mod:
            data["bumpers"] = _merge(data["bumpers"], mod["bumpers"], "module.toml")
    cfg = Config(root, data)
    _cache[key] = cfg
    return cfg


# -- themes ---------------------------------------------------------------------

@dataclass
class Theme:
    name: str
    dir: Path
    css: Path
    width: int
    height: int
    safe_bottom: int
    safe_right: float  # fraction of width kept clear on the right, for a p-in-p presenter; 1.0 = none
    fonts: dict[str, list[str]]
    content_font_size: dict[str, int]
    image_scale: float | None = None
    font_faces: list[dict] | None = None
    bumper: dict | None = None
    document: dict | None = None
    title_weight: int = 900
    subtitle_fonts: dict[str, str] | None = None

    @property
    def fonts_dir(self) -> Path:
        return self.dir / "fonts"

    def asset(self, rel: str) -> Path:
        """A file the theme names, relative to the theme directory (it may point outside it)."""
        return (self.dir / rel).resolve()

    def subtitle_font(self, lang: str) -> str:
        """The face for burned-in subtitles: [subtitle_font] if set, else the first of the stack."""
        return (self.subtitle_fonts or {}).get(lang) or self.fonts[lang][0]

    def bumper_media(self) -> list[Path]:
        """Files outside the stylesheet that bumpers depend on (logo, background video)."""
        b = self.bumper or {}
        return [self.asset(b[k]) for k in ("logo", "background_video") if b.get(k)]

    def document_media(self) -> list[Path]:
        """Files outside the stylesheet that printed documents (bcn coursemap) depend on."""
        d = self.document or {}
        return [self.asset(d["logo"])] if d.get("logo") else []

    def files(self) -> list[Path]:
        return sorted(p for p in self.dir.rglob("*") if p.is_file() and not p.name.startswith("."))


# The intro/outro title card (bcn bumpers), overridable under [bumper] in theme.toml.
BUMPER_DEFAULTS: dict[str, Any] = {
    "logo": "",               # PNG or SVG, relative to the theme directory; the whole outro card
    "logo_height": 320,       # pixels at the slide resolution
    "background_video": "",   # video behind the intro title, relative to the theme directory
    "background": "#f7f5f0",  # the outro, and the intro when there is no background video
    "color": "#12344d",       # the intro title
    "intro_seconds": 5.0,
    "outro_seconds": 5.0,
    "fade_seconds": 0.75,
}

# Printed documents on a white page (bcn coursemap), overridable under [document] in theme.toml.
# A separate logo from [bumper]: that one is usually a light mark for a dark video background.
DOCUMENT_DEFAULTS: dict[str, Any] = {
    "logo": "",        # PNG or SVG, relative to the theme directory; a dark mark for a white page
    "logo_height": 28,  # points, in the printed header
}


def resolve_theme_name(cfg: Config, flag: str | None) -> str:
    # --theme, then module.toml, then programme.toml, then "default". load() has
    # already layered module.toml over programme.toml.
    return flag or cfg["programme"].get("theme") or "default"


def load_theme(root: Path, name: str) -> Theme:
    for base in (root / "themes", BUNDLED_THEMES):
        d = base / name
        if (d / "theme.toml").is_file():
            break
    else:
        raise Fail("RENDER_THEME", f"Theme '{name}' not found in themes/ or the bundled themes.",
                   hint="Create themes/<name>/ with a CSS file and theme.toml, or fix the theme name.")
    t = _load_toml(d / "theme.toml")
    try:
        css = d / t["css"]
        slide = t["slide"]
        theme = Theme(
            name=name,
            dir=d,
            css=css,
            width=int(slide["width"]),
            height=int(slide["height"]),
            safe_bottom=int(t["safe_area"]["bottom"]),
            # A presenter inset over the right of the frame (compose's side_by_side layout)
            # needs the slide content kept out of that space too; 1.0 means no reservation.
            safe_right=float(t["safe_area"].get("right", 1.0)),
            fonts={k: list(v) for k, v in t["fonts"].items()},
            content_font_size={k: int(v) for k, v in t.get("font_size", {}).items()},
            image_scale=float(slide["image_scale"]) if "image_scale" in slide else None,
            font_faces=[dict(f) for f in t.get("font_face", [])],
            bumper={**BUMPER_DEFAULTS, **t.get("bumper", {})},
            document={**DOCUMENT_DEFAULTS, **t.get("document", {})},
            title_weight=int(t.get("title_weight", 900)),
            subtitle_fonts={k: str(v) for k, v in t.get("subtitle_font", {}).items()},
        )
        for k in ("intro_seconds", "outro_seconds", "fade_seconds", "logo_height"):
            theme.bumper[k] = float(theme.bumper[k])
        theme.document["logo_height"] = float(theme.document["logo_height"])
    except (KeyError, TypeError, ValueError) as e:
        raise Fail("RENDER_THEME", f"themes/{name}/theme.toml is missing or has a bad value: {e}",
                   file=str(d / "theme.toml")) from e
    if not css.is_file():
        raise Fail("RENDER_THEME", f"Theme CSS {css.name} not found.", file=str(css))
    head = css.read_text(encoding="utf-8")[:500]
    if f"@theme {name}" not in head:
        raise Fail("RENDER_THEME", f"{css.name} must declare /* @theme {name} */ so Marp registers it under that name.",
                   file=str(css))
    for face in theme.font_faces or []:
        if not (d / face.get("file", "")).is_file():
            raise Fail("RENDER_THEME", f"Font file {face.get('file')} declared in theme.toml does not exist.", file=str(d / "theme.toml"))
    for logo in (theme.bumper["logo"], theme.document["logo"]):
        if logo and Path(logo).suffix.lower() in (".eps", ".ai", ".ps", ".pdf"):
            raise Fail("RENDER_THEME", f"The logo {logo} is {Path(logo).suffix.upper()[1:]}, which a browser cannot draw.",
                       file=str(d / "theme.toml"), hint="Use a PNG or SVG export of the logo (brand kits usually include both).")
    for key in ("logo", "background_video"):
        rel = theme.bumper[key]
        if rel and not theme.asset(rel).is_file():
            raise Fail("RENDER_THEME", f"The bumper {key.replace('_', ' ')} {rel} named in theme.toml does not exist "
                       f"(looked for {theme.asset(rel)}).", file=str(d / "theme.toml"))
    if theme.document["logo"] and not theme.asset(theme.document["logo"]).is_file():
        raise Fail("RENDER_THEME", f"The document logo {theme.document['logo']} named in theme.toml does not exist "
                   f"(looked for {theme.asset(theme.document['logo'])}).", file=str(d / "theme.toml"))
    b = theme.bumper
    # The intro only fades out; the outro fades in and out, so its fades must not overlap.
    if b["intro_seconds"] < b["fade_seconds"] or b["outro_seconds"] < 2 * b["fade_seconds"]:
        raise Fail("RENDER_THEME", "The intro must last at least fade_seconds, and the outro at least twice fade_seconds, "
                   "so the fades do not overlap.",
                   file=str(d / "theme.toml"))
    for lang in ("en", "zh"):
        if lang not in theme.fonts:
            raise Fail("RENDER_THEME", f"theme.toml declares no font stack for '{lang}'.", file=str(d / "theme.toml"))
    return theme
