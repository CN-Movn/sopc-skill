#!/usr/bin/env python3
"""Print the private localhost bridge token for Codex auth.command."""

from __future__ import annotations

from pathlib import Path


def main() -> int:
    token_file = Path(__file__).resolve().parents[2] / ".local" / "local-bridge-token.txt"
    token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit("local bridge token is unavailable")
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
