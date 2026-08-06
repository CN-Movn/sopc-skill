"""项目状态路径与宿主无关的默认位置。

状态（manifest、锁、备份）与平台安装目标完全分离：
- 状态默认位置：Windows %LOCALAPPDATA%\\deepseek-subagent，
  macOS ~/Library/Application Support/deepseek-subagent，
  Linux $XDG_STATE_HOME/deepseek-subagent（缺省 ~/.local/state/deepseek-subagent）。
- 可用 DEEPSEEK_SUBAGENT_STATE_HOME 环境变量或 CLI --state-home 覆盖。
- 平台安装目标由各平台适配器自行探测（如 Codex 的 CODEX_HOME / ~/.codex）。

manifest / 锁文件 / 备份目录位于状态根目录，不再依赖 CODEX_HOME。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_NAME = "deepseek-subagent"
LEGACY_PROJECT_NAME = "codex-deepseek-subagent"
STATE_ENV = "DEEPSEEK_SUBAGENT_STATE_HOME"
MANIFEST_FILE = "manifest.json"
LOCK_FILE = f"{PROJECT_NAME}.lock"
BACKUPS_DIR = "backups"


@dataclass(frozen=True)
class ProjectStatePaths:
    state_root: Path
    manifest: Path
    lock: Path
    backups: Path


def default_state_root() -> Path:
    override = os.environ.get(STATE_ENV)
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / PROJECT_NAME
        return Path.home() / "AppData" / "Local" / PROJECT_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / PROJECT_NAME
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / PROJECT_NAME
    return Path.home() / ".local" / "state" / PROJECT_NAME


def state_paths(state_home: str | Path | None = None) -> ProjectStatePaths:
    root = Path(state_home).expanduser().resolve() if state_home else default_state_root()
    return ProjectStatePaths(
        state_root=root,
        manifest=root / MANIFEST_FILE,
        lock=root / LOCK_FILE,
        backups=root / BACKUPS_DIR,
    )
