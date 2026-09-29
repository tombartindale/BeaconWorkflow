"""bcn translation: the batch handover to translation and back.

--export  For every topic in scope (all or nothing), put the English SRT (reviewed corrections
          applied) and the narration-stripped slide markdown in a batch folder
          and zip under translation/exports/, with a manifest, and record the
          export in each topic's translation.json.
--import  Take returned <topic_id>.zh.md and <topic_id>.zh.srt files from a folder
          or zip, place them as topic.zh.md and edit/master.zh.srt, then run
          validate --lang zh and subtitles --lang zh across the batch and report
          every failure together, so they go back to the translator in one message.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from .. import fsutil
from ..envelope import Diagnostic, Envelope, Fail, TopicResult, sha256_file, utcnow
from ..markdown import parse
from ..runner import run_topics
from ..state import TopicState, module_context
from ..tree import Target, Topic, is_noise, topic_from_id
from . import subtitles as subtitles_cmd
from . import validate as validate_cmd

HELP = "export topics for translation, or import what comes back"


def add_args(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", action="store_true", help="export the topics beneath the path")
    g.add_argument("--import", dest="source", metavar="DIR_OR_ZIP", help="import returned files")


def _export(env: Envelope, target: Target) -> None:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    scope = target.rel.replace("/", "-") if target.rel != "." else "programme"
    batch = f"{stamp}-{scope}"
    exports = target.root / "translation" / "exports"
    n = 2
    while (exports / batch).exists() or (exports / f"{batch}.zip").exists():
        batch = f"{stamp}-{scope}-{n}"
        n += 1
    out_dir = exports / batch
    ready: list[tuple[Topic, TopicResult]] = []
    for t in target.topics:
        r = TopicResult(t.id, t.rel)
        env.results.append(r)
        st = TopicState(t, module_context(t.root, t.module)).compute()
        en = st["en"]
        reasons = []
        if not en["steps"].get("validate", {}).get("ok") or not en["steps"].get("validate", {}).get("fresh"):
            reasons.append("the English has not passed validate")
        subs = en["steps"].get("subtitles", {})
        if not (subs.get("ok") and subs.get("fresh")) or not t.subtitle_out("en", "srt").is_file():
            reasons.append("the English SRT has not been through bcn subtitles")
        if st["unreviewed_mistranscriptions"]:
            reasons.append(f"{st['unreviewed_mistranscriptions']} suspected mis-transcriptions are unreviewed; the translator would inherit them")
        if reasons:
            r.ok = False
            r.diagnostics.append(Diagnostic("XL_NOT_READY", f"{t.id} is not ready to export: {'; '.join(reasons)}.", topic=t.id, lang="zh"))
            continue
        ready.append((t, r))
    if not ready or len(ready) < len(env.results):
        # A batch is a handover: all of the scope goes, or none of it does.
        if ready:
            env.diagnostics.append(Diagnostic("XL_NOT_READY", f"Nothing was exported: {len(env.results) - len(ready)} of {len(env.results)} topics are not ready.",
                                              hint="Fix them, or export a narrower scope."))
        return
    work = Path(tempfile.mkdtemp(prefix=".export-", dir=target.root))
    try:
        files = []
        for t, _r in ready:
            p = parse(t.src("en"), "topic.md", t.id)
            md = work / f"{t.id}.md"
            md.write_text(p.stripped_file(), encoding="utf-8")
            srt = work / f"{t.id}.en.srt"
            shutil.copyfile(t.subtitle_out("en", "srt"), srt)
            for f in (md, srt):
                files.append({"topic": t.id, "name": f.name, "bytes": f.stat().st_size, "sha256": sha256_file(f)})
        manifest = {
            "batch": batch, "created": utcnow(), "topics": [t.id for t, _ in ready], "files": files,
            "return": "For each topic return <topic_id>.zh.md (same slides, same breaks, lang: zh, no Say blocks) "
                      "and <topic_id>.zh.srt (same cue count, identical timings, translated text).",
        }
        fsutil.write_json(work / "manifest.json", manifest)
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(work), out_dir)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    zpath = out_dir.with_suffix(".zip")
    with fsutil.atomic_path(zpath) as tmp:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(out_dir.iterdir()):
                z.write(f, f"{batch}/{f.name}")
    for t, r in ready:
        rec = fsutil.read_json(t.translation_file) or {}
        rec.setdefault("exports", []).append({
            "batch": batch, "at": manifest["created"],
            "files": [f for f in files if f["topic"] == t.id],
        })
        fsutil.write_json(t.translation_file, rec)
        r.extra["batch"] = batch
        r.diagnostics.append(Diagnostic("XL_EXPORTED", f"{t.id} exported in batch {batch}.", topic=t.id, lang="zh"))
    env.artifacts.append(env.artifact(zpath, "translation-export"))
    env.extra["batch"] = batch
    env.extra["folder"] = env.rel(out_dir)
    env.extra["zip"] = env.rel(zpath)


def _import(env: Envelope, target: Target, source: str, args: argparse.Namespace) -> None:
    src = Path(source).expanduser()
    if not src.exists():
        raise Fail("FS_MISSING", f"{source} does not exist.")
    tmp = None
    try:
        if src.is_file() and src.suffix.lower() == ".zip":
            tmp = Path(tempfile.mkdtemp(prefix=".import-", dir=target.root))
            with zipfile.ZipFile(src) as z:
                for member in z.namelist():
                    name = Path(member).name
                    if name and not member.endswith("/") and not is_noise(name) and "__MACOSX" not in member:
                        (tmp / name).write_bytes(z.read(member))
            base = tmp
        elif src.is_dir():
            base = src
        else:
            raise Fail("USAGE", f"{source} must be a folder or a .zip.")
        found: dict[str, dict[str, Path]] = {}
        for f in sorted(base.rglob("*")):
            if not f.is_file() or is_noise(f.name) or "__MACOSX" in f.parts:
                continue
            m = re.match(r"^([A-Z]{2}\d{4}-U\d{2}-T\d{2})\.zh\.(md|srt)$", f.name)
            if not m:
                if f.name != "manifest.json":
                    env.diagnostics.append(Diagnostic("XL_UNKNOWN_FILE", f"'{f.name}' is not a <topic_id>.zh.md or <topic_id>.zh.srt file; ignored.",
                                                      file=f.name))
                continue
            found.setdefault(m.group(1), {})[m.group(2)] = f
        in_scope = {t.id for t in target.topics}
        placed: list[Topic] = []
        results: dict[str, TopicResult] = {}
        for tid, kinds in sorted(found.items()):
            t = topic_from_id(target.root, tid)
            r = TopicResult(tid, t.rel)
            results[tid] = r
            env.results.append(r)
            if not t.dir.is_dir() or (target.level != "root" and tid not in in_scope):
                r.ok = False
                r.diagnostics.append(Diagnostic("XL_UNKNOWN_FILE", f"{tid} is not a topic under {target.rel}; its files were not placed.", topic=tid, lang="zh"))
                continue
            actions = {}
            for kind, f in kinds.items():
                dest = t.src("zh") if kind == "md" else t.srt("zh")
                data = f.read_bytes()
                if dest.is_file() and dest.read_bytes() == data:
                    actions[kind] = "unchanged"
                    continue
                actions[kind] = "replaced" if dest.exists() else "placed"
                fsutil.write_bytes(dest, data)
                r.diagnostics.append(Diagnostic("XL_IMPORTED", f"{actions[kind].capitalize()} {dest.relative_to(t.root)}.", topic=tid, lang="zh",
                                                file=str(dest.relative_to(t.dir))))
            missing = {"md", "srt"} - set(kinds)
            if missing:
                r.diagnostics.append(Diagnostic("FS_MISSING", f"The return for {tid} has no {' or '.join('.zh.' + k for k in sorted(missing))} file.",
                                                topic=tid, lang="zh"))
            r.extra["files"] = actions
            placed.append(t)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    if not placed:
        return
    # Check the whole batch at once: parity first, then SRT timings.
    sub = Target(target.root, target.path, target.level, target.rel, target.modules, placed, [])
    for step, fn in (
        ("validate", lambda t, r, tp, cfg: validate_cmd.check_topic(t, r, tp, cfg, "zh")),
        ("subtitles", lambda t, r, tp, cfg: subtitles_cmd.subtitles_topic(t, r, tp, cfg, "zh", True, stable=False)),
    ):
        step_env = Envelope(step, target.rel, target.root)
        run_topics(step_env, sub, step, "zh", fn)
        for sr in step_env.results:
            r = results[sr.topic]
            r.extra[f"{step}_ok"] = sr.ok
            r.diagnostics.extend(d for d in sr.diagnostics if d.level != "info")
    for r in results.values():
        r.ok = not any(d.level == "error" for d in r.diagnostics)
    env.extra["return_to_translator"] = [
        {"topic": d.topic, "code": d.code, "message": d.message}
        for r in results.values() for d in r.diagnostics
        if d.level == "error" and d.code in ("MD_PARITY_COUNT", "MD_PARITY_BREAK", "MD_SAY_IN_ZH", "SRT_ZH_CUE_COUNT", "SRT_ZH_TIMING", "FS_MISSING")
    ]


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    if args.export:
        _export(env, target)
    else:
        _import(env, target, args.source, args)
