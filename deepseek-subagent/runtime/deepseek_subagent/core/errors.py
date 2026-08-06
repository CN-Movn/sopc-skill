"""通用错误模型与弃用提示。"""

from __future__ import annotations

import sys
from typing import Any


class ManagerError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def deprecation_warning(text: str) -> None:
    print(f"deprecation: {text}", file=sys.stderr)
