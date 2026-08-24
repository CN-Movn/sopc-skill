#!/usr/bin/env python3
"""Validate Agent Skill metadata, structure, artifacts and template syntax."""
from __future__ import annotations

import argparse
import ast
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from validate_version import validate as validate_version_metadata


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "VERSION",
    "SKILL.md",
    "README.md",
    "CHANGELOG.md",
    "review_questions.md",
    "references/ui_design_language.md",
    "references/window_chrome.md",
    "references/layout_patterns.md",
    "references/serial_logging.md",
    "references/architecture_workflow.md",
    "references/reuse_boundaries.md",
    "references/verification_and_delivery.md",
    "references/asset_catalog.md",
    "assets/template/py_hosttool_template/main.py",
    "assets/template/py_hosttool_template/HostTool.spec",
    "assets/template/py_hosttool_template/build.bat",
    "assets/template/py_hosttool_template/hosttool/config.py",
    "assets/template/py_hosttool_template/hosttool/serial_worker.py",
    "assets/template/py_hosttool_template/hosttool/serial_console.py",
    "assets/template/py_hosttool_template/tests/test_helpers.py",
    "assets/template/py_hosttool_template/tests/test_gui_smoke.py",
    "scripts/bootstrap_project.py",
    "scripts/tests/test_bootstrap_project.py",
)
FORBIDDEN_DIRS = {"__pycache__", ".pytest_cache", "build", "release", "dist"}
FORBIDDEN_SUFFIXES = {".exe", ".pyc", ".pyo", ".log"}
KNOWN_PLACEHOLDERS = (
    "{{APP_NAME}}", "{{APP_VERSION}}", "{{EXE_NAME}}", "{{LAYOUT}}",
    "{{APP_NAME_LITERAL}}", "{{APP_VERSION_LITERAL}}", "{{DEFAULT_BAUDRATE}}",
)
GENERATED_PROJECT_RESIDUES = ("MasterController_v1.4", "ArqMinSystem_v1.1")
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _frontmatter(skill: str) -> dict[str, str]:
    match = re.match(r"\A---\n(.*?)\n---(?:\n|\Z)", skill, flags=re.DOTALL)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for raw in match.group(1).splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key.strip()] = value
    return fields


def _clean_caches(root: Path) -> None:
    for cache in root.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def _validate_python_syntax(root: Path, label: str, errors: list[str]) -> None:
    """Parse Python sources and PyInstaller specs without writing bytecode."""
    paths = [*root.rglob("*.py"), *root.rglob("*.spec")]
    for path in paths:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError) as exc:
            errors.append(f"{label} Python syntax failed: {path.relative_to(root)}: {exc}")


def _validate_generated_projects(errors: list[str], notes: list[str],
                                 skipped: list[str]) -> None:
    bootstrap = ROOT / "scripts" / "bootstrap_project.py"
    runtime_modules = ("PySide6", "serial", "pytest")
    missing_runtime = [name for name in runtime_modules if importlib.util.find_spec(name) is None]
    run_gui_tests = not missing_runtime
    if missing_runtime:
        reason = "generated GUI pytest skipped: missing dependencies: " + ", ".join(missing_runtime)
        notes.append(reason)
        skipped.append(reason)

    with tempfile.TemporaryDirectory(prefix="py-hosttool-validate-") as temp:
        temp_root = Path(temp)
        for layout in ("workbench", "dashboard"):
            destination = temp_root / layout
            proc = subprocess.run(
                [
                    sys.executable, str(bootstrap), str(destination),
                    "--app-name", f"Validation-{layout}",
                    "--version", "9.9.9",
                    "--baudrate", "115200",
                    "--layout", layout,
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            if proc.returncode:
                errors.append(f"bootstrap failed for {layout}: {proc.stderr.strip() or proc.stdout.strip()}")
                continue
            for path in destination.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                unresolved = [token for token in KNOWN_PLACEHOLDERS if token in text]
                if unresolved:
                    errors.append(f"unresolved placeholder in generated {layout}: {path.relative_to(destination)}")
                residues = [token for token in GENERATED_PROJECT_RESIDUES if token in text]
                if residues:
                    errors.append(
                        f"source-project residue in generated {layout}: "
                        f"{path.relative_to(destination)} -> {', '.join(residues)}"
                    )
            _validate_python_syntax(destination, f"generated {layout}", errors)
            if run_gui_tests:
                env = os.environ.copy()
                env["QT_QPA_PLATFORM"] = "offscreen"
                proc = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                    cwd=destination, env=env, text=True, capture_output=True, check=False,
                )
                if proc.returncode:
                    output = proc.stdout.strip() or proc.stderr.strip()
                    errors.append(f"generated {layout} pytest failed: {output[-2000:]}")
                else:
                    notes.append(f"generated {layout} pytest passed")
            _clean_caches(destination)


def _validate_bootstrap_tests(errors: list[str], notes: list[str]) -> None:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", "scripts/tests", "-v"],
        cwd=ROOT, env=env, text=True, capture_output=True, check=False,
    )
    if proc.returncode:
        output = proc.stdout.strip() or proc.stderr.strip()
        errors.append(f"bootstrap regression tests failed: {output[-2000:]}")
    else:
        notes.append("bootstrap regression tests passed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the py-hosttool skill package")
    parser.add_argument(
        "--strict", action="store_true",
        help="return a non-zero status when runtime or reference checks are skipped",
    )
    args = parser.parse_args()
    errors: list[str] = []
    notes: list[str] = []
    skipped: list[str] = []
    for rel in REQUIRED:
        if not (ROOT / rel).is_file():
            errors.append(f"missing: {rel}")

    errors.extend(validate_version_metadata(ROOT))

    if (ROOT / "reference").exists():
        errors.append("legacy singular directory exists: reference/ (use references/)")

    for path in ROOT.rglob("*"):
        if path.is_dir() and path.name in FORBIDDEN_DIRS:
            errors.append(f"forbidden directory: {path.relative_to(ROOT)}")
        if path.is_file() and path.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f"forbidden file: {path.relative_to(ROOT)}")

    skill_path = ROOT / "SKILL.md"
    skill = skill_path.read_text(encoding="utf-8") if skill_path.exists() else ""
    fields = _frontmatter(skill)
    name = fields.get("name", "")
    description = fields.get("description", "")
    if not name:
        errors.append("SKILL.md frontmatter is missing name")
    else:
        if len(name) > 64:
            errors.append("skill name exceeds 64 characters")
        if not NAME_PATTERN.fullmatch(name):
            errors.append("skill name must use lowercase letters, digits and hyphens")
        if name != ROOT.name:
            errors.append(f"skill name {name!r} does not match parent directory {ROOT.name!r}")
    if not description:
        errors.append("SKILL.md frontmatter is missing description")
    elif len(description) > 1024:
        errors.append("skill description exceeds 1024 characters")

    for token in KNOWN_PLACEHOLDERS:
        if token in skill:
            errors.append(f"unresolved placeholder in SKILL.md: {token}")

    # Manifest is intentionally exact: it also lists manifest.txt itself.
    manifest_path = ROOT / "manifest.txt"
    if manifest_path.is_file():
        listed = [line.strip() for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        actual = sorted(str(path.relative_to(ROOT)).replace("\\", "/") for path in ROOT.rglob("*") if path.is_file())
        if sorted(listed) != actual:
            missing = sorted(set(actual) - set(listed))
            stale = sorted(set(listed) - set(actual))
            if missing:
                errors.append("manifest missing: " + ", ".join(missing[:8]))
            if stale:
                errors.append("manifest stale: " + ", ".join(stale[:8]))
    else:
        errors.append("missing: manifest.txt")

    _validate_python_syntax(ROOT / "assets" / "template", "template", errors)
    _validate_python_syntax(ROOT / "scripts", "scripts", errors)
    _validate_bootstrap_tests(errors, notes)
    _validate_generated_projects(errors, notes, skipped)

    # Agent Skills recommends the external `skills-ref validate` command.
    # Use it when installed, but keep this skill self-validating in minimal
    # environments where that optional reference CLI is unavailable.
    skills_ref = shutil.which("skills-ref")
    if skills_ref:
        proc = subprocess.run(
            [skills_ref, "validate", str(ROOT)],
            text=True, capture_output=True, check=False,
        )
        if proc.returncode:
            errors.append("skills-ref validation failed: " + (proc.stderr.strip() or proc.stdout.strip()))
        else:
            notes.append("skills-ref validation passed")
    else:
        reason = "skills-ref validation skipped: CLI not installed"
        notes.append(reason)
        skipped.append(reason)

    for note in notes:
        print(note)

    if errors:
        print("Validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    if skipped:
        print("py-hosttool structural validation passed with skipped checks")
        if args.strict:
            print("Strict validation failed because one or more checks were skipped")
            return 2
        return 0
    print("py-hosttool validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
