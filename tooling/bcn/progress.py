"""NDJSON progress on stderr, heartbeats, cancellation and child processes.

Two levels of progress, always: item/items/pct across the run and topic_pct
within the current topic. A heartbeat goes out at least every two seconds so a
UI can tell slow work from a hung process. SIGINT stops everything cleanly.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator

from .envelope import Cancelled

CANCEL = threading.Event()
_children: set[subprocess.Popen] = set()
_children_lock = threading.Lock()
_out_lock = threading.Lock()
_quiet = False
HEARTBEAT_S = 1.5


def set_quiet(q: bool) -> None:
    global _quiet
    _quiet = q


def emit(event: dict) -> None:
    if _quiet:
        return
    line = json.dumps(event, ensure_ascii=False)
    with _out_lock:
        sys.stderr.write(line + "\n")
        sys.stderr.flush()


def log(message: str, level: str = "info", **kw: object) -> None:
    emit({"event": "log", "level": level, "message": message, **kw})


def _terminate_children() -> None:
    with _children_lock:
        procs = list(_children)
    for p in procs:
        try:
            p.terminate()
        except OSError:
            pass
    deadline = time.monotonic() + 5
    for p in procs:
        try:
            p.wait(max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            p.kill()


def install_signal_handlers() -> None:
    def handler(signum: int, frame: object) -> None:
        if CANCEL.is_set():
            return
        CANCEL.set()
        threading.Thread(target=_terminate_children, daemon=True).start()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


def check_cancel() -> None:
    if CANCEL.is_set():
        raise Cancelled()


def run(cmd: list[str], *, env: dict[str, str] | None = None, cwd: str | None = None,
        on_stdout_line: Callable[[str], None] | None = None,
        on_stderr_line: Callable[[str], None] | None = None, timeout: float | None = None) -> tuple[int, str, str]:
    """Run a child process, cancellable. Returns (code, stdout, stderr)."""
    check_cancel()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                         env={**os.environ, **(env or {})}, cwd=cwd, text=True, errors="replace",
                         start_new_session=True)  # our SIGINT handler owns the child's lifetime
    with _children_lock:
        _children.add(p)
    out_chunks: list[str] = []
    err_chunks: list[str] = []

    def read_out() -> None:
        assert p.stdout
        for line in p.stdout:
            out_chunks.append(line)
            if on_stdout_line:
                on_stdout_line(line.rstrip("\n"))

    def read_err() -> None:
        assert p.stderr
        for line in p.stderr:
            err_chunks.append(line)
            if on_stderr_line:
                on_stderr_line(line.rstrip("\n"))
            if len(err_chunks) > 2000:
                del err_chunks[:1000]

    threads = [threading.Thread(target=read_out, daemon=True), threading.Thread(target=read_err, daemon=True)]
    for t in threads:
        t.start()
    t0 = time.monotonic()
    try:
        while True:
            try:
                p.wait(0.2)
                break
            except subprocess.TimeoutExpired:
                if CANCEL.is_set():
                    p.terminate()
                    try:
                        p.wait(5)
                    except subprocess.TimeoutExpired:
                        p.kill()
                    raise Cancelled()
                if timeout and time.monotonic() - t0 > timeout:
                    p.kill()
                    p.wait()
                    return 124, "".join(out_chunks), "".join(err_chunks) + f"\n[bcn] killed after {timeout}s\n"
        for t in threads:
            t.join(2)
    finally:
        with _children_lock:
            _children.discard(p)
    if CANCEL.is_set():
        raise Cancelled()
    return p.returncode, "".join(out_chunks), "".join(err_chunks)


@dataclass
class _Active:
    topic: str
    item: int
    pct: float = 0.0
    message: str = ""


@dataclass
class Progress:
    step: str
    items: int
    t0: float = field(default_factory=time.monotonic)
    completed: int = 0
    completed_real: int = 0
    real_time: float = 0.0
    started: int = 0
    _active: dict[int, _Active] = field(default_factory=dict)
    _last_emit: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def __enter__(self) -> "Progress":
        self._thread = threading.Thread(target=self._heartbeat, daemon=True)
        self._thread.start()
        self._emit(None, "starting")
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(2)

    def _heartbeat(self) -> None:
        while not self._stop.wait(0.5):
            if time.monotonic() - self._last_emit >= HEARTBEAT_S:
                with self._lock:
                    actives = list(self._active.values())
                if actives:
                    for a in actives:
                        self._emit(a, a.message, heartbeat=True)
                else:
                    self._emit(None, "working", heartbeat=True)

    def _overall(self) -> float:
        with self._lock:
            frac = sum(a.pct for a in self._active.values()) / 100.0
        return min(100.0, 100.0 * (self.completed + frac) / self.items) if self.items else 100.0

    def _eta(self, pct: float) -> int | None:
        elapsed = time.monotonic() - self.t0
        done_units = pct / 100.0 * self.items
        if done_units <= 0 or elapsed < 1:
            return None
        if self.completed_real == 0:
            with self._lock:
                best = max((a.pct for a in self._active.values()), default=0.0)
            if best < 5:
                return None  # nothing to base an estimate on yet
        return int(elapsed / done_units * (self.items - done_units) * 1000)

    def _emit(self, a: _Active | None, message: str, heartbeat: bool = False) -> None:
        pct = self._overall()
        ev: dict = {
            "event": "progress",
            "step": self.step,
            "topic": a.topic if a else None,
            "item": a.item if a else min(self.completed + 1, self.items),
            "items": self.items,
            "pct": round(pct, 1),
            "topic_pct": round(a.pct, 1) if a else None,
            "elapsed_ms": int((time.monotonic() - self.t0) * 1000),
            "message": message,
        }
        eta = self._eta(pct)
        if eta is not None:
            ev["eta_ms"] = eta
        if heartbeat:
            ev["heartbeat"] = True
        self._last_emit = time.monotonic()
        emit(ev)

    def topic(self, topic_id: str) -> "TopicProgress":
        with self._lock:
            self.started += 1
            key = self.started
            self._active[key] = _Active(topic_id, key)
        return TopicProgress(self, key)

    def _finish(self, key: int, skipped: bool, seconds: float) -> None:
        with self._lock:
            a = self._active.pop(key, None)
            self.completed += 1
            if not skipped:
                self.completed_real += 1
                self.real_time += seconds
        if a:
            if CANCEL.is_set():
                self._emit(a, "cancelled")
            else:
                a.pct = 100.0
                self._emit(a, "skipped" if skipped else "done")

    def done(self, cancelled: bool = False) -> None:
        emit({"event": "done", "step": self.step, "cancelled": cancelled,
              "elapsed_ms": int((time.monotonic() - self.t0) * 1000)})


class TopicProgress:
    def __init__(self, parent: Progress, key: int) -> None:
        self.parent = parent
        self.key = key
        self.t0 = time.monotonic()
        self.skipped = False

    def update(self, pct: float, message: str = "") -> None:
        a = self.parent._active.get(self.key)
        if not a:
            return
        a.pct = max(0.0, min(100.0, pct))
        a.message = message or a.message
        self.parent._emit(a, a.message)

    def __enter__(self) -> "TopicProgress":
        return self

    def __exit__(self, *exc: object) -> None:
        self.parent._finish(self.key, self.skipped, time.monotonic() - self.t0)


def iter_lines_nonblocking(stream: Iterator[str]) -> Iterator[str]:
    yield from stream
