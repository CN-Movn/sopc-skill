"""Deterministic machine-local continuity logs for replaceable child agents.

The canonical store is the installed Skill's private ``.local/handoffs``
directory (``<installed-skill-root>/.local/handoffs``).  Handoff logs are
machine-local persistent user data like the other private ``.local`` files;
they are never written into a project working directory and are preserved
across Skill upgrades and syncs.

The deterministic file identity binds the normalized absolute project root
(expanded, resolved, and on Windows casefolded with native separators), the
stable role, and the scope, so identical role and scope in different projects
never share one log while a successor with the same identity continues it.
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomic import atomic_write
from .errors import ManagerError

HANDOFF_SCHEMA_VERSION = 1
HANDOFF_ROOT_RELATIVE = Path(".local") / "handoffs"
LEGACY_HANDOFF_DIRECTORY = Path(".deepseek-subagent") / "handoffs"
MAX_VERIFIED_APPEND_BYTES = 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _required_text(value: str, field: str, maximum: int) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ManagerError("handoff_field_missing", f"Handoff field is required: {field}")
    if len(text) > maximum:
        raise ManagerError("handoff_field_too_long", f"Handoff field is too long: {field}")
    return text


def _project_root(value: str | Path) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise ManagerError("handoff_project_root_not_absolute", "Handoff project root must be absolute")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ManagerError("handoff_project_root_missing", f"Handoff project root does not exist: {candidate}") from exc
    if not resolved.is_dir():
        raise ManagerError("handoff_project_root_not_directory", f"Handoff project root is not a directory: {resolved}")
    return resolved


def _role_slug(stable_role: str) -> str:
    normalized = unicodedata.normalize("NFKC", stable_role).casefold()
    slug = re.sub(r"[^\w]+", "-", normalized, flags=re.UNICODE).strip("-_")
    slug = slug[:64].rstrip("-_")
    return slug or "agent"


def _skill_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_handoff_root() -> Path:
    """Return the canonical machine-local handoff store under the Skill root."""
    return _skill_root() / HANDOFF_ROOT_RELATIVE


def _project_identity(project_root: str | Path) -> str:
    """Normalize one project root into one stable identity string.

    ``resolve`` removes ``.``/``..`` parts, symlinks, and trailing slashes;
    Windows ``normcase`` additionally casefolds the path and converts forward
    slashes to native separators so one real project never gets several keys.
    """

    resolved = _project_root(project_root)
    text = str(resolved)
    if os.name == "nt":
        text = os.path.normcase(text)
    return text


def project_fingerprint(project_root: str | Path) -> str:
    return hashlib.sha256(_project_identity(project_root).encode("utf-8")).hexdigest()[:12]


def continuity_key(project_root: str | Path, stable_role: str, scope: str) -> str:
    """Collision-resistant identity for one handoff log.

    The material is the normalized project identity plus the normalized stable
    role and scope, so identical role and scope in different projects always
    produce different keys while a successor with the same tuple continues the
    same log.  The key is not a credential fingerprint.
    """

    role = _required_text(stable_role, "stable_role", 200)
    normalized_scope = _required_text(scope, "scope", 2000)
    material = "\n".join(
        (
            _project_identity(project_root),
            unicodedata.normalize("NFKC", role).casefold(),
            unicodedata.normalize("NFKC", normalized_scope),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def _legacy_continuity_key(stable_role: str, scope: str) -> str:
    """Pre-1.6.3 project-independent key scheme, used only for legacy detection."""

    role = _required_text(stable_role, "stable_role", 200)
    normalized_scope = _required_text(scope, "scope", 2000)
    material = (
        f"{unicodedata.normalize('NFKC', role).casefold()}\n"
        f"{unicodedata.normalize('NFKC', normalized_scope)}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def handoff_path(
    project_root: str | Path,
    stable_role: str,
    scope: str,
    root: str | Path | None = None,
) -> Path:
    role = _required_text(stable_role, "stable_role", 200)
    normalized_scope = _required_text(scope, "scope", 2000)
    key = continuity_key(project_root, role, normalized_scope)
    store = Path(root).expanduser().resolve() if root is not None else default_handoff_root()
    return store / f"{_role_slug(role)}--{key}.md"


def legacy_handoff_path(project_root: str | Path, stable_role: str, scope: str) -> Path:
    """Pre-1.6.3 project-local location, used only to detect and report old logs."""

    root = _project_root(project_root)
    role = _required_text(stable_role, "stable_role", 200)
    normalized_scope = _required_text(scope, "scope", 2000)
    key = _legacy_continuity_key(role, normalized_scope)
    return root / LEGACY_HANDOFF_DIRECTORY / f"{_role_slug(role)}--{key}.md"


def _document_marker(key: str) -> str:
    return f"<!-- deepseek-subagent-handoff:v{HANDOFF_SCHEMA_VERSION} key={key} -->"


def turn_marker(turn_token: str) -> str:
    try:
        normalized = str(uuid.UUID(str(turn_token)))
    except (ValueError, AttributeError) as exc:
        raise ManagerError("handoff_turn_token_invalid", "Handoff turn token is invalid") from exc
    return f"<!-- deepseek-subagent-turn token={normalized} -->"


def _sha256_prefix(path: Path, size: int) -> str:
    if size < 0:
        raise ManagerError("handoff_baseline_invalid", "Handoff baseline size cannot be negative")
    digest = hashlib.sha256()
    remaining = size
    try:
        with path.open("rb") as handle:
            while remaining:
                chunk = handle.read(min(remaining, 1024 * 1024))
                if not chunk:
                    raise ManagerError(
                        "handoff_history_modified",
                        "The handoff history is shorter than the issued baseline",
                    )
                digest.update(chunk)
                remaining -= len(chunk)
    except OSError as exc:
        raise ManagerError("handoff_file_unreadable", f"Handoff file cannot be read: {path}") from exc
    return digest.hexdigest()


def _snapshot(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError as exc:
        raise ManagerError("handoff_file_missing", f"Handoff file is unavailable: {path}") from exc
    return {
        "handoff_file": str(path),
        "baseline_size": stat.st_size,
        "baseline_mtime_ns": stat.st_mtime_ns,
        "baseline_sha256": _sha256_prefix(path, stat.st_size),
    }


def initialize_handoff(
    project_root: str | Path,
    stable_role: str,
    scope: str,
    root: str | Path | None = None,
) -> dict[str, Any]:
    role = _required_text(stable_role, "stable_role", 200)
    normalized_scope = _required_text(scope, "scope", 2000)
    key = continuity_key(project_root, role, normalized_scope)
    path = handoff_path(project_root, role, normalized_scope, root=root)
    store = path.parent
    # Idempotent store creation: a missing canonical directory is created and
    # an existing one is never cleared or rewritten.
    store.mkdir(parents=True, exist_ok=True)
    marker = _document_marker(key)
    created = False

    legacy_path = legacy_handoff_path(project_root, role, normalized_scope)
    legacy_detected = legacy_path.is_file()
    legacy_verified = False
    if legacy_detected:
        try:
            legacy_header = legacy_path.read_bytes()[:8192].decode("utf-8", "replace")
        except OSError:
            legacy_header = ""
        legacy_verified = _document_marker(_legacy_continuity_key(role, normalized_scope)) in legacy_header

    if path.exists():
        try:
            header = path.read_bytes()[:8192].decode("utf-8", "replace")
        except OSError as exc:
            raise ManagerError("handoff_file_unreadable", f"Handoff file cannot be read: {path}") from exc
        if marker not in header:
            raise ManagerError(
                "handoff_file_conflict",
                "The deterministic handoff path already contains an unrelated or incompatible file",
                {"handoff_file": str(path)},
            )
    else:
        template = (
            f"# DeepSeek continuity log — {role}\n\n"
            f"{marker}\n\n"
            "> Append one compact record after every completed child turn. Preserve prior records. "
            "Record reusable facts and rationale, not hidden chain-of-thought, raw prompts, credentials, or tokens.\n\n"
            "## Continuity identity\n\n"
            f"- Stable role: `{role}`\n"
            f"- Scope: `{normalized_scope}`\n"
            f"- Continuity key: `{key}`\n"
            f"- Created (UTC): `{_now()}`\n\n"
            "## Durable baseline\n\n"
            "- Canonical artifacts and verified facts: _add as they become known_\n"
            "- Current constraints and invariants: _add as they become known_\n\n"
            "## Turn records\n\n"
            "Append each record using this shape:\n\n"
            "```markdown\n"
            "### <UTC timestamp> — <short task title>\n"
            "<!-- deepseek-subagent-turn token=<token supplied by parent> -->\n"
            "- Request and prior context:\n"
            "- Work performed and rationale:\n"
            "- Evidence and artifact paths:\n"
            "- Decisions and result:\n"
            "- Open risks and next step:\n"
            "```\n"
        )
        try:
            atomic_write(path, template.encode("utf-8"))
        except OSError as exc:
            raise ManagerError("handoff_file_create_failed", f"Handoff file could not be created: {path}") from exc
        created = True

    return {
        **_snapshot(path),
        "status": "handoff_ready",
        "created": created,
        "handoff_key": key,
        "handoff_schema_version": HANDOFF_SCHEMA_VERSION,
        "stable_role": role,
        "scope": normalized_scope,
        "handoff_root": str(store),
        "legacy_handoff_detected": legacy_detected,
        "legacy_handoff_path": str(legacy_path),
        "legacy_handoff_verified": legacy_verified,
        "legacy_handoff_migrated": False,
    }


def issue_handoff_turn(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    snapshot = _snapshot(target)
    token = str(uuid.uuid4())
    return {
        **snapshot,
        "status": "handoff_turn_ready",
        "turn_token": token,
        "required_marker": turn_marker(token),
        "append_only": True,
    }


def verify_handoff_update(
    path: str | Path,
    after_size: int,
    baseline_sha256: str,
    turn_token: str,
) -> dict[str, Any]:
    target = Path(path)
    marker = turn_marker(turn_token).encode("utf-8")
    snapshot = _snapshot(target)
    current_size = int(snapshot["baseline_size"])
    baseline = int(after_size)
    if baseline < 0:
        raise ManagerError("handoff_baseline_invalid", "Handoff baseline size cannot be negative")
    expected_digest = str(baseline_sha256 or "").strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise ManagerError("handoff_baseline_digest_invalid", "Handoff baseline SHA-256 is invalid")
    if current_size < baseline or _sha256_prefix(target, baseline) != expected_digest:
        return {
            **snapshot,
            "status": "handoff_history_modified",
            "updated": False,
            "error_code": "handoff_history_modified",
        }
    if current_size <= baseline:
        return {
            **snapshot,
            "status": "handoff_update_missing",
            "updated": False,
            "error_code": "handoff_update_missing",
        }
    appended_size = current_size - baseline
    if appended_size > MAX_VERIFIED_APPEND_BYTES:
        return {
            **snapshot,
            "status": "handoff_update_too_large",
            "updated": False,
            "error_code": "handoff_update_too_large",
            "appended_size": appended_size,
        }
    try:
        with target.open("rb") as handle:
            handle.seek(baseline)
            appended = handle.read(appended_size)
    except OSError as exc:
        raise ManagerError("handoff_file_unreadable", f"Handoff file cannot be read: {target}") from exc
    marker_present = marker in appended
    return {
        **snapshot,
        "status": "handoff_update_verified" if marker_present else "handoff_turn_marker_missing",
        "updated": marker_present,
        "error_code": None if marker_present else "handoff_turn_marker_missing",
        "appended_size": appended_size,
        "turn_token": str(uuid.UUID(str(turn_token))),
    }
