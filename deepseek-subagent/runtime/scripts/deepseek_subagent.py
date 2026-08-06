#!/usr/bin/env python3
"""deepseek-subagent 管理程序入口。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deepseek_subagent.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
