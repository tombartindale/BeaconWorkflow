"""Programme state, which is always bcn status's and never the UI's own.

Polled on a timer, refreshed immediately after any job finishes, and refreshed
when the tree changes on disk (files arrive by sync, not only through the UI).
Held in memory with a short TTL so moving between views does not rescan.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .bcn import Bcn, BcnError
from .bus import Bus

TTL_SECONDS = 3.0
QUIET_SECONDS = 2.0  # a changing tree must settle this long before it is read: sync writes arrive in pieces
MODULE_RE = re.compile(r"^[A-Z]{2}\d{4}$")
UNIT_RE = re.compile(r"^U\d{2}$")
TOPIC_RE = re.compile(r"^T\d{2}$")


class StatusCache:
    def __init__(self, bcn: Bcn, bus: Bus, poll_seconds: Callable[[], float]) -> None:
        self.bcn = bcn
        self.bus = bus
        self.poll_seconds = poll_seconds
        self._env: dict[str, Any] | None = None
        self._at = 0.0
        self._lock = threading.Lock()
        self._refreshing = threading.Lock()
        self.version = 0
        self.error: str | None = None
        threading.Thread(target=self._poller, daemon=True, name="status-poll").start()

    def get(self, max_age: float = TTL_SECONDS) -> dict[str, Any] | None:
        with self._lock:
            fresh = self._env is not None and time.time() - self._at < max_age
            env = self._env
        if fresh:
            return env
        return self.refresh("read")

    def refresh(self, reason: str) -> dict[str, Any] | None:
        if not self._refreshing.acquire(blocking=False):
            # Another refresh is running; wait for it rather than scanning twice.
            with self._refreshing:
                pass
            with self._lock:
                return self._env
        try:
            try:
                env = self.bcn.query("status", str(self.bcn.root))
                self.error = None
            except BcnError as e:
                self.error = str(e)
                self.bus.publish({"type": "status-error", "error": self.error})
                with self._lock:
                    return self._env
            with self._lock:
                changed = self._env is None or _fingerprint(env) != _fingerprint(self._env)
                self._env = env
                self._at = time.time()
                if changed:
                    self.version += 1
            if changed:
                self.bus.publish({"type": "status", "version": self.version, "reason": reason,
                                  "summary": env.get("summary")})
            return env
        finally:
            self._refreshing.release()

    def _poller(self) -> None:
        while True:
            time.sleep(max(3.0, float(self.poll_seconds())))
            self.refresh("poll")


def _fingerprint(env: dict[str, Any]) -> str:
    # Artefact times are included so that any new or rebuilt file (a recording script, a draft)
    # refreshes the views showing it, even when no stage changes.
    slim = [(r.get("topic"), r.get("en"), r.get("zh"), r.get("hydration"), r.get("unreviewed_mistranscriptions"),
             [(a.get("key"), a.get("mtime")) for a in r.get("artifacts", [])])
            for r in env.get("results", [])]
    return hashlib.sha1(repr((slim, env.get("diagnostics"))).encode()).hexdigest()


class Watcher:
    """Polls the tree's source files and step results and refreshes status once changes settle.

    Stats only the names the pipeline cares about, the same allowlist bcn uses,
    so it stays cheap across a whole programme and never opens a file.
    """

    def __init__(self, root: Path, on_change: Callable[[str], None], interval: float = 2.0) -> None:
        self.root = root
        self.on_change = on_change
        self.interval = interval
        threading.Thread(target=self._loop, daemon=True, name="tree-watch").start()

    def signature(self) -> str:
        h = hashlib.sha1()

        def add(p: str) -> None:
            try:
                st = os.stat(p)
            except OSError:
                return
            h.update(f"{p}|{st.st_mtime_ns}|{st.st_size}\n".encode())

        root = str(self.root)
        add(os.path.join(root, "programme.toml"))
        for m in _dirs(root, MODULE_RE):
            md = os.path.join(root, m)
            for name in _files(md):
                add(os.path.join(md, name))
            add(os.path.join(md, "build", "validate.json"))
            for u in _dirs(md, UNIT_RE):
                ud = os.path.join(md, u)
                add(os.path.join(ud, "activity.md"))
                for t in _dirs(ud, TOPIC_RE):
                    td = os.path.join(ud, t)
                    for name in _files(td):
                        add(os.path.join(td, name))
                    for sub, pat in (("edit", None), ("assets", None), ("build", ".json"), ("out", ".json")):
                        sd = os.path.join(td, sub)
                        for name in _files(sd):
                            if pat is None or name.endswith(pat):
                                add(os.path.join(sd, name))
        return h.hexdigest()

    def _loop(self) -> None:
        last = self.signature()
        pending_since = None
        while True:
            time.sleep(self.interval)
            try:
                sig = self.signature()
            except Exception:  # noqa: BLE001
                continue
            if sig != last:
                last = sig
                pending_since = time.time()  # still changing: wait for it to settle
                continue
            if pending_since and time.time() - pending_since >= QUIET_SECONDS:
                pending_since = None
                self.on_change("tree changed")


def _dirs(d: str, rx: re.Pattern[str]) -> list[str]:
    try:
        return sorted(e.name for e in os.scandir(d) if e.is_dir() and rx.match(e.name))
    except OSError:
        return []


def _files(d: str) -> list[str]:
    try:
        return sorted(e.name for e in os.scandir(d) if e.is_file() and not e.name.startswith("."))
    except OSError:
        return []
