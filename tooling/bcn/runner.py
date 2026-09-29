"""Shared machinery for commands that operate topic by topic."""

from __future__ import annotations

import concurrent.futures as cf
import traceback
from pathlib import Path
from typing import Callable

from . import fsutil, progress
from .config import Config, load
from .envelope import Cancelled, Diagnostic, Envelope, Fail, TopicResult
from .progress import Progress, TopicProgress
from .tree import Target, Topic, scan_topic

TopicFn = Callable[[Topic, TopicResult, TopicProgress, Config], None]

# Which step produces an input, so a missing-input message can say what to run.
PRODUCER = {
    "topic.md": "write or paste the topic (bcn intake)",
    "topic.zh.md": "import the translation",
    "edit/master.mp4": "wait for the editor's delivery",
    "edit/master.srt": "wait for the editor's delivery",
    "edit/master.zh.srt": "import the translation",
}


def require_input(t: Topic, path: Path, lang: str | None = None, step_hint: str | None = None,
                  stable: bool = False) -> None:
    rel = str(path.relative_to(t.dir))
    if not path.exists():
        hint = step_hint or PRODUCER.get(rel)
        raise Fail("FS_MISSING", f"{rel} does not exist.", topic=t.id, lang=lang, file=rel,
                   hint=f"First: {hint}." if hint else None)
    if fsutil.hydration(path) == "cloud":
        raise Fail("FS_NOT_HYDRATED", f"{rel} is cloud-only; its contents are not on this machine.",
                   topic=t.id, lang=lang, file=rel,
                   hint=f"In Finder, right-click {t.rel}/{Path(rel).parent} and choose Always Keep on This Device, "
                        "or work from a local copy of the programme root.")
    if path.is_file() and path.stat().st_size == 0:
        raise Fail("FS_EMPTY", f"{rel} is empty.", topic=t.id, lang=lang, file=rel)
    if stable and fsutil.recently_modified(path, 2.0):
        raise Fail("FS_UNSTABLE", f"{rel} was modified in the last two seconds and may still be syncing.",
                   topic=t.id, lang=lang, file=rel, level="error", hint="Run again in a moment.")


def require_step(t: Topic, step: str, lang: str, inputs: list[Path], run_hint: str) -> dict:
    """The named step must have run, passed, and be newer than its inputs."""
    f = t.step_file(step, lang)
    env = fsutil.read_json(f)
    if env is None:
        raise Fail("STEP_PREREQUISITE", f"{step} has not been run{' for ' + lang if lang != 'en' else ''}.",
                   topic=t.id, lang=lang, hint=f"Run {run_hint} first.")
    if not env.get("ok"):
        raise Fail("STEP_PREREQUISITE", f"The last {step} run failed.", topic=t.id, lang=lang,
                   hint=f"Fix its diagnostics and run {run_hint} again.")
    if not fsutil.is_fresh([f], inputs):
        raise Fail("STEP_PREREQUISITE", f"{step} output is older than its inputs.", topic=t.id, lang=lang,
                   hint=f"Run {run_hint} again.")
    return env


def restore(r: TopicResult, prev: dict) -> None:
    r.skipped = True
    r.ok = bool(prev.get("ok"))
    for d in prev.get("diagnostics", []):
        try:
            r.diagnostics.append(Diagnostic(d["code"], d["message"], level=d.get("level"), topic=d.get("topic"),
                                            file=d.get("file"), line=d.get("line"), slide=d.get("slide"),
                                            hint=d.get("hint"), lang=d.get("lang"), data=d.get("data")))
        except (KeyError, ValueError):
            continue
    for k, v in (prev.get("results") or [{}])[0].items():
        if k not in ("topic", "ok", "skipped"):
            r.extra[k] = v


def try_skip(t: Topic, r: TopicResult, step: str, lang: str, outputs: list[Path], inputs: list[Path],
             force: bool) -> bool:
    """Resumability: skip a topic whose last run passed and whose outputs are newer than its inputs."""
    if force:
        return False
    f = t.step_file(step, lang)
    prev = fsutil.read_json(f)
    if not prev or not prev.get("ok"):
        return False
    if not fsutil.is_fresh(outputs + [f], inputs):
        return False
    restore(r, prev)
    return True


def run_topics(env: Envelope, target: Target, step: str, lang: str, fn: TopicFn, *, jobs: int = 1,
               write_step: bool = True, report_unexpected: bool = False) -> None:
    topics = [t for t in target.topics if t.dir.is_dir()]
    env.diagnostics.extend(target.diagnostics)

    def one(t: Topic, prog: Progress) -> TopicResult:
        r = TopicResult(t.id, t.rel)
        with prog.topic(t.id) as tp:
            try:
                cfg = load(t.root, t.module_dir)
                scan = scan_topic(t)
                for d in scan:
                    if d.level == "error" or report_unexpected:
                        d.lang = d.lang or (lang if d.level == "error" else None)
                        r.diagnostics.append(d)
                if not any(d.level == "error" for d in r.diagnostics):
                    fsutil.clean_partials(t.build)
                    fn(t, r, tp, cfg)
            except Fail as f:
                f.diagnostic.topic = f.diagnostic.topic or t.id
                f.diagnostic.lang = f.diagnostic.lang or lang
                r.diagnostics.append(f.diagnostic)
            except Cancelled:
                env.cancelled = True
                r.diagnostics.append(Diagnostic("CANCELLED", "Cancelled before this topic finished.", topic=t.id, lang=lang))
            except Exception as e:  # noqa: BLE001 - one topic failing never aborts the run
                progress.log(traceback.format_exc(), level="error", topic=t.id)
                r.diagnostics.append(Diagnostic("INTERNAL", f"{type(e).__name__}: {e}", topic=t.id, lang=lang,
                                                hint="This is a bug in bcn. The traceback is on stderr."))
            if not r.skipped:
                r.ok = not any(d.level == "error" for d in r.diagnostics)
            tp.skipped = r.skipped
        if write_step and not r.skipped and not progress.CANCEL.is_set():
            try:
                fsutil.write_json(t.step_file(step, lang), env.topic_envelope(r))
            except OSError as e:
                r.diagnostics.append(Diagnostic("INTERNAL", f"Could not write step result: {e}", topic=t.id))
                r.ok = False
        return r

    with Progress(step, len(topics)) as prog:
        results: dict[str, TopicResult] = {}
        if jobs <= 1:
            for t in topics:
                if progress.CANCEL.is_set():
                    env.cancelled = True
                    break
                results[t.id] = one(t, prog)
        else:
            with cf.ThreadPoolExecutor(max_workers=jobs) as pool:
                futs = {}
                for t in topics:
                    futs[pool.submit(lambda t=t: None if progress.CANCEL.is_set() else one(t, prog))] = t
                for fut in cf.as_completed(futs):
                    res = fut.result()
                    if res is not None:
                        results[futs[fut].id] = res
        if progress.CANCEL.is_set():
            env.cancelled = True
        env.results.extend(results[t.id] for t in topics if t.id in results)
        prog.done(cancelled=env.cancelled)
