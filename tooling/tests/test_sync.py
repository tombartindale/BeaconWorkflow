"""bcn sync: name mapping and the three-way pull/push rules."""

import json
import os
import time
from pathlib import Path

from bcn.cli import main
from bcn.sync import local_to_remote, remote_to_local

from .conftest import age, topic_md


def bcn(capsys, *args):
    code = main([*args, "--quiet"])
    return code, json.loads(capsys.readouterr().out)


def test_name_mapping():
    assert remote_to_local("KV7016", "KV7016-U01-T01.md") == "U01/T01/topic.md"
    assert remote_to_local("KV7016", "KV7016-U01-T01.zh.srt") == "U01/T01/edit/master.zh.srt"
    assert remote_to_local("KV7016", "KV7016-course-map.md") == "course-map.md"
    assert remote_to_local("KV7016", "asset-requests.md") == "assets.md"
    assert remote_to_local("KV7016", "KV7016-U01-T01-assets/fig.png") == "U01/T01/assets/fig.png"
    assert remote_to_local("KV7016", "U01/T01/topic.md") == "U01/T01/topic.md"
    assert remote_to_local("KV7016", "Module_Specification_-_KV7016.docx") is None
    assert remote_to_local("KV7016", "KV7099-U01-T01.md") is None  # another module's file
    assert local_to_remote("KV7016", "U01/T01/topic.md") == "KV7016-U01-T01.md"
    assert local_to_remote("KV7016", "U01/T01/edit/master.mp4") is None  # media is never pushed


def _remote(tmp_path: Path) -> Path:
    r = tmp_path / "OneDrive" / "Module Development"
    (r / "KV7015").mkdir(parents=True)
    (r / "KV7015" / "KV7015-U01-T01.md").write_text(topic_md())
    (r / "KV7015" / "Module_Specification.docx").write_text("x")
    for f in (r / "KV7015").iterdir():
        age(f, 600)
    return r


def test_init_pull_push_and_conflict(tmp_path, capsys):
    remote = _remote(tmp_path)
    local = tmp_path / "local"
    code, env = bcn(capsys, "sync", str(local), "--pull", "--init", "--remote", str(remote))
    assert code == 0 and env["counts"]["copy"] == 1
    assert env["ignored"] == ["KV7015/Module_Specification.docx"]
    topic = local / "KV7015/U01/T01/topic.md"
    assert topic.read_text() == topic_md()
    assert time.time() - topic.stat().st_mtime < 60, "pulled files must look new to the pipeline"

    # Changed locally only: push copies it under the name it was pulled from.
    topic.write_text(topic_md() + "\nlocal\n")
    age(topic, 30)
    code, env = bcn(capsys, "sync", str(local), "--push")
    assert code == 0 and env["counts"]["copy"] == 1
    assert (remote / "KV7015/KV7015-U01-T01.md").read_text().endswith("local\n")

    # Changed on both sides: a conflict, nothing overwritten, until a side is preferred.
    topic.write_text("local 2\n")
    (remote / "KV7015/KV7015-U01-T01.md").write_text("remote 2\n")
    age(topic, 20)
    age(remote / "KV7015/KV7015-U01-T01.md", 20)
    code, env = bcn(capsys, "sync", str(local), "--pull")
    assert code == 1 and env["counts"]["conflict"] == 1 and topic.read_text() == "local 2\n"
    code, env = bcn(capsys, "sync", str(local), "--pull", "--prefer", "remote", "--only", "KV7015/U01/T01/topic.md")
    assert code == 0 and topic.read_text() == "remote 2\n"


def test_dry_run_writes_nothing(tmp_path, capsys):
    remote = _remote(tmp_path)
    local = tmp_path / "local"
    bcn(capsys, "sync", str(local), "--pull", "--init", "--remote", str(remote), "--dry-run")
    assert not (local / "KV7015").exists() and not (local / "sync-state.json").exists()


def test_deletions_never_sync(tmp_path, capsys):
    remote = _remote(tmp_path)
    local = tmp_path / "local"
    bcn(capsys, "sync", str(local), "--pull", "--init", "--remote", str(remote))
    os.remove(remote / "KV7015/KV7015-U01-T01.md")
    code, env = bcn(capsys, "sync", str(local), "--pull")
    assert (local / "KV7015/U01/T01/topic.md").is_file()
    assert env["counts"]["one_side"] == 1
