"""固定的 Codex Adapter 与 OpenCode Go Provider 入口。"""

from .platforms.codex.adapter import CodexAdapter
from .providers import OpenCodeGoProvider

CODEX_ADAPTER = CodexAdapter()


def get_platform() -> CodexAdapter:
    return CODEX_ADAPTER


def get_provider_definition():
    return OpenCodeGoProvider


__all__ = ["CODEX_ADAPTER", "OpenCodeGoProvider", "get_platform", "get_provider_definition"]
