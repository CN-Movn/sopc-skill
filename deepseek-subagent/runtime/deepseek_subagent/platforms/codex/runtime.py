"""Codex 桌面运行时探测（CodexRuntimeDetector）。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from ...core.errors import ManagerError

DESKTOP_BIN_ENV = "CODEX_DESKTOP_BIN"


class CodexRuntimeDetector:
    @staticmethod
    def candidates() -> tuple[Path, ...]:
        if sys.platform == "darwin":
            return (
                Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
                Path("/Applications/Codex.app/Contents/Resources/codex"),
            )
        if sys.platform == "win32":
            candidates: list[Path] = []
            discovered = shutil.which("codex.exe") or shutil.which("codex")
            if discovered:
                candidates.append(Path(discovered))

            program_files = os.environ.get("ProgramFiles")
            if program_files:
                windows_apps = Path(program_files) / "WindowsApps"
                try:
                    packages = sorted(
                        windows_apps.glob("OpenAI.Codex_*_x64__*"),
                        key=lambda item: item.stat().st_mtime_ns,
                        reverse=True,
                    )
                except OSError:
                    packages = []
                candidates.extend(package / "app" / "resources" / "codex.exe" for package in packages)

            unique: list[Path] = []
            seen: set[str] = set()
            for candidate in candidates:
                normalized = os.path.normcase(os.path.abspath(str(candidate)))
                if normalized not in seen:
                    seen.add(normalized)
                    unique.append(candidate)
            return tuple(unique)
        return ()

    @staticmethod
    def find() -> str | None:
        configured = os.environ.get(DESKTOP_BIN_ENV)
        candidates = (Path(configured).expanduser(),) if configured else CodexRuntimeDetector.candidates()
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate.resolve())
        return None

    @staticmethod
    def require() -> str:
        found = CodexRuntimeDetector.find()
        if found is None:
            raise ManagerError(
                "desktop_codex_missing",
                "没有找到 Codex 桌面应用内置运行时。请先安装或启动桌面应用，或设置 CODEX_DESKTOP_BIN。",
            )
        return found

    @staticmethod
    def version(codex_bin: str) -> str:
        try:
            proc = subprocess.run([codex_bin, "--version"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ManagerError("codex_version_unknown", "无法读取 Codex 桌面应用内置运行时版本。") from exc
        text = f"{proc.stdout}\n{proc.stderr}".strip()
        if proc.returncode != 0 or not text:
            raise ManagerError("codex_version_unknown", "无法读取 Codex 桌面应用内置运行时版本。")
        return text
