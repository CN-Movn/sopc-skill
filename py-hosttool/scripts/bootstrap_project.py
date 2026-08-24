#!/usr/bin/env python3
"""Create a host-tool project from the starter template.

The bootstrapper deliberately stages a project before replacing the target.
That keeps an existing generated project intact when input validation or
template processing fails, and makes ``--force`` safe to use only for projects
that this script previously created.
"""
from __future__ import annotations

import argparse
import ast
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = SKILL_ROOT / "assets" / "template" / "py_hosttool_template"
TEXT_SUFFIXES = {".py", ".md", ".txt", ".bat", ".spec", ".gitignore"}
GENERATED_MARKER = ".py-hosttool-generated"
GENERATED_MARKER_CONTENT = "py-hosttool bootstrap project v1\n"
MAX_IDENTITY_LENGTH = 128


def _safe_identity(value: str, field_name: str) -> str:
    """Validate values that are copied into both text and Python files."""
    value = value.strip()
    if not value:
        raise argparse.ArgumentTypeError(f"{field_name} must not be empty")
    if len(value) > MAX_IDENTITY_LENGTH:
        raise argparse.ArgumentTypeError(
            f"{field_name} must be at most {MAX_IDENTITY_LENGTH} characters"
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise argparse.ArgumentTypeError(
            f"{field_name} must not contain control characters or newlines"
        )
    if '"""' in value or "{{" in value or "}}" in value:
        raise argparse.ArgumentTypeError(
            f"{field_name} contains unsupported template or quote delimiters"
        )
    return value


def _positive_baudrate(value: str) -> int:
    try:
        baudrate = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("baudrate must be an integer") from exc
    if not 1 <= baudrate <= 10_000_000:
        raise argparse.ArgumentTypeError("baudrate must be between 1 and 10000000")
    return baudrate


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _has_symlink_component(path: Path) -> bool:
    """Reject destinations that could redirect a destructive operation."""
    return any(candidate.is_symlink() for candidate in (path, *path.parents))


def _is_generated_project(path: Path) -> bool:
    marker = path / GENERATED_MARKER
    if not marker.is_file() or marker.is_symlink():
        return False
    try:
        return marker.read_text(encoding="utf-8") == GENERATED_MARKER_CONTENT
    except (OSError, UnicodeError):
        return False


def _assert_force_target(destination: Path) -> None:
    """Ensure ``--force`` can only replace a project owned by this script."""
    if _is_within(destination, SKILL_ROOT):
        raise ValueError("refusing to remove a path inside the py-hosttool skill")
    if _has_symlink_component(destination):
        raise ValueError("refusing to remove a destination containing a symlink")
    if not _is_generated_project(destination):
        raise ValueError(
            "--force is allowed only for a non-empty project previously created "
            f"by this bootstrapper (missing or invalid {GENERATED_MARKER})"
        )


def _validate_python_syntax(root: Path) -> None:
    for path in sorted(root.rglob("*.py")):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise ValueError(f"generated Python syntax check failed: {path.name}: {exc}") from exc


def _replace_text_files(root: Path, replacements: dict[str, str]) -> None:
    for path in root.rglob("*"):
        if not path.is_file() or (path.suffix not in TEXT_SUFFIXES and path.name != ".gitignore"):
            continue
        text = path.read_text(encoding="utf-8")
        for old, new in replacements.items():
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8", newline="\n")


def _new_sibling_path(destination: Path, label: str) -> Path:
    return destination.parent / f".{destination.name}.bootstrap-{label}-{uuid.uuid4().hex}"


def _commit_staged_project(staging: Path, destination: Path, force: bool) -> None:
    """Atomically-ish replace the destination while retaining rollback ability."""
    if not destination.exists():
        staging.replace(destination)
        return

    if not force:
        # The only existing destination accepted without --force is empty.
        destination.rmdir()
        try:
            staging.replace(destination)
        except OSError:
            destination.mkdir()
            raise
        return

    backup = _new_sibling_path(destination, "backup")
    destination.replace(backup)
    try:
        staging.replace(destination)
    except OSError:
        if not destination.exists() and backup.exists():
            backup.replace(destination)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def exe_name(app_name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", app_name.strip())
    return value.strip("._") or "HostTool"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a PySide6 host-tool project")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--app-name", required=True, type=lambda value: _safe_identity(value, "app-name"))
    parser.add_argument("--version", default="0.1.0", type=lambda value: _safe_identity(value, "version"))
    parser.add_argument(
        "--baudrate",
        required=True,
        type=_positive_baudrate,
        help="initial UI baudrate; must be verified against the target device",
    )
    parser.add_argument("--layout", choices=("workbench", "dashboard"), default="workbench")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    raw_destination = args.destination.expanduser()
    if not raw_destination.is_absolute():
        raw_destination = Path.cwd() / raw_destination
    if _has_symlink_component(raw_destination):
        parser.error("destination must not contain a symlink component")
    destination = raw_destination.resolve(strict=False)
    if _is_within(destination, SKILL_ROOT):
        parser.error("destination must be outside the py-hosttool skill directory")
    if destination.exists() and not destination.is_dir():
        parser.error(f"destination is not a directory: {destination}")

    force_replace = False
    if destination.exists() and any(destination.iterdir()):
        if not args.force:
            parser.error(f"destination is not empty: {destination}")
        try:
            _assert_force_target(destination)
        except ValueError as exc:
            parser.error(str(exc))
        force_replace = True

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.bootstrap-", dir=str(destination.parent)))
    replacements = {
        "{{APP_NAME}}": args.app_name,
        "{{APP_VERSION}}": args.version,
        "{{EXE_NAME}}": exe_name(args.app_name),
        "{{LAYOUT}}": args.layout,
        "{{APP_NAME_LITERAL}}": repr(args.app_name),
        "{{APP_VERSION_LITERAL}}": repr(args.version),
        "{{DEFAULT_BAUDRATE}}": str(args.baudrate),
    }
    try:
        shutil.copytree(TEMPLATE, staging, dirs_exist_ok=True)
        _replace_text_files(staging, replacements)
        _validate_python_syntax(staging)
        (staging / GENERATED_MARKER).write_text(GENERATED_MARKER_CONTENT, encoding="utf-8")
        _commit_staged_project(staging, destination, force_replace)
    except (OSError, UnicodeError, ValueError) as exc:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        return 1

    print(f"Created {args.app_name} {args.version} at {destination}")
    print(f"Layout: {args.layout}")
    print(f"Initial baudrate: {args.baudrate} (verify against the target device)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
