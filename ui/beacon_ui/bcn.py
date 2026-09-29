"""Running bcn. The backend is the only thing that does.

Read-only queries (status, show, diagnostics, review listing, codes, doctor) run
synchronously here. Anything that changes files goes through the job queue.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

QUERY_TIMEOUT = 180


class BcnError(Exception):
    pass


def locate(explicit: str | None) -> list[str]:
    """The command prefix that runs bcn."""
    if explicit:
        return [explicit]
    if os.environ.get("BCN"):
        return [os.environ["BCN"]]
    sibling = Path(__file__).resolve().parents[2] / "tooling" / ".venv" / "bin" / "bcn"
    if sibling.is_file():
        return [str(sibling)]
    found = shutil.which("bcn")
    if found:
        return [found]
    return [sys.executable, "-m", "bcn"]


class Bcn:
    def __init__(self, prefix: list[str], root: Path) -> None:
        self.prefix = prefix
        self.root = root

    def argv(self, command: str, *args: str) -> list[str]:
        return [*self.prefix, command, *args, "--quiet"]

    def query(self, command: str, *args: str, timeout: float = QUERY_TIMEOUT) -> dict[str, Any]:
        """Run a read-only command and return its envelope, whatever its exit code."""
        try:
            p = subprocess.run(self.argv(command, *args), capture_output=True, text=True, timeout=timeout,
                               stdin=subprocess.DEVNULL, cwd=self.root)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise BcnError(f"bcn {command} could not run: {e}") from e
        try:
            env = json.loads(p.stdout)
        except ValueError as e:
            raise BcnError(f"bcn {command} printed no envelope (exit {p.returncode}): {p.stderr[-500:]}") from e
        env["exit_code"] = p.returncode
        return env
