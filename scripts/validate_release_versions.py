#!/usr/bin/env python3
"""Validate unified release metadata for the two maintained skills."""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("rtl-style", "py-hosttool")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
CURRENT_RELEASE = re.compile(
    r"^[ \t]*Current release[ \t]*:[ \t]*v?([0-9]+\.[0-9]+\.[0-9]+)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


def _read_version(path: Path, label: str, errors: list[str]) -> str | None:
    if not path.is_file():
        errors.append(f"missing: {label}")
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.append(f"{label} cannot be read: {exc}")
        return None
    value = raw[:-1] if raw.endswith("\n") else raw
    if value.endswith("\r"):
        value = value[:-1]
    if "\r" in value or "\n" in value or not SEMVER.fullmatch(value):
        errors.append(f"{label} must contain only MAJOR.MINOR.PATCH SemVer")
        return None
    return value


def validate(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    release = _read_version(root / "VERSION", "VERSION", errors)
    skill_versions: dict[str, str] = {}
    for skill in SKILLS:
        version = _read_version(root / skill / "VERSION", f"{skill}/VERSION", errors)
        if version:
            skill_versions[skill] = version
            if release and version != release:
                errors.append(f"{skill}/VERSION does not match root VERSION")

        readme = root / skill / "README.md"
        if not readme.is_file():
            errors.append(f"missing: {skill}/README.md")
        elif version:
            try:
                declared = CURRENT_RELEASE.findall(readme.read_text(encoding="utf-8"))
            except (OSError, UnicodeError) as exc:
                errors.append(f"{skill}/README.md cannot be read: {exc}")
            else:
                if declared != [version]:
                    errors.append(f"{skill}/README.md current release does not match VERSION")

        changelog = root / skill / "CHANGELOG.md"
        if not changelog.is_file():
            errors.append(f"missing: {skill}/CHANGELOG.md")
        elif version:
            try:
                text = changelog.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                errors.append(f"{skill}/CHANGELOG.md cannot be read: {exc}")
            else:
                heading = re.compile(
                    rf"^##\s+\[{re.escape(version)}\](?:\s+-\s+\d{{4}}-\d{{2}}-\d{{2}})?\s*$",
                    re.MULTILINE,
                )
                if not heading.search(text):
                    errors.append(f"{skill}/CHANGELOG.md missing release heading: {version}")

    if (root / "deepseek-subagent").exists():
        errors.append("stopped skill still exists: deepseek-subagent")

    root_readme = root / "README.md"
    if not root_readme.is_file():
        errors.append("missing: README.md")
    elif skill_versions:
        try:
            text = root_readme.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"README.md cannot be read: {exc}")
        else:
            for skill, version in skill_versions.items():
                pattern = re.compile(
                    rf"\[`{re.escape(skill)}`\]\(\./{re.escape(skill)}\).*?\*\*{re.escape(version)}\*\*"
                )
                if not pattern.search(text):
                    errors.append(f"README.md missing current {skill} version: {version}")
            if re.search(r"\[[^\]]+\]\(\./deepseek-subagent\)", text):
                errors.append("README.md still links to stopped skill: deepseek-subagent")

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("repository release metadata validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("repository release metadata validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
