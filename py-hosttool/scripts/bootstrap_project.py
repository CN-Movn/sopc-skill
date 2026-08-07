#!/usr/bin/env python3
"""Copy the starter template and replace project identity placeholders."""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = SKILL_ROOT / "assets" / "template" / "py_hosttool_template"
TEXT_SUFFIXES = {".py", ".md", ".txt", ".bat", ".spec", ".gitignore"}


def exe_name(app_name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", app_name.strip())
    return value.strip("._") or "HostTool"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a PySide6 host-tool project")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--layout", choices=("workbench", "dashboard"), default="workbench")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    destination = args.destination.expanduser().resolve()
    if destination.exists() and any(destination.iterdir()) and not args.force:
        parser.error(f"destination is not empty: {destination}")
    if destination.exists() and args.force:
        shutil.rmtree(destination)
    shutil.copytree(TEMPLATE, destination)

    replacements = {
        "{{APP_NAME}}": args.app_name,
        "{{APP_VERSION}}": args.version,
        "{{EXE_NAME}}": exe_name(args.app_name),
        "{{LAYOUT}}": args.layout,
    }
    for path in destination.rglob("*"):
        if not path.is_file() or (path.suffix not in TEXT_SUFFIXES and path.name != ".gitignore"):
            continue
        text = path.read_text(encoding="utf-8")
        for old, new in replacements.items():
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8", newline="\n")

    print(f"Created {args.app_name} {args.version} at {destination}")
    print(f"Layout: {args.layout}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
