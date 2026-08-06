"""Codex 平台路径（config.toml / 模型目录 / 多角色 Agent 文件）。

平台安装目标由 CodexPaths.resolve 探测：CODEX_HOME 环境变量、
--codex-home 显式值，缺省 ~/.codex。Agent 文件按角色名生成，
支持多个角色（每个角色一个 agents/*.toml）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class CodexPaths:
    home: Path
    config: Path
    catalog: Path
    role_names: tuple[str, ...]
    agents: tuple[Path, ...]

    @staticmethod
    def from_home(home: Path, role_names: Iterable[str] | str = ("DeepSeek",)) -> "CodexPaths":
        names = tuple(role_names) if isinstance(role_names, (tuple, list)) else (role_names,)
        return CodexPaths(
            home=home,
            config=home / "config.toml",
            catalog=home / "models-with-deepseek.json",
            role_names=names,
            agents=tuple(home / "agents" / f"{name}.toml" for name in names),
        )

    @staticmethod
    def resolve(platform_home: str | None, role_names: Iterable[str] | str = ("DeepSeek",)) -> "CodexPaths":
        home = Path(platform_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()
        return CodexPaths.from_home(home, role_names)

    def agent_for(self, role_name: str) -> Path:
        return self.home / "agents" / f"{role_name}.toml"

    @property
    def agent(self) -> Path:
        return self.agents[0] if self.agents else self.agent_for("DeepSeek")

    def transaction_targets(self) -> tuple[Path, ...]:
        return (self.config, self.catalog, *self.agents)
