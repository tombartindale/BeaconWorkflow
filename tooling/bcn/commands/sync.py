"""bcn sync: pull from, or push to, the shared folder (OneDrive/SharePoint).

  bcn sync <root> --pull [--dry-run]      shared folder -> local working copy
  bcn sync <root> --push [--dry-run]      local sources and finished packages -> shared folder
  bcn sync <new root> --pull --init --remote "<shared folder>"   create a local root and pull into it

A path below the root narrows the sync to that module, unit or topic. A conflict
(changed on both sides) is never overwritten unless --prefer says which side
wins, optionally limited to particular files with --only.

sync-state.json in the local root is the record that makes the three-way
comparison possible. Like status, sync writes no build/ step file.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from .. import fsutil, progress
from .. import sync as S
from ..config import load
from ..envelope import Cancelled, Diagnostic, Envelope, Fail, TopicResult
from ..progress import Progress
from ..tree import Target

HELP = "pull from or push to the shared folder"


def add_args(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--pull", action="store_true", help="shared folder -> local")
    g.add_argument("--push", action="store_true", help="local -> shared folder")
    p.add_argument("--dry-run", action="store_true", help="report what would happen; never downloads")
    p.add_argument("--prefer", choices=["remote", "local"], help="resolve conflicts in favour of this side")
    p.add_argument("--only", action="append", default=[], metavar="PATH",
                   help="limit to these local paths (relative to the root); repeatable")
    p.add_argument("--remote", help="shared folder, overriding [sync] remote in programme.toml")
    p.add_argument("--init", action="store_true", help="create the local root if it does not exist")


def prepare(args: argparse.Namespace) -> None:
    """Runs before the path is resolved, so --init can create the root."""
    if not args.init:
        return
    root = Path(args.path).expanduser()
    if not args.remote:
        raise Fail("USAGE", "--init needs --remote, the shared folder to sync with.")
    remote = Path(args.remote).expanduser()
    if not remote.is_dir():
        raise Fail("SYNC_REMOTE_MISSING", f"{remote} is not a folder.")
    if (root / "programme.toml").is_file():
        return
    if root.exists() and any(root.iterdir()):
        raise Fail("USAGE", f"{root} exists and is not empty; --init only creates a new programme root.")
    root.mkdir(parents=True, exist_ok=True)
    remote_toml = str(remote.resolve()).replace("\\", "\\\\").replace('"', '\\"')
    (root / "programme.toml").write_text(
        "# Beacon programme configuration. Unset values take the defaults in tooling/bcn/config.py.\n\n"
        "[programme]\nname = \"\"\ntheme = \"default\"\n\n"
        "[sync]\n# The shared folder holding one folder per module. bcn sync pulls from and pushes to it.\n"
        f"remote = \"{remote_toml}\"\n", encoding="utf-8")


def _copy(pair: S.Pair, direction: str) -> str:
    src, dst = (pair.remote.path, pair.local.path) if direction == "pull" else (pair.local.path, pair.remote.path)
    # Reading a cloud-only source is what downloads it; that is the point of pulling.
    with fsutil.atomic_path(dst) as tmp:
        shutil.copyfile(src, tmp)
    # copyfile leaves the new file with the current time as its mtime: the pipeline must
    # see pulled content as new, whatever time the shared copy carries.
    return S.sha256_file(dst)


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    direction = "pull" if args.pull else "push"
    cfg = load(target.root)
    remote_s = args.remote or cfg["sync"]["remote"]
    if not remote_s:
        raise Fail("SYNC_NOT_CONFIGURED", "No shared folder is set.", file="programme.toml",
                   hint='Add [sync] remote = "/path/to/shared/folder" to programme.toml, or pass --remote.')
    remote = Path(remote_s).expanduser()
    if not remote.is_dir():
        raise Fail("SYNC_REMOTE_MISSING", f"The shared folder {remote} is not there.", file="programme.toml",
                   hint="Check OneDrive is running and signed in, and that the path in programme.toml is right.")

    state = S.State.load(target.root)
    scope = None if target.level == "root" else target.rel
    modules = [scope.split("/")[0]] if scope else None
    pairs, ignored, remote_conflicts = S.plan(target.root, remote, modules, state, cfg["sync"]["delivery_dir"])
    if scope:
        pairs = [p for p in pairs if p.local_rel == scope or p.local_rel.startswith(scope + "/")]
    if args.only:
        wanted = {o.strip("/") for o in args.only}
        pairs = [p for p in pairs if p.local_rel in wanted]

    for p in pairs:
        S.decide(p, direction, args.dry_run)
        winner = "remote" if direction == "pull" else "local"
        if p.action == "conflict" and args.prefer == winner:
            p.action, p.reason = "copy", f"conflict resolved in favour of {'OneDrive' if winner == 'remote' else 'local'}"

    for rc in remote_conflicts:
        env.diagnostics.append(Diagnostic("SYNC_REMOTE_CONFLICT_COPY", f"{rc} looks like a sync conflict copy in the shared folder; it is ignored.",
                                          file=rc, hint="Decide which version is right in the shared folder and delete the other."))

    copies = [p for p in pairs if p.action == "copy"]
    done: set[str] = set()
    if not args.dry_run:
        with Progress("sync", len(copies)) as prog:
            for p in copies:
                if progress.CANCEL.is_set():
                    env.cancelled = True
                    break
                with prog.topic(p.local_rel) as tp:
                    src = p.remote if direction == "pull" else p.local
                    if fsutil.recently_modified(src.path, 2.0):
                        p.action, p.reason = "skip", "still changing; try again in a moment"
                        env.diagnostics.append(Diagnostic("SYNC_UNSTABLE", f"{p.remote_rel if direction == 'pull' else p.local_rel} changed in the last two seconds; skipped.",
                                                          file=p.local_rel))
                        continue
                    tp.update(10, "downloading" if src.cloud else "copying")
                    try:
                        sha = _copy(p, direction)
                    except Cancelled:
                        env.cancelled = True
                        break
                    except OSError as e:
                        p.action, p.reason = "failed", str(e)
                        env.diagnostics.append(Diagnostic("SYNC_DOWNLOAD_FAILED" if src.cloud else "FS_CORRUPT",
                                                          f"Could not copy {p.remote_rel if direction == 'pull' else p.local_rel}: {e}",
                                                          file=p.local_rel, hint="If this machine is offline, try again when it is back online."))
                        continue
                    state.record(p, sha)
                    done.add(p.local_rel)
                    tp.update(100, "copied")
            prog.done(cancelled=env.cancelled)
        # Refresh the baseline for everything already identical, so later runs compare by stat alone.
        for p in pairs:
            if p.action == "in_sync":
                sha = (p.base or {}).get("sha256") or p.local.sha(True)
                if sha:
                    state.record(p, sha)
        state.save(direction, remote)

    by_module: dict[str, TopicResult] = {}
    counts = {"copy": 0, "conflict": 0, "one_side": 0, "in_sync": 0, "skip": 0, "check": 0, "failed": 0}
    for p in pairs:
        counts[p.action] = counts.get(p.action, 0) + 1
        r = by_module.setdefault(p.module, TopicResult(p.module, p.module))
        key = {"copy": "copied" if not args.dry_run else "to_copy"}.get(p.action, p.action)
        r.extra[key] = r.extra.get(key, 0) + 1
        if p.action == "conflict":
            r.diagnostics.append(Diagnostic(
                "SYNC_CONFLICT", f"{p.local_rel} {p.reason}.", file=p.local_rel,
                data={"local": p.local_rel, "remote": p.remote_rel},
                hint=f"Keep one version: bcn sync --{direction} --prefer {'remote' if direction == 'pull' else 'local'} --only {p.local_rel}, "
                     "or edit the other side to match."))
        elif p.action == "copy" and p.local_rel in done:
            r.diagnostics.append(Diagnostic("SYNC_COPIED", f"{'Pulled' if direction == 'pull' else 'Pushed'} {p.local_rel}.", file=p.local_rel))
    for r in by_module.values():
        r.ok = not any(d.level == "error" for d in r.diagnostics)
    env.results.extend(by_module[m] for m in sorted(by_module))
    env.extra.update({
        "direction": direction,
        "dry_run": bool(args.dry_run),
        "remote": str(remote),
        "counts": counts,
        "plan": [p.to_json() for p in pairs if p.action not in ("in_sync", "skip")],
        "ignored": ignored,
        "last_pull": state.data.get("last_pull"),
        "last_push": state.data.get("last_push"),
    })
