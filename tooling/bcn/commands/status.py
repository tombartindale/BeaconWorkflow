"""bcn status: the current state of every topic beneath the path.

Reads the filesystem only and never recomputes anything. Fast enough to poll on
every UI refresh. --verify additionally opens each local artefact to confirm it
is what it claims; it never opens a cloud-only file.

Unlike the pipeline steps, status writes nothing: a UI polls it and watches the
tree, and a poll that wrote files would trigger its own refresh.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

from .. import fsutil, srt, tools
from ..config import Theme, load_theme, resolve_theme_name
from ..envelope import Diagnostic, Envelope, Fail, TopicResult, sha256_file
from ..markdown import parse
from ..quiz import parse_quiz
from ..media import probe
from ..state import STAGES, TopicState, all_topics, module_context
from ..tree import Target, Topic
from .qti import package_path
from .validate import module_documents

HELP = "current state of every topic beneath the path"


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--verify", action="store_true", help="open each local artefact and check it is what it claims")


def verify_topic(t: Topic, data: dict[str, Any], r: TopicResult, theme: Theme | None, tl: tools.Tools | None) -> None:
    def bad(code: str, rel: str, msg: str) -> None:
        r.diagnostics.append(Diagnostic(code, msg, topic=t.id, file=rel))

    for a in data["artifacts"]:
        if not a["exists"] or a.get("hydration") == "cloud" or a["kind"] == "slides":
            continue
        p = t.root / a["path"]
        rel = str(p.relative_to(t.dir))
        ok = True
        try:
            if p.stat().st_size == 0:
                bad("FS_EMPTY", rel, f"{rel} is empty.")
                ok = False
            else:
                with open(p, "rb") as f:
                    f.read(1)
                if p.name in ("topic.md", "topic.zh.md"):
                    parsed = parse(p, rel, t.id)
                    fm = [d for d in parsed.diagnostics if d.code.startswith("MD_FRONT_MATTER")]
                    if fm or len(parsed.slides) < 2:
                        bad("FS_CORRUPT", rel, f"{rel}: front matter does not parse or there is no slide break.")
                        ok = False
                elif p.suffix == ".srt":
                    if not srt.verify_head(p):
                        bad("FS_CORRUPT", rel, f"{rel}: the first cue does not parse.")
                        ok = False
                elif p.suffix == ".mp4":
                    if not fsutil.mp4_has_ftyp(p):
                        bad("FS_CORRUPT", rel, f"{rel} has no ftyp box; it is not an MP4.")
                        ok = False
                    elif tl and probe(tl, p, rel).duration <= 0:
                        bad("FS_CORRUPT", rel, f"{rel}: ffprobe reports no duration.")
                        ok = False
                elif p.suffix == ".json":
                    if fsutil.read_json(p) is None:
                        bad("FS_CORRUPT", rel, f"{rel} is not valid JSON.")
                        ok = False
        except Fail as f:
            bad("FS_CORRUPT", rel, f.diagnostic.message)
            ok = False
        except OSError as e:
            bad("FS_CORRUPT", rel, f"{rel} cannot be read: {e}")
            ok = False
        a["verified"] = ok

    for lang in ("en", "zh"):
        pngs = t.slide_pngs(lang)
        slide_row = next(a for a in data["artifacts"] if a["key"] == f"build/slides/{lang}")
        slide_ok = True
        for png in pngs:
            if fsutil.hydration(png) == "cloud":
                continue
            rel = str(png.relative_to(t.dir))
            dims = fsutil.png_size(png)
            if dims is None:
                bad("FS_CORRUPT", rel, f"{rel} is not a PNG.")
                slide_ok = False
            elif theme and dims != (theme.width, theme.height) and not theme.image_scale:
                bad("FS_CORRUPT", rel, f"{rel} is {dims[0]}x{dims[1]}; the theme declares {theme.width}x{theme.height}.")
                slide_ok = False
        if pngs:
            slide_row["verified"] = slide_ok

        mname = "manifest.json" if lang == "en" else f"manifest.{lang}.json"
        m = fsutil.read_json(t.out / mname) if (t.out / mname).is_file() else None
        if m:
            for f in m.get("files", []):
                fp = t.out / f["name"]
                rel = f"out/{f['name']}"
                if not fp.is_file():
                    bad("FS_CHECKSUM_MISMATCH", rel, f"{rel} is listed in {mname} but missing.")
                elif fsutil.hydration(fp) != "cloud" and sha256_file(fp) != f.get("sha256"):
                    bad("FS_CHECKSUM_MISMATCH", rel, f"{rel} does not match its checksum in {mname}.")


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    mods: dict[str, Any] = {}
    tot = Counter()
    for d in results:
        m = mods.setdefault(d["module"], {
            "topics": 0, "units": [], "en": {s: 0 for s in STAGES["en"]}, "zh": {s: 0 for s in STAGES["zh"]},
            "complete": {"en": 0, "zh": 0}, "blocked": 0, "stale": 0, "cloud": 0,
            "diagnostics": {"error": 0, "warn": 0, "info": 0}, "unreviewed": 0,
        })
        m["topics"] += 1
        if d["unit"] not in m["units"]:
            m["units"].append(d["unit"])
        for lang in ("en", "zh"):
            s = d[lang]
            m[lang][s["stage"]] += 1
            if s["complete"]:
                m["complete"][lang] += 1
                tot[f"complete_{lang}"] += 1
            for k, v in s["diagnostics"].items():
                m["diagnostics"][k] += v
                tot[f"diag_{k}"] += v
        blocked = d["en"]["blocked"] or d["zh"]["blocked"]
        stale = d["en"]["stale"] or d["zh"]["stale"]
        m["blocked"] += blocked
        m["stale"] += stale
        m["cloud"] += d["hydration"] in ("cloud", "partial")
        m["unreviewed"] += d["unreviewed_mistranscriptions"]
        tot["blocked"] += blocked
        tot["stale"] += stale
        tot["cloud"] += d["hydration"] in ("cloud", "partial")
        tot["unreviewed"] += d["unreviewed_mistranscriptions"]
    for m in mods.values():
        m["units"].sort()
    n = len(results)
    return {
        "topics": n,
        "complete": {"en": tot["complete_en"], "zh": tot["complete_zh"]},
        "blocked": tot["blocked"],
        "stale": tot["stale"],
        "cloud": tot["cloud"],
        "cloud_share": round(tot["cloud"] / n, 3) if n else 0.0,
        "unreviewed": tot["unreviewed"],
        "diagnostics": {"error": tot["diag_error"], "warn": tot["diag_warn"], "info": tot["diag_info"]},
        "modules": mods,
    }


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    env.diagnostics.extend(target.diagnostics)
    topics = all_topics(target.root, target.modules, target.topics, target.level, target.rel)
    tl = None
    if args.verify:
        try:
            tl = tools.require(module_context(target.root, target.modules[0]).cfg, "ffprobe") if target.modules else None
        except Fail as f:
            env.diagnostics.append(f.diagnostic)
    rows = []
    for t in topics:
        ctx = module_context(t.root, t.module)
        r = TopicResult(t.id, t.rel)
        data = TopicState(t, ctx).compute()
        if args.verify and t.dir.is_dir():
            try:
                theme = load_theme(t.root, resolve_theme_name(ctx.cfg, args.theme))
            except Fail:
                theme = None
            verify_topic(t, data, r, theme, tl)
        r.ok = not any(d.level == "error" for d in r.diagnostics)
        data["ok"] = r.ok
        for k, v in data.items():
            if k not in ("topic", "ok", "skipped"):
                r.extra[k] = v
        env.results.append(r)
        rows.append(data)
    # Module document problems (course map and so on) from the last module-level validate.
    module_docs = {}
    for m in target.modules:
        ctx = module_context(target.root, m)
        # Module documents are parsed afresh (they are small), so their problems show without a validate run.
        mdir = target.root / m
        diags = module_documents(Target(target.root, mdir, "module", m, [m], [], []))
        names = ["course-map.md", "assets.md", "reading-list.md"] + sorted(p.name for p in mdir.glob("assignment-*.md"))
        units = sorted(set(ctx.course_map.units) | {p.parent.name for p in mdir.glob("U*/activity.md")})
        documents = []
        for rel in names + [f"{u}/activity.md" for u in units]:
            mine = [d for d in diags if d.file == f"{m}/{rel}"]
            documents.append({"path": f"{m}/{rel}", "exists": (mdir / rel).is_file(),
                              "errors": sum(1 for d in mine if d.level == "error"),
                              "warnings": sum(1 for d in mine if d.level == "warn"),
                              "quiz": _quiz(target.root, m, rel),
                              "pdf": _coursemap_pdf(target.root, m, rel)})
        module_docs[m] = {
            "course_map": ctx.course_map.path.is_file(),
            "documents": documents,
            "errors": sum(1 for d in diags if d.level == "error"),
            "title": _module_title(ctx.course_map.path),
            "unit_titles": dict(ctx.course_map.units),
        }
    env.extra["summary"] = summarise(rows)
    for m, info in module_docs.items():
        if m in env.extra["summary"]["modules"]:
            env.extra["summary"]["modules"][m].update(info)
    env.extra["verified"] = bool(args.verify)


def _quiz(root: Path, module: str, rel: str) -> dict[str, Any] | None:
    """For a unit activity that is a quiz: its question count and its QTI package (bcn qti), if made."""
    src = root / module / rel
    if not rel.endswith("/activity.md") or not src.is_file():
        return None
    quiz = parse_quiz(src, f"{module}/{rel}")
    if quiz.front.get("type") != "quiz":
        return None
    pkg = package_path(root, module, rel.split("/")[0])
    exists = pkg.is_file()
    return {"questions": len(quiz.questions), "package": str(pkg.relative_to(root)), "exists": exists,
            "stale": (not fsutil.is_fresh([pkg], [src])) if exists else None}


def _coursemap_pdf(root: Path, module: str, rel: str) -> dict[str, Any] | None:
    """For course-map.md: its printed PDF (bcn coursemap), if made."""
    if rel != "course-map.md":
        return None
    src = root / module / rel
    out = root / module / "build" / "course-map.pdf"
    exists = out.is_file()
    return {"path": str(out.relative_to(root)), "exists": exists,
            "stale": (not fsutil.is_fresh([out], [src])) if exists else None}


def _module_title(p: Path) -> str | None:
    try:
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                title = title[len(p.parent.name):].strip(" :-—–") if title.startswith(p.parent.name) else title
                # "KV7016 — Course map" names the document, not the module.
                return None if title.lower() in ("course map", "course-map", "") else title
    except OSError:
        return None
    return None
