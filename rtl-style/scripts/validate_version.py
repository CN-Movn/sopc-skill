#!/usr/bin/env python3
"""Validate rtl-style release metadata without inspecting RTL behavior."""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
CURRENT_README = re.compile(
    r"^[ \t]*(?:Current release|Current Version)[ \t]*:[ \t]*v?([0-9]+\.[0-9]+\.[0-9]+)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


def _version_value(path: Path, errors: list[str]) -> str | None:
    if not path.is_file():
        errors.append("missing: VERSION")
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.append(f"VERSION cannot be read: {exc}")
        return None
    value = raw
    if value.endswith("\n"):
        value = value[:-1]
        if value.endswith("\r"):
            value = value[:-1]
    if "\r" in value or "\n" in value or not SEMVER.fullmatch(value):
        errors.append("VERSION must contain only MAJOR.MINOR.PATCH SemVer")
        return None
    return value


def validate(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    version = _version_value(root / "VERSION", errors)

    changelog = root / "CHANGELOG.md"
    if not changelog.is_file():
        errors.append("missing: CHANGELOG.md")
    elif version:
        try:
            text = changelog.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"CHANGELOG.md cannot be read: {exc}")
        else:
            title = re.compile(
                rf"^##\s+(?:\[{re.escape(version)}\]|{re.escape(version)})"
                rf"(?:\s+-\s+\d{{4}}-\d{{2}}-\d{{2}})?\s*$",
                re.MULTILINE,
            )
            if not title.search(text):
                errors.append(f"CHANGELOG.md missing current version title: {version}")

    readme = root / "README.md"
    if readme.is_file() and version:
        try:
            text = readme.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"README.md cannot be read: {exc}")
        else:
            declared = CURRENT_README.findall(text)
            if any(item != version for item in declared):
                errors.append("README.md current release does not match VERSION")

    manifest = root / "manifest.txt"
    if manifest.is_file():
        try:
            listed = {
                line.strip().replace("\\", "/")
                for line in manifest.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
            actual = {
                path.relative_to(root).as_posix()
                for path in root.rglob("*")
                if path.is_file()
            }
        except (OSError, UnicodeError) as exc:
            errors.append(f"manifest.txt cannot be read: {exc}")
        else:
            missing = sorted(actual - listed)
            stale = sorted(listed - actual)
            if missing:
                errors.append("manifest missing: " + ", ".join(missing[:8]))
            if stale:
                errors.append("manifest stale: " + ", ".join(stale[:8]))
            if "VERSION" not in listed:
                errors.append("manifest missing required release file: VERSION")
            if "CHANGELOG.md" not in listed:
                errors.append("manifest missing required release file: CHANGELOG.md")

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("rtl-style version metadata validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("rtl-style version metadata validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
