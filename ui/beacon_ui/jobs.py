"""The job queue. Every action is a job; nothing runs synchronously in a request.

A job is one command over one or more target paths, run as one bcn invocation
per target. Progress is bcn's own NDJSON from stderr, relayed to the browser
unchanged with the job id attached. The envelope from stdout is stored whole.

Up to `parallel_jobs` jobs run at once, but never two whose targets overlap, so
two jobs never touch the same topic. Cancelling sends SIGINT so bcn can stop its
children and remove partial output itself.
"""

from __future__ import annotations

import collections
import datetime as _dt
import json
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .bcn import Bcn
from .bus import Bus
from .db import DB

STALL_SECONDS = 10
LOG_LINES = 400

# What the browser may ask for. Anything else is refused.
COMMANDS = {"validate", "render", "cues", "subtitles", "compose", "package", "qa", "review", "intake", "translation"}
FLAG_ARGS = {"force", "no_bumpers", "dump_narration", "accept", "clear", "dry_run", "export"}
VALUE_ARGS = {
    "lang": re.compile(r"^(en|zh)$"),
    "theme": re.compile(r"^[A-Za-z0-9_-]{1,40}$"),
    "set": re.compile(r"^\d{1,2}=\d{1,2}:\d{2}:\d{2}(?:[.,]\d{1,3})?$"),
    "unset": re.compile(r"^\d{1,2}$"),
    "item": re.compile(r"^[a-z]{2}-[0-9a-f]{10}$"),
    "correct": re.compile(r"^[^\x00-\x1f]{1,200}$"),
    "from": re.compile(r"^.{1,1024}$"),     # server-provided paths only, never from the browser
    "import": re.compile(r"^.{1,1024}$"),   # checked to be inside the root before use
    "by": re.compile(r"^[^\x00-\x1f]{0,80}$"),
}


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def overlaps(a: str, b: str) -> bool:
    if a == "." or b == ".":
        return True
    pa, pb = a.split("/"), b.split("/")
    n = min(len(pa), len(pb))
    return pa[:n] == pb[:n]


@dataclass
class Job:
    id: int
    command: str
    label: str
    targets: list[str]
    args: dict[str, Any]
    state: str
    created: str
    by: str = ""
    started: str | None = None
    finished: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    envelopes: list[dict[str, Any]] = field(default_factory=list)
    log: collections.deque = field(default_factory=lambda: collections.deque(maxlen=LOG_LINES))
    progress: dict[str, Any] | None = None
    target_index: int = 0
    last_event: float = 0.0
    cancel_requested: bool = False
    proc: subprocess.Popen | None = None
    t0: float = 0.0

    def summary(self, with_log: bool = False) -> dict[str, Any]:
        stalled = self.state == "running" and self.last_event and (time.time() - self.last_event) > STALL_SECONDS
        d = {
            "id": self.id, "command": self.command, "label": self.label, "targets": self.targets, "args": self.args,
            "state": "stalled" if stalled else self.state, "created": self.created, "started": self.started,
            "finished": self.finished, "by": self.by, "exit_code": self.exit_code, "duration_ms": self.duration_ms,
            "progress": self.progress, "target_index": self.target_index, "target_count": len(self.targets),
            "last_event_age_ms": int((time.time() - self.last_event) * 1000) if self.last_event else None,
            "ok": None if self.state in ("queued", "running") else self.state == "done",
        }
        if with_log:
            d["log"] = list(self.log)
            d["envelopes"] = self.envelopes
        return d


class JobQueue:
    def __init__(self, db: DB, bus: Bus, bcn: Bcn, root: Path, prefs: Callable[[], dict[str, Any]],
                 on_finished: Callable[[Job], None]) -> None:
        self.db, self.bus, self.bcn, self.root = db, bus, bcn, root
        self.prefs = prefs
        self.on_finished = on_finished
        self.jobs: dict[int, Job] = {}
        self.lock = threading.Lock()
        self._recover()
        threading.Thread(target=self._scheduler, daemon=True, name="job-scheduler").start()

    # -- persistence ------------------------------------------------------------------
    def _recover(self) -> None:
        """Running jobs from a previous backend are marked interrupted, never silently lost."""
        self.db.execute("UPDATE jobs SET state='interrupted', finished=? WHERE state='running'", (now(),))
        for row in self.db.query("SELECT * FROM jobs WHERE state='queued' ORDER BY id"):
            self.jobs[row["id"]] = self._from_row(row)

    def _from_row(self, row: dict[str, Any]) -> Job:
        j = Job(row["id"], row["command"], row["label"], json.loads(row["targets"]), json.loads(row["args"]), row["state"],
                row["created"], row["by"] or "", row["started"], row["finished"], row["exit_code"], row["duration_ms"])
        if row.get("envelope"):
            j.envelopes = json.loads(row["envelope"])
        if row.get("log"):
            j.log.extend(row["log"].splitlines())
        return j

    def _save(self, j: Job) -> None:
        self.db.execute(
            "UPDATE jobs SET started=?, finished=?, state=?, exit_code=?, duration_ms=?, envelope=?, log=? WHERE id=?",
            (j.started, j.finished, j.state, j.exit_code, j.duration_ms, json.dumps(j.envelopes) if j.envelopes else None,
             "\n".join(j.log), j.id))

    # -- public -----------------------------------------------------------------------
    def submit(self, command: str, targets: list[str], args: dict[str, Any], label: str, by: str) -> Job:
        if command not in COMMANDS:
            raise ValueError(f"'{command}' is not a job the UI can run.")
        if not targets:
            raise ValueError("A job needs at least one target.")
        for k, v in args.items():
            if k in FLAG_ARGS:
                if not isinstance(v, bool):
                    raise ValueError(f"'{k}' must be true or false.")
            elif k in VALUE_ARGS:
                if not isinstance(v, (str, int)) or not VALUE_ARGS[k].match(str(v)):
                    raise ValueError(f"'{k}' has an invalid value.")
            else:
                raise ValueError(f"'{k}' is not an allowed argument.")
        cur = self.db.execute(
            "INSERT INTO jobs(created, by, command, label, targets, args, state) VALUES(?,?,?,?,?,?, 'queued')",
            (now(), by, command, label, json.dumps(targets), json.dumps(args)))
        j = Job(cur.lastrowid, command, label, targets, args, "queued", now(), by)
        with self.lock:
            self.jobs[j.id] = j
        self._publish(j)
        return j

    def cancel(self, job_id: int) -> Job | None:
        with self.lock:
            j = self.jobs.get(job_id)
        if not j:
            return None
        j.cancel_requested = True
        if j.state == "queued":
            j.state = "cancelled"
            j.finished = now()
            self._save(j)
            with self.lock:
                self.jobs.pop(j.id, None)
            self._publish(j)
        elif j.proc and j.proc.poll() is None:
            j.proc.send_signal(signal.SIGINT)  # bcn cleans up after itself
        return j

    def get(self, job_id: int) -> dict[str, Any] | None:
        with self.lock:
            j = self.jobs.get(job_id)
        if j:
            return j.summary(with_log=True)
        rows = self.db.query("SELECT * FROM jobs WHERE id=?", (job_id,))
        return self._from_row(rows[0]).summary(with_log=True) if rows else None

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            live = {j.id: j.summary() for j in self.jobs.values()}
        rows = self.db.query("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
        out = []
        for row in rows:
            out.append(live.pop(row["id"], None) or self._from_row(row).summary())
        return sorted(live.values(), key=lambda x: -x["id"]) + out

    def running(self) -> int:
        with self.lock:
            return sum(1 for j in self.jobs.values() if j.state == "running")

    # -- scheduling -------------------------------------------------------------------
    def _scheduler(self) -> None:
        last_stall_check = 0.0
        while True:
            time.sleep(0.3)
            limit = max(1, int(self.prefs().get("parallel_jobs", 2)))
            with self.lock:
                running = [j for j in self.jobs.values() if j.state == "running"]
                queued = sorted((j for j in self.jobs.values() if j.state == "queued"), key=lambda j: j.id)
                to_start = []
                for j in queued:
                    if len(running) + len(to_start) >= limit:
                        break
                    busy = [t for r in running + to_start for t in r.targets]
                    if any(overlaps(a, b) for a in j.targets for b in busy):
                        continue
                    j.state = "running"
                    to_start.append(j)
            for j in to_start:
                threading.Thread(target=self._run, args=(j,), daemon=True, name=f"job-{j.id}").start()
            if time.time() - last_stall_check > 2:
                last_stall_check = time.time()
                for j in running:
                    if j.last_event and time.time() - j.last_event > STALL_SECONDS:
                        self._publish(j)  # lets the browser show it as stalled

    def _argv(self, j: Job, target: str) -> list[str]:
        a = j.args
        path = str(self.root / target) if target != "." else str(self.root)
        argv = [j.command, path]
        for k in ("lang", "theme", "set", "unset", "item", "correct", "from", "by"):
            if k in a and a[k] not in (None, ""):
                argv += [f"--{k}", str(a[k])]
        if "import" in a:
            argv += ["--import", str(a["import"])]
        for k in FLAG_ARGS:
            if a.get(k):
                argv.append("--" + k.replace("_", "-"))
        if j.command in ("validate", "render", "cues", "subtitles", "compose", "package", "qa"):
            argv += ["--jobs", str(max(1, int(self.prefs().get("jobs", 1))))]
        return argv

    def _run(self, j: Job) -> None:
        j.started = now()
        j.t0 = time.time()
        j.last_event = time.time()
        self._save(j)
        self._publish(j)
        codes = []
        try:
            for i, target in enumerate(j.targets):
                if j.cancel_requested:
                    break
                j.target_index = i
                code, envelope = self._invoke(j, target)
                codes.append(code)
                if envelope is not None:
                    j.envelopes.append(envelope)
        except Exception as e:  # noqa: BLE001
            j.log.append(json.dumps({"event": "log", "level": "error", "message": f"UI backend error: {e}"}))
            codes.append(-1)
        j.finished = now()
        j.duration_ms = int((time.time() - j.t0) * 1000)
        j.exit_code = next((c for c in codes if c != 0), 0) if codes else None
        if j.cancel_requested:
            j.state = "cancelled"
        else:
            j.state = "done" if codes and all(c == 0 for c in codes) else "failed"
        self._save(j)
        with self.lock:
            self.jobs.pop(j.id, None)
        self._publish(j)
        try:
            self.on_finished(j)
        except Exception:  # noqa: BLE001
            pass

    def _invoke(self, j: Job, target: str) -> tuple[int, dict[str, Any] | None]:
        argv = [*self.bcn.prefix, *self._argv(j, target)]
        j.log.append(json.dumps({"event": "log", "level": "info", "message": "$ bcn " + " ".join(argv[len(self.bcn.prefix):])}))
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                             text=True, cwd=self.root, errors="replace")
        j.proc = p
        out_chunks: list[str] = []
        reader = threading.Thread(target=lambda: out_chunks.append(p.stdout.read() if p.stdout else ""), daemon=True)
        reader.start()
        assert p.stderr
        for line in p.stderr:
            line = line.rstrip("\n")
            if not line:
                continue
            j.last_event = time.time()
            j.log.append(line)
            try:
                ev = json.loads(line)
            except ValueError:
                ev = {"event": "log", "level": "info", "message": line}
            if ev.get("event") == "progress":
                j.progress = ev
            # Relayed unchanged; the job id and target position ride alongside.
            self.bus.publish({"type": "job-event", "job": j.id, "target_index": j.target_index,
                              "target_count": len(j.targets), "event": ev})
        code = p.wait()
        reader.join(5)
        j.proc = None
        text = "".join(out_chunks)
        try:
            env = json.loads(text) if text.strip() else None
        except ValueError:
            env = None
            j.log.append(json.dumps({"event": "log", "level": "error", "message": "bcn printed no envelope"}))
        return code, env

    def _publish(self, j: Job) -> None:
        self.bus.publish({"type": "job", "job": j.summary()})
