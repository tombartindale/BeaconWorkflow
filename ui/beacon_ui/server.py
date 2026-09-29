"""HTTP server: JSON API, server-sent events, static front end, and files from the root.

Single user, bound to localhost, no authentication. Requests with a foreign
Host or Origin header are refused so a web page elsewhere cannot drive it.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import mimetypes
import os
import queue
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .bcn import Bcn, BcnError
from .bus import Bus
from .db import DB
from .jobs import JobQueue
from .state import StatusCache, Watcher

STATIC = Path(__file__).parent / "static"
TOPIC_ID_RE = re.compile(r"^([A-Z]{2}\d{4})-(U\d{2})-(T\d{2})$")
TARGET_RE = re.compile(r"^(\.|[A-Z]{2}\d{4}(/U\d{2}(/T\d{2})?)?)$")
UPLOAD_MAX = 500 * 1024 * 1024
mimetypes.add_type("text/vtt", ".vtt")
mimetypes.add_type("application/x-subrip", ".srt")
mimetypes.add_type("text/markdown", ".md")
mimetypes.add_type("text/javascript", ".js")


class StartupError(Exception):
    pass


def check_root(root: Path) -> list[str]:
    """Fail clearly rather than present an empty dashboard that looks like nothing is done."""
    if not root.is_dir():
        raise StartupError(f"The programme root {root} does not exist.")
    if not (root / "programme.toml").is_file():
        raise StartupError(f"{root} has no programme.toml; it is not a programme root.")
    modules = [p.name for p in root.iterdir() if p.is_dir() and re.match(r"^[A-Z]{2}\d{4}$", p.name)]
    if not modules:
        raise StartupError(f"{root} contains no module folders (named like KV7015).")
    probe = root / f".beacon-ui-write-test-{os.getpid()}"
    try:
        probe.write_text("x")
        probe.unlink()
    except OSError as e:
        raise StartupError(f"{root} is not writable: {e}") from e
    warnings = []
    if "CloudStorage" in str(root.resolve()) or "OneDrive" in str(root.resolve()):
        warnings.append("The programme root is inside a synced folder. Files may be cloud-only placeholders; "
                        "keeping the root on local disk and copying to SharePoint deliberately avoids that.")
    return warnings


class App:
    def __init__(self, root: Path, bcn_prefix: list[str], db_path: Path, data_dir: Path) -> None:
        self.root = root.resolve()
        self.data_dir = data_dir
        self.warnings = check_root(self.root)
        self.db = DB(db_path)
        self.bus = Bus()
        self.bcn = Bcn(bcn_prefix, self.root)
        self.status = StatusCache(self.bcn, self.bus, lambda: self.db.prefs().get("poll_seconds", 15))
        self.jobs = JobQueue(self.db, self.bus, self.bcn, self.root, self.db.prefs, self._job_finished)
        self.watcher = Watcher(self.root, lambda reason: self.status.refresh(reason))
        self.doctor: dict[str, Any] | None = None
        self.codes: list[dict[str, Any]] = []
        self._query_cache: dict[tuple, tuple[float, dict[str, Any]]] = {}
        threading.Thread(target=self._warm, daemon=True).start()

    def _warm(self) -> None:
        try:
            self.doctor = self.bcn.query("doctor", str(self.root))
            self.codes = self.bcn.query("codes").get("codes", [])
        except BcnError as e:
            self.warnings.append(f"bcn could not run: {e}")
        env = self.status.refresh("startup")
        share = ((env or {}).get("summary") or {}).get("cloud_share", 0)
        if share and share >= 0.1:
            self.warnings.append(f"{share:.0%} of topics have cloud-only files. A pipeline that processes a placeholder "
                                 "produces convincing rubbish: make the folders available offline in Finder "
                                 "(Always Keep on This Device) before running jobs.")
            self.bus.publish({"type": "warnings", "warnings": self.warnings})

    def _job_finished(self, job: Any) -> None:
        self._query_cache.clear()
        self.status.refresh(f"job {job.id} finished")

    def cached_query(self, key: tuple, ttl: float, *argv: str) -> dict[str, Any]:
        hit = self._query_cache.get(key)
        if hit and time.time() - hit[0] < ttl and hit[1].get("_status_version") == self.status.version:
            return hit[1]
        env = self.bcn.query(*argv)
        env["_status_version"] = self.status.version
        self._query_cache[key] = (time.time(), env)
        return env

    def safe_path(self, rel: str) -> Path:
        """A path beneath the root, symlinks resolved. Anything else is refused."""
        rel = urllib.parse.unquote(rel).lstrip("/")
        if not rel or "\x00" in rel:
            raise PermissionError(rel)
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise PermissionError(rel)
        return p

    def target_rel(self, value: str) -> str:
        m = TOPIC_ID_RE.match(value)
        if m:
            value = "/".join(m.groups())
        if not TARGET_RE.match(value):
            raise ValueError(f"'{value}' is not a programme, module, unit or topic.")
        return value

    def operator(self) -> str:
        return self.db.prefs().get("operator") or os.environ.get("USER", "")


def job_label(command: str, targets: list[str], args: dict[str, Any]) -> str:
    what = command
    if args.get("lang") == "zh":
        what += " (zh)"
    if args.get("force"):
        what += " --force"
    if command == "cues" and args.get("set"):
        what = f"cues: set slide {args['set']}"
    if command == "review":
        what = "review: " + ("accept" if args.get("accept") else "correct" if args.get("correct") else "clear")
    if command == "ack":
        what = "withdraw acknowledgement" if args.get("clear") else "acknowledge " + str(args.get("fingerprint", "")).split("|")[0]
    if command == "sync":
        what = ("pull from OneDrive" if args.get("pull") else "push to OneDrive") + (" (keep chosen version)" if args.get("prefer") else "")
    if command == "translation":
        what = "translation export" if args.get("export") else "translation import"
    scope = targets[0] if len(targets) == 1 else f"{len(targets)} targets"
    return f"{what} · {'programme' if scope == '.' else scope}"


class Handler(BaseHTTPRequestHandler):
    app: App
    server_version = "BeaconUI/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet
        pass

    # -- plumbing ---------------------------------------------------------------------
    def _allowed(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("localhost", "127.0.0.1", "[::1]", "::1"):
            return False
        origin = self.headers.get("Origin")
        if origin:
            o = urllib.parse.urlparse(origin)
            if o.hostname not in ("localhost", "127.0.0.1", "::1"):
                return False
        return True

    def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, data: Any, status: int = 200) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def error(self, status: int, message: str) -> None:
        self.json({"error": message}, status)

    def body_json(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 5 * 1024 * 1024:
            raise ValueError("Request too large.")
        raw = self.rfile.read(n) if n else b"{}"
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object.")
        return data

    def query_args(self) -> dict[str, str]:
        return {k: v[-1] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}

    # -- dispatch ---------------------------------------------------------------------
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def _dispatch(self, method: str) -> None:
        if not self._allowed():
            self.error(403, "Requests are accepted from localhost only.")
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            for m, rx, fn in ROUTES:
                if m == method:
                    match = rx.match(path)
                    if match:
                        fn(self, *match.groups())
                        return
            if method == "GET" and not path.startswith("/api/"):
                self.static(path)
                return
            self.error(404, "Not found.")
        except PermissionError:
            self.error(403, "That path is outside the programme root.")
        except (ValueError, KeyError) as e:
            self.error(400, str(e))
        except BcnError as e:
            self.error(502, str(e))
        except (BrokenPipeError, ConnectionResetError):
            pass

    # -- static and files ---------------------------------------------------------------
    def static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        p = (STATIC / rel).resolve()
        if STATIC.resolve() not in p.parents or not p.is_file():
            p = STATIC / "index.html"  # client-side routes
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, p.read_bytes(), ctype)

    def files(self, rel: str) -> None:
        p = self.app.safe_path(rel)
        if not p.is_file():
            self.error(404, "No such file.")
            return
        q = self.query_args()
        if p.suffix == ".srt" and q.get("format") == "vtt":
            text = p.read_bytes().decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
            vtt = "WEBVTT\n\n" + re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", text)
            self._send(200, vtt.encode(), "text/vtt; charset=utf-8")
            return
        if q.get("view") and p.suffix in (".md", ".txt", ".json", ".toml", ".csv", ".srt", ".vtt"):
            # For the in-app document viewer: always text, never a download.
            self._send(200, p.read_bytes(), "text/plain; charset=utf-8")
            return
        size = p.stat().st_size
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        extra = {"Accept-Ranges": "bytes"}
        if q.get("download"):
            extra["Content-Disposition"] = f'attachment; filename="{p.name}"'
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d*)-(\d*)$", rng or "")
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
                end = size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                self._send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
                return
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            for k, v in extra.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(p, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(p, "rb") as f:
            while chunk := f.read(1 << 20):
                self.wfile.write(chunk)

    # -- API ----------------------------------------------------------------------------
    def boot(self) -> None:
        a = self.app
        self.json({
            "root": str(a.root), "prefs": a.db.prefs(), "operator": a.operator(), "warnings": a.warnings,
            "doctor": a.doctor, "codes": a.codes, "status_version": a.status.version,
        })

    def get_status(self) -> None:
        env = self.app.status.get()
        if env is None:
            self.error(503, self.app.status.error or "Status is not available yet.")
            return
        self.json({**env, "version": self.app.status.version, "jobs_running": self.app.jobs.running()})

    def refresh_status(self) -> None:
        self.app._query_cache.clear()
        self.app.status.refresh("requested")
        self.json({"version": self.app.status.version})

    def topic(self, topic_id: str) -> None:
        rel = self.app.target_rel(topic_id)
        path = str(self.app.root / rel)
        q = self.query_args()
        if q.get("verify"):
            self.json(self.app.bcn.query("status", path, "--verify"))
            return
        show = self.app.cached_query(("show", rel), 30, "show", path, "--asset-prefix", "/files/")
        env = self.app.status.get() or {}
        row = next((r for r in env.get("results", []) if r.get("topic") == topic_id), None)
        self.json({"show": show, "status": row})

    # -- script editing: bcn edit does the writing and checking; this only moves text --------
    def _edit_text_file(self, text: str) -> Path:
        d = self.app.data_dir / "edits"
        d.mkdir(parents=True, exist_ok=True)
        for old in sorted(d.glob("*.md"))[:-50]:
            old.unlink(missing_ok=True)
        f = d / f"{_dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.md"
        f.write_text(text, encoding="utf-8")
        return f

    def topic_source(self, topic_id: str) -> None:
        rel = self.app.target_rel(topic_id)
        lang = self.query_args().get("lang", "en")
        if lang not in ("en", "zh"):
            raise ValueError("lang must be en or zh")
        p = self.app.root / rel / ("topic.md" if lang == "en" else "topic.zh.md")
        if not p.is_file():
            self.json({"exists": False, "text": "", "sha256": None, "path": str(p.relative_to(self.app.root))})
            return
        data = p.read_bytes()
        self.json({"exists": True, "text": data.decode("utf-8-sig"), "sha256": hashlib.sha256(data).hexdigest(),
                   "path": str(p.relative_to(self.app.root))})

    def topic_check(self, topic_id: str) -> None:
        """Validate unsaved text (bcn edit --dry-run). Read-only, so it runs directly rather than as a job."""
        rel = self.app.target_rel(topic_id)
        data = self.body_json()
        lang = "zh" if data.get("lang") == "zh" else "en"
        f = self._edit_text_file(str(data.get("text", "")))
        try:
            env = self.app.bcn.query("edit", str(self.app.root / rel), "--from", str(f), "--lang", lang, "--dry-run", timeout=60)
        finally:
            f.unlink(missing_ok=True)
        self.json(env)

    def topic_save(self, topic_id: str) -> None:
        rel = self.app.target_rel(topic_id)
        data = self.body_json()
        lang = "zh" if data.get("lang") == "zh" else "en"
        f = self._edit_text_file(str(data.get("text", "")))
        args = {"from": str(f), "lang": lang}
        if data.get("expect_sha") and not data.get("overwrite"):
            args["expect_sha"] = str(data["expect_sha"])
        j = self.app.jobs.submit("edit", [rel], args, f"edit {'topic.zh.md' if lang == 'zh' else 'topic.md'} · {rel}",
                                 self.app.operator())
        self.json(j.summary(), 202)

    def diagnostics(self) -> None:
        rel = self.app.target_rel(self.query_args().get("path", "."))
        self.json(self.app.cached_query(("diagnostics", rel), 10, "diagnostics", str(self.app.root / rel)))

    def sync_preview(self) -> None:
        """Both directions as dry runs. They only stat files, so no cloud download is triggered."""
        a = self.app
        pull = a.cached_query(("sync", "pull"), 20, "sync", str(a.root), "--pull", "--dry-run")
        push = a.cached_query(("sync", "push"), 20, "sync", str(a.root), "--push", "--dry-run")
        self.json({"pull": pull, "push": push})

    def review(self) -> None:
        rel = self.app.target_rel(self.query_args().get("path", "."))
        self.json(self.app.cached_query(("review", rel), 10, "review", str(self.app.root / rel)))

    def list_jobs(self) -> None:
        self.json({"jobs": self.app.jobs.list(int(self.query_args().get("limit", 100)))})

    def get_job(self, job_id: str) -> None:
        j = self.app.jobs.get(int(job_id))
        if not j:
            self.error(404, "No such job.")
            return
        self.json(j)

    def create_job(self) -> None:
        data = self.body_json()
        command = str(data.get("command", ""))
        args = dict(data.get("args") or {})
        for k in ("from", "import", "by"):
            args.pop(k, None)  # server-supplied only
        targets = [self.app.target_rel(str(t)) for t in data.get("targets") or []]
        if command in ("review", "ack") or (command == "cues" and (args.get("set") or args.get("unset"))):
            args["by"] = self.app.operator() or "ui"
        j = self.app.jobs.submit(command, targets, args, job_label(command, targets, args), self.app.operator())
        self.json(j.summary(), 202)

    def cancel_job(self, job_id: str) -> None:
        j = self.app.jobs.cancel(int(job_id))
        if not j:
            self.error(404, "No such job, or it has already finished.")
            return
        self.json(j.summary())

    def intake(self) -> None:
        data = self.body_json()
        text = str(data.get("text", ""))
        if not text.strip():
            raise ValueError("Paste some text first.")
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        d = self.app.data_dir / "intake"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{stamp}.md"
        f.write_text(text, encoding="utf-8")
        target = self.app.target_rel(str(data.get("path") or "."))
        args = {"from": str(f), "dry_run": bool(data.get("dry_run"))}
        j = self.app.jobs.submit("intake", [target], args, f"intake{' (check only)' if args['dry_run'] else ''} · paste",
                                 self.app.operator())
        self.json(j.summary(), 202)

    def translation_list(self) -> None:
        base = self.app.root / "translation"

        def listing(sub: str) -> list[dict[str, Any]]:
            d = base / sub
            if not d.is_dir():
                return []
            out = []
            for p in sorted(d.iterdir(), reverse=True):
                if p.name.startswith("."):
                    continue
                st = p.stat()
                out.append({"name": p.name, "path": str(p.relative_to(self.app.root)), "kind": "zip" if p.is_file() else "folder",
                            "bytes": st.st_size if p.is_file() else None,
                            "mtime": _dt.datetime.fromtimestamp(st.st_mtime, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
            return out

        self.json({"exports": listing("exports"), "returned": listing("returned")})

    def translation_upload(self) -> None:
        q = self.query_args()
        name = re.sub(r"[^A-Za-z0-9._-]", "_", q.get("name", "returned.zip"))
        if not name.lower().endswith(".zip"):
            raise ValueError("Upload a .zip file.")
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > UPLOAD_MAX:
            raise ValueError("Upload is empty or too large.")
        d = self.app.root / "translation" / "returned"
        d.mkdir(parents=True, exist_ok=True)
        dest = d / name
        if dest.exists():
            dest = d / f"{dest.stem}-{int(time.time())}.zip"
        tmp = d / f".{dest.name}.partial"
        with open(tmp, "wb") as f:
            remaining = n
            while remaining > 0:
                chunk = self.rfile.read(min(1 << 20, remaining))
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)
        os.replace(tmp, dest)
        self.json({"path": str(dest.relative_to(self.app.root))}, 201)

    def translation_import(self) -> None:
        data = self.body_json()
        src = self.app.safe_path(str(data.get("source", "")))
        returned = (self.app.root / "translation" / "returned").resolve()
        if returned not in src.parents:
            raise PermissionError(str(src))
        target = self.app.target_rel(str(data.get("path") or "."))
        j = self.app.jobs.submit("translation", [target], {"import": str(src)}, f"translation import · {src.name}",
                                 self.app.operator())
        self.json(j.summary(), 202)

    def get_prefs(self) -> None:
        self.json(self.app.db.prefs())

    def put_prefs(self) -> None:
        data = self.body_json()
        if "jobs" in data:
            data["jobs"] = max(1, min(16, int(data["jobs"])))
        if "parallel_jobs" in data:
            data["parallel_jobs"] = max(1, min(8, int(data["parallel_jobs"])))
        if "poll_seconds" in data:
            data["poll_seconds"] = max(5, min(600, int(data["poll_seconds"])))
        self.json(self.app.db.set_prefs(data))

    def events(self) -> None:
        q = self.app.bus.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(f"event: hello\ndata: {json.dumps({'status_version': self.app.status.version})}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.app.bus.unsubscribe(q)


ROUTES = [
    ("GET", re.compile(r"^/api/boot$"), Handler.boot),
    ("GET", re.compile(r"^/api/status$"), Handler.get_status),
    ("POST", re.compile(r"^/api/status/refresh$"), Handler.refresh_status),
    ("GET", re.compile(r"^/api/topic/([A-Z]{2}\d{4}-U\d{2}-T\d{2})$"), Handler.topic),
    ("GET", re.compile(r"^/api/topic/([A-Z]{2}\d{4}-U\d{2}-T\d{2})/source$"), Handler.topic_source),
    ("POST", re.compile(r"^/api/topic/([A-Z]{2}\d{4}-U\d{2}-T\d{2})/check$"), Handler.topic_check),
    ("POST", re.compile(r"^/api/topic/([A-Z]{2}\d{4}-U\d{2}-T\d{2})/save$"), Handler.topic_save),
    ("GET", re.compile(r"^/api/diagnostics$"), Handler.diagnostics),
    ("GET", re.compile(r"^/api/review$"), Handler.review),
    ("GET", re.compile(r"^/api/sync$"), Handler.sync_preview),
    ("GET", re.compile(r"^/api/jobs$"), Handler.list_jobs),
    ("POST", re.compile(r"^/api/jobs$"), Handler.create_job),
    ("GET", re.compile(r"^/api/jobs/(\d+)$"), Handler.get_job),
    ("POST", re.compile(r"^/api/jobs/(\d+)/cancel$"), Handler.cancel_job),
    ("POST", re.compile(r"^/api/intake$"), Handler.intake),
    ("GET", re.compile(r"^/api/translation$"), Handler.translation_list),
    ("POST", re.compile(r"^/api/translation/upload$"), Handler.translation_upload),
    ("POST", re.compile(r"^/api/translation/import$"), Handler.translation_import),
    ("GET", re.compile(r"^/api/prefs$"), Handler.get_prefs),
    ("PUT", re.compile(r"^/api/prefs$"), Handler.put_prefs),
    ("GET", re.compile(r"^/api/events$"), Handler.events),
    ("GET", re.compile(r"^/files/(.+)$"), Handler.files),
]


def serve(app: App, host: str, port: int) -> ThreadingHTTPServer:
    Handler.app = app
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd
