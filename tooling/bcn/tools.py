"""External binaries: where they are, and whether they are the pinned versions.

Checked before a command starts work, so a missing or drifted binary fails at
second zero rather than part way through a ninety-topic run.
"""

from __future__ import annotations

import json
import plistlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import TOOLING_DIR, Config
from .envelope import Fail

# The pins. Change deliberately, test against a real topic, and note it.
FFMPEG_VERSION = "7.1"          # major.minor; patch releases allowed
NODE_MAJOR = 22
MARP_CLI_VERSION = "4.5.1"      # also pinned in tooling/node/package.json
MARP_CORE_VERSION = "4.4.0"
CHROME_VERSION = "154.0.8037.57"  # Chrome for Testing, installed under tooling/vendor

NODE_DIR = TOOLING_DIR / "node"
VENDOR_DIR = TOOLING_DIR / "vendor"


@dataclass
class Tools:
    ffmpeg: str | None = None
    ffprobe: str | None = None
    node: str | None = None
    marp: str | None = None
    chrome: str | None = None
    versions: dict[str, str] | None = None


def _version_of(cmd: list[str], rx: str) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(rx, out)
    return m.group(1) if m else None


def _default_chrome() -> str | None:
    hits = sorted(VENDOR_DIR.glob(f"chrome/chrome/*-{CHROME_VERSION}/*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"))
    if hits:
        return str(hits[0])
    hits = sorted(VENDOR_DIR.glob(f"chrome/chrome/*-{CHROME_VERSION}/*/chrome"))
    return str(hits[0]) if hits else None


def chrome_version(path: str) -> str | None:
    """Read the version from the app bundle rather than launching the browser."""
    p = Path(path)
    for parent in p.parents:
        if parent.suffix == ".app":
            try:
                with open(parent / "Contents" / "Info.plist", "rb") as f:
                    return plistlib.load(f).get("CFBundleShortVersionString")
            except OSError:
                return None
    return _version_of([path, "--version"], r"(\d+\.\d+\.\d+\.\d+)")


def locate(cfg: Config) -> Tools:
    t = cfg["tools"]
    return Tools(
        ffmpeg=t["ffmpeg"] or shutil.which("ffmpeg"),
        ffprobe=t["ffprobe"] or shutil.which("ffprobe"),
        node=t["node"] or shutil.which("node"),
        marp=str(NODE_DIR / "node_modules" / "@marp-team" / "marp-cli" / "marp-cli.js"),
        chrome=t["chrome"] or _default_chrome(),
    )


def require(cfg: Config, *names: str) -> Tools:
    """Fail with TOOL_MISSING / TOOL_VERSION unless every named tool is present and pinned."""
    tools = locate(cfg)
    versions: dict[str, str] = {}
    for name in names:
        if name in ("ffmpeg", "ffprobe"):
            path = getattr(tools, name)
            if not path:
                raise Fail("TOOL_MISSING", f"{name} not found on PATH.", hint=f"brew install ffmpeg (pinned to {FFMPEG_VERSION}.x).")
            v = _version_of([path, "-version"], rf"{name} version n?(\d+\.\d+(?:\.\d+)?)")
            if not v or not (v == FFMPEG_VERSION or v.startswith(FFMPEG_VERSION + ".")):
                raise Fail("TOOL_VERSION", f"{name} is version {v}; the pipeline is pinned to {FFMPEG_VERSION}.x.",
                           hint="A major bump can change filter behaviour. Install the pinned version or re-pin deliberately.")
            versions[name] = v
        elif name == "marp":
            if not tools.node:
                raise Fail("TOOL_MISSING", "node not found on PATH.", hint=f"Install Node {NODE_MAJOR}.")
            nv = _version_of([tools.node, "--version"], r"v(\d+\.\d+\.\d+)")
            if not nv or int(nv.split(".")[0]) != NODE_MAJOR:
                raise Fail("TOOL_VERSION", f"node is {nv}; the pipeline is pinned to Node {NODE_MAJOR}.")
            versions["node"] = nv
            for pkg, want in (("@marp-team/marp-cli", MARP_CLI_VERSION), ("@marp-team/marp-core", MARP_CORE_VERSION)):
                pj = NODE_DIR / "node_modules" / pkg / "package.json"
                if not pj.is_file():
                    raise Fail("TOOL_MISSING", f"{pkg} is not installed in {NODE_DIR}.",
                               hint="Run scripts/setup.sh (npm ci in tooling/node).")
                got = json.loads(pj.read_text())["version"]
                if got != want:
                    raise Fail("TOOL_VERSION", f"{pkg} is {got}; pinned to {want}.", hint="Run npm ci in tooling/node.")
                versions[pkg.split("/")[1]] = got
        elif name == "chrome":
            if not tools.chrome or not Path(tools.chrome).exists():
                raise Fail("TOOL_MISSING", f"Chrome for Testing {CHROME_VERSION} is not installed.",
                           hint="Run scripts/setup.sh; Chromium must be installed ahead of time, never fetched at run time.")
            cv = chrome_version(tools.chrome)
            if cv != CHROME_VERSION:
                raise Fail("TOOL_VERSION", f"Chrome at {tools.chrome} is {cv}; pinned to {CHROME_VERSION}.")
            versions["chrome"] = cv
    tools.versions = versions
    return tools
