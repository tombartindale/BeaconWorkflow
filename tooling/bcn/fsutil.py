"""Filesystem helpers: atomic writes, hydration, freshness and content verification."""

from __future__ import annotations

import contextlib
import json
import os
import struct
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator

# macOS sets this on dataless File Provider items (OneDrive "online-only").
# Not in Python's stat module. Advisory only: see tooling-spec 3, Hydration.
SF_DATALESS = 0x40000000

# Windows equivalents, for reference if this ever runs there.
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000


def hydration(path: Path, st: os.stat_result | None = None) -> str:
    """'local', 'cloud' or 'unknown', from attributes only. Never opens the file."""
    try:
        st = st or path.stat()
    except OSError:
        return "unknown"
    flags = getattr(st, "st_flags", None)
    if flags is not None and flags & SF_DATALESS:
        return "cloud"
    attrs = getattr(st, "st_file_attributes", None)
    if attrs is not None:
        if attrs & (FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS):
            return "cloud"
        return "local"
    blocks = getattr(st, "st_blocks", None)
    if blocks is not None and st.st_size > 0 and blocks == 0:
        return "cloud"
    if blocks is None:
        return "unknown"
    return "local"


def mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def newest(paths: list[Path]) -> float | None:
    times = [t for t in (mtime(p) for p in paths) if t is not None]
    return max(times) if times else None


def is_fresh(outputs: list[Path], inputs: list[Path]) -> bool:
    """Every output exists and none is older than the newest input."""
    if not outputs:
        return False
    out_times = [mtime(p) for p in outputs]
    if any(t is None for t in out_times):
        return False
    newest_in = newest(inputs)
    if newest_in is None:
        return True
    return min(out_times) >= newest_in  # type: ignore[type-var]


def recently_modified(path: Path, seconds: float = 2.0) -> bool:
    t = mtime(path)
    return t is not None and (time.time() - t) < seconds


_UMASK = os.umask(0)
os.umask(_UMASK)


@contextlib.contextmanager
def atomic_path(dest: Path, suffix: str | None = None) -> Iterator[Path]:
    """Yield a temp path in dest's directory; rename onto dest only if the block succeeds."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=suffix or f".partial{dest.suffix}", dir=dest.parent)
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        yield tmp_path
        os.chmod(tmp_path, 0o666 & ~_UMASK)  # mkstemp creates 0600; outputs are shared files
        os.replace(tmp_path, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def write_text(dest: Path, text: str) -> None:
    with atomic_path(dest) as tmp:
        tmp.write_text(text, encoding="utf-8")


def write_bytes(dest: Path, data: bytes) -> None:
    with atomic_path(dest) as tmp:
        tmp.write_bytes(data)


def write_json(dest: Path, data: Any) -> None:
    write_text(dest, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def clean_partials(directory: Path) -> None:
    """Remove temp files a killed run left behind. Safe: they are never real outputs."""
    if not directory.is_dir():
        return
    for p in directory.rglob(".*.partial*"):
        with contextlib.suppress(OSError):
            p.unlink()


def log(msg: str) -> None:
    """Anything conversational goes to stderr, never stdout."""
    sys.stderr.write(msg.rstrip("\n") + "\n")
    sys.stderr.flush()


# -- content verification (status --verify) --------------------------------------

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def png_size(path: Path) -> tuple[int, int] | None:
    with open(path, "rb") as f:
        head = f.read(24)
    if len(head) < 24 or head[:8] != PNG_SIG or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def mp4_has_ftyp(path: Path) -> bool:
    with open(path, "rb") as f:
        head = f.read(12)
    return len(head) >= 8 and head[4:8] == b"ftyp"
