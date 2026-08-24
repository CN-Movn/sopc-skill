#!/usr/bin/env python3
"""Validate repository-level Skill version declarations.

Each Skill keeps its own VERSION as the authoritative current version. This
repository-level check only verifies that the root README's "当前 Skill" table
stays synchronized with those VERSION files.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("rtl-style", "py-hosttool", "deepseek-subagent")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _read_version(root: Path, skill: str, errors: list[str]) -> str | None:
    path = root / skill / "VERSION"
    if not path.is_file():
        errors.append(f"{skill}: missing VERSION")
        return None
    try:
        version = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        errors.append(f"{skill}: VERSION cannot be read: {exc}")
        return None
    if not SEMVER.fullmatch(version):
        errors.append(f"{skill}: VERSION is not MAJOR.MINOR.PATCH SemVer")
        return None
    return version


def _readme_declared_version(readme: str, skill: str, errors: list[str]) -> str | None:
    pattern = re.compile(
        rf"^\|\s*\[`{re.escape(skill)}`\]\(\./{re.escape(skill)}\)\s*"
        rf"\|\s*\*\*v([0-9]+\.[0-9]+\.[0-9]+)\*\*\s*\|",
        re.MULTILINE,
    )
    matches = pattern.findall(readme)
    if not matches:
        errors.append(f"README.md missing current Skill row: {skill}")
        return None
    if len(matches) != 1:
        errors.append(f"README.md has multiple current Skill rows: {skill}")
        return None
    return matches[0]


def validate(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    readme_path = root / "README.md"
    if not readme_path.is_file():
        return ["missing: README.md"]
    try:
        readme = readme_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [f"README.md cannot be read: {exc}"]

    for skill in SKILLS:
        version = _read_version(root, skill, errors)
        declared = _readme_declared_version(readme, skill, errors)
        if version is not None and declared is not None and declared != version:
            errors.append(
                f"README.md version mismatch for {skill}: declared {declared}, VERSION {version}"
            )

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("repository release version validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("repository release version validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
