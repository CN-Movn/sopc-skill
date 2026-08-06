"""当前固定使用的 OpenCode Go Provider。"""

from .opencode_go import OpenCodeGoProvider


def get_provider():
    return OpenCodeGoProvider


__all__ = ["OpenCodeGoProvider", "get_provider"]
