"""Two-way sync between the local programme root and a shared folder (OneDrive/SharePoint).

The local root is the working copy the pipeline runs on. The shared folder is
where content arrives and where other people look. Sync is an explicit step in
each direction, never automatic.

Naming. The shared folder may use the team's flat convention, where everything
for a module sits in one folder and the topic id is in the file name
(KV7016/KV7016-U01-T01.md), or the pipeline's nested layout
(KV7016/U01/T01/topic.md). Both are recognised on pull. Push writes the flat
names, or whatever name the file was pulled from.

Safety.
  - A three-way comparison against sync-state.json decides who changed a file.
    Changed on one side: copied. Changed on both: a conflict, and neither side
    is overwritten unless --prefer says which wins.
  - Deletions never sync. A file on one side only is reported, not removed.
  - A pulled file gets the current time as its mtime, so the pipeline sees it
    as new and rebuilds whatever depends on it.
  - A dry run never opens a cloud-only file, so it never triggers a download.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import fsutil
from .envelope import sha256_file, utcnow
from .tree import MODULE_RE, conflict_of, is_noise

STATE_FILE = "sync-state.json"

# Remote file name (flat convention, inside the module folder) -> local path under the module.
# {m} is the module id. Groups: u (unit), t (topic), n (number), f (asset file name).
FLAT_RULES: list[tuple[str, str]] = [
    (r"(?:{m}-)?course-map\.md", "course-map.md"),
    (r"(?:{m}-)?(?:asset-requests|assets)\.md", "assets.md"),
    (r"(?:{m}-)?reading-list\.md", "reading-list.md"),
    (r"(?:{m}-)?assignment-(?P<n>\d+)\.md", "assignment-{n}.md"),
    (r"{m}-(?P<u>U\d\d)-activity\.md", "{u}/activity.md"),
    (r"{m}-(?P<u>U\d\d)-(?P<t>T\d\d)\.md", "{u}/{t}/topic.md"),
    (r"{m}-(?P<u>U\d\d)-(?P<t>T\d\d)\.zh\.md", "{u}/{t}/topic.zh.md"),
    (r"{m}-(?P<u>U\d\d)-(?P<t>T\d\d)\.review\.json", "{u}/{t}/review.json"),
    (r"{m}-(?P<u>U\d\d)-(?P<t>T\d\d)\.mp4", "{u}/{t}/edit/master.mp4"),
    (r"{m}-(?P<u>U\d\d)-(?P<t>T\d\d)\.srt", "{u}/{t}/edit/master.srt"),
    (r"{m}-(?P<u>U\d\d)-(?P<t>T\d\d)\.zh\.srt", "{u}/{t}/edit/master.zh.srt"),
    (r"{m}-(?P<u>U\d\d)-(?P<t>T\d\d)-assets/(?P<f>[^/]+)", "{u}/{t}/assets/{f}"),
]

# The pipeline's own nested layout, if someone has used it in the shared folder.
NESTED_RULES: list[tuple[str, str]] = [
    (r"(?P<u>U\d\d)/activity\.md", "{u}/activity.md"),
    (r"(?P<u>U\d\d)/(?P<t>T\d\d)/(?P<f>topic\.md|topic\.zh\.md|review\.json)", "{u}/{t}/{f}"),
    (r"(?P<u>U\d\d)/(?P<t>T\d\d)/edit/(?P<f>master\.mp4|master\.srt|master\.zh\.srt)", "{u}/{t}/edit/{f}"),
    (r"(?P<u>U\d\d)/(?P<t>T\d\d)/assets/(?P<f>[^/]+)", "{u}/{t}/assets/{f}"),
]

# Local path under the module -> flat remote name, for files that have never been pulled.
PUSH_NAMES: list[tuple[str, str]] = [
    (r"course-map\.md", "{m}-course-map.md"),
    (r"assets\.md", "{m}-asset-requests.md"),
    (r"reading-list\.md", "{m}-reading-list.md"),
    (r"assignment-(?P<n>\d+)\.md", "{m}-assignment-{n}.md"),
    (r"(?P<u>U\d\d)/activity\.md", "{m}-{u}-activity.md"),
    (r"(?P<u>U\d\d)/(?P<t>T\d\d)/topic\.md", "{m}-{u}-{t}.md"),
    (r"(?P<u>U\d\d)/(?P<t>T\d\d)/topic\.zh\.md", "{m}-{u}-{t}.zh.md"),
    (r"(?P<u>U\d\d)/(?P<t>T\d\d)/review\.json", "{m}-{u}-{t}.review.json"),
    (r"(?P<u>U\d\d)/(?P<t>T\d\d)/assets/(?P<f>[^/]+)", "{m}-{u}-{t}-assets/{f}"),
]
# Media comes from the editor and the translator, not from us: pulled, never pushed back.
PULL_ONLY = re.compile(r"/edit/")


def _match(rules: list[tuple[str, str]], rel: str, module: str) -> str | None:
    for rx, template in rules:
        m = re.fullmatch(rx.replace("{m}", re.escape(module)), rel)
        if m:
            return template.format(m=module, **{k: v for k, v in m.groupdict().items() if v is not None})
    return None


def remote_to_local(module: str, rel: str) -> str | None:
    """A remote path (relative to the remote module folder) to a local one (relative to the module)."""
    return _match(FLAT_RULES, rel, module) or _match(NESTED_RULES, rel, module)


def local_to_remote(module: str, rel: str) -> str | None:
    return _match(PUSH_NAMES, rel, module)


@dataclass
class Side:
    path: Path
    exists: bool = False
    size: int | None = None
    mtime: float | None = None
    cloud: bool = False
    _sha: str | None = None

    @classmethod
    def of(cls, p: Path) -> "Side":
        try:
            st = p.stat()
        except OSError:
            return cls(p)
        return cls(p, True, st.st_size, st.st_mtime, fsutil.hydration(p, st) == "cloud")

    def sig(self) -> list[Any]:
        return [self.size, round(self.mtime or 0, 3)]

    def sha(self, allow_download: bool) -> str | None:
        """Content hash; None if reading would download a cloud-only file and that is not allowed."""
        if self._sha is None and self.exists:
            if self.cloud and not allow_download:
                return None
            self._sha = sha256_file(self.path)
        return self._sha


@dataclass
class Pair:
    module: str
    local_rel: str              # relative to the local root
    remote_rel: str             # relative to the remote root
    local: Side
    remote: Side
    base: dict[str, Any] | None
    action: str = ""            # copy | in_sync | conflict | one_side | skip
    reason: str = ""
    pull_only: bool = False
    delivery: bool = False

    def to_json(self) -> dict[str, Any]:
        return {"module": self.module, "local": self.local_rel, "remote": self.remote_rel, "action": self.action,
                "reason": self.reason, "bytes": (self.remote if self.action == "copy" else self.local).size,
                "cloud": self.remote.cloud}


@dataclass
class State:
    path: Path
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path) -> "State":
        p = root / STATE_FILE
        data = fsutil.read_json(p) if p.is_file() else None
        data = data if isinstance(data, dict) else {}
        data.setdefault("schema", 1)
        data.setdefault("files", {})
        return cls(p, data)

    def base(self, local_rel: str) -> dict[str, Any] | None:
        return self.data["files"].get(local_rel)

    def record(self, pair: Pair, sha: str) -> None:
        l, r = Side.of(pair.local.path), Side.of(pair.remote.path)
        self.data["files"][pair.local_rel] = {"sha256": sha, "remote": pair.remote_rel, "local_sig": l.sig(), "remote_sig": r.sig()}

    def save(self, direction: str, remote: Path) -> None:
        self.data["remote"] = str(remote)
        if direction:
            self.data[f"last_{direction}"] = utcnow()
        fsutil.write_json(self.path, self.data)


def _walk(base: Path) -> list[str]:
    out = []
    if not base.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if not is_noise(d) and not d.startswith(".")]
        for f in filenames:
            if is_noise(f) or f.startswith("."):
                continue
            out.append(str((Path(dirpath) / f).relative_to(base)))
    return sorted(out)


def plan(root: Path, remote: Path, modules: list[str] | None, state: State, delivery_dir: str
         ) -> tuple[list[Pair], list[str], list[str]]:
    """Every file that maps between the two sides, plus remote files that map to nothing and conflict copies."""
    remote_modules = sorted(d.name for d in remote.iterdir() if d.is_dir() and MODULE_RE.match(d.name)) if remote.is_dir() else []
    local_modules = sorted(d.name for d in root.iterdir() if d.is_dir() and MODULE_RE.match(d.name))
    wanted = sorted(set(remote_modules) | set(local_modules))
    if modules:
        wanted = [m for m in wanted if m in modules]
    pairs: dict[str, Pair] = {}
    ignored: list[str] = []
    conflicts: list[str] = []
    # Remote names recorded at the last sync win over the default push names.
    recorded = {k: v["remote"] for k, v in state.data["files"].items()}

    for m in wanted:
        rbase, lbase = remote / m, root / m
        rfiles = _walk(rbase)
        mapped_names = set()
        for rel in rfiles:
            if rel.startswith(delivery_dir + "/"):
                continue  # our own packages: pushed, never pulled back
            lrel = remote_to_local(m, rel)
            if lrel:
                mapped_names.add(Path(rel).name)
                key = f"{m}/{lrel}"
                pairs[key] = Pair(m, key, f"{m}/{rel}", Side.of(lbase / lrel), Side.of(rbase / rel), state.base(key),
                                  pull_only=bool(PULL_ONLY.search("/" + lrel)))
        for rel in rfiles:
            if rel.startswith(delivery_dir + "/") or remote_to_local(m, rel):
                continue
            c = conflict_of(Path(rel).name, mapped_names)
            (conflicts if c else ignored).append(f"{m}/{rel}")
        for rel in _walk(lbase):
            key = f"{m}/{rel}"
            if key in pairs or rel.startswith("build/") or "/build/" in rel or "/out/" in rel:
                continue
            if PULL_ONLY.search("/" + rel):
                continue
            rrel = recorded.get(key) or (f"{m}/{local_to_remote(m, rel)}" if local_to_remote(m, rel) else None)
            if rrel:
                pairs[key] = Pair(m, key, rrel, Side.of(lbase / rel), Side.of(remote / rrel), state.base(key))
        # Finished packages: out/ -> <module>/<delivery_dir>/<topic id>/, push only.
        for manifest in sorted(lbase.glob("U*/T*/out/manifest.json")):
            tdir = manifest.parent.parent
            tid = f"{m}-{tdir.parent.name}-{tdir.name}"
            for f in sorted(manifest.parent.iterdir()):
                if f.is_file() and not f.name.startswith("."):
                    key = str(f.relative_to(root))
                    rrel = f"{m}/{delivery_dir}/{tid}/{f.name}"
                    pairs[key] = Pair(m, key, rrel, Side.of(f), Side.of(remote / rrel), state.base(key), delivery=True)
    return sorted(pairs.values(), key=lambda p: p.local_rel), ignored, conflicts


def decide(pair: Pair, direction: str, dry_run: bool) -> None:
    """Three-way decision against the last synced version. Sets pair.action and pair.reason."""
    src, dst = (pair.remote, pair.local) if direction == "pull" else (pair.local, pair.remote)
    here, there = ("OneDrive", "local") if direction == "pull" else ("local", "OneDrive")
    if direction == "pull" and pair.delivery:
        pair.action, pair.reason = "skip", "delivery packages are pushed, never pulled"
        return
    if direction == "push" and pair.pull_only:
        pair.action, pair.reason = "skip", "media comes from the editor or translator and is never pushed back"
        return
    if not src.exists and not dst.exists:
        pair.action = "skip"
        return
    if not src.exists:
        pair.action, pair.reason = "one_side", f"only {there}"
        return
    b = pair.base
    src_sig = pair.remote.sig() if direction == "pull" else pair.local.sig()
    dst_sig = pair.local.sig() if direction == "pull" else pair.remote.sig()
    b_src = (b or {}).get("remote_sig" if direction == "pull" else "local_sig")
    b_dst = (b or {}).get("local_sig" if direction == "pull" else "remote_sig")

    def changed(side: Side, sig: list[Any], base_sig: Any) -> bool | None:
        """True/False, or None when only a download could tell."""
        if not b:
            return True
        if sig == base_sig:
            return False
        h = side.sha(allow_download=not dry_run)
        return None if h is None else h != b["sha256"]

    if not dst.exists:
        pair.action, pair.reason = "copy", f"new in {here}" if not b else f"missing in {there}"
        return
    if not b:
        # First sync with the file on both sides: identical is fine, anything else is a conflict.
        hs, hd = src.sha(not dry_run), dst.sha(not dry_run)
        if hs is not None and hs == hd:
            pair.action, pair.reason = "in_sync", "identical"
        elif hs is None or hd is None:
            pair.action, pair.reason = ("conflict", "on both sides, never synced, sizes differ") if src.size != dst.size \
                else ("check", "on both sides, never synced; compared when it runs")
        else:
            pair.action, pair.reason = "conflict", "on both sides with different contents, never synced"
        return
    # A dry run cannot always tell (it never downloads): None means "only a download would say".
    sc = changed(src, src_sig, b_src)
    dc = changed(dst, dst_sig, b_dst)
    if sc is False and dc is False:
        pair.action = "in_sync"
    elif sc is False:
        pair.action, pair.reason = "skip", f"changed only in {there}"
    elif dc is False:
        pair.action, pair.reason = "copy", f"changed in {here}" if sc else f"may have changed in {here}"
    elif sc and dc:
        same = src.sha(not dry_run) == dst.sha(not dry_run)
        pair.action, pair.reason = ("in_sync", "same change on both sides") if same else \
            ("conflict", "changed on both sides since the last sync")
    else:
        pair.action, pair.reason = "check", "may have changed on both sides; compared when it runs"
