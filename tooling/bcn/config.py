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
        "forbidden": ["semester", "deadline", "next week"],
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
    },
    "documents": {
        "course_map_headings": ["Learning outcomes"],
        "activity_headings": ["Task", "Outcomes"],
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
        "transcode": False,
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
        "width": 960,
        "height": 540,
        "fps": 25,
        "inset_scale": 0.3,
        "inset_position": "top-right",  # top-left, top-right, bottom-left, bottom-right; bottom sits over subtitles
        "inset_margin": 16,
        "watermark": "DRAFT",
        "subtitle_font_size": 16,  # libass units at the default 288-line script resolution
        "crf": 30,
        "preset": "veryfast",
    },
    "bumpers": {"intro": "", "outro": ""},
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
    fonts: dict[str, list[str]]
    content_font_size: dict[str, int]
    image_scale: float | None = None
    font_faces: list[dict] | None = None

    @property
    def fonts_dir(self) -> Path:
        return self.dir / "fonts"

    def files(self) -> list[Path]:
        return sorted(p for p in self.dir.rglob("*") if p.is_file() and not p.name.startswith("."))


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
            fonts={k: list(v) for k, v in t["fonts"].items()},
            content_font_size={k: int(v) for k, v in t.get("font_size", {}).items()},
            image_scale=float(slide["image_scale"]) if "image_scale" in slide else None,
            font_faces=[dict(f) for f in t.get("font_face", [])],
        )
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
    for lang in ("en", "zh"):
        if lang not in theme.fonts:
            raise Fail("RENDER_THEME", f"theme.toml declares no font stack for '{lang}'.", file=str(d / "theme.toml"))
    return theme
