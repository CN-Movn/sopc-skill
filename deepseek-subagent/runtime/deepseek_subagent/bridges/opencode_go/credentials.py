"""Fixed local OpenCode Go key-file discovery and validation."""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .upstream import (
    UPSTREAM_HEADERS,
    UPSTREAM_MODELS_URL,
    classify_upstream_failure,
)

KEY_RELATIVE_PATH = Path(".local") / "opencode-go.key"


class CredentialError(RuntimeError):
    """A stable, non-secret credential error."""

    def __init__(self, code: str, message: str, path: Path):
        super().__init__(message)
        self.code = code
        self.path = path


@dataclass(frozen=True)
class Credential:
    key: str
    source: str
    path: Path


@dataclass(frozen=True)
class ValidationResult:
    status: str
    http_status: int | None = None


def skill_root() -> Path:
    return Path(__file__).resolve().parents[4]


def key_file_path(root: str | Path | None = None) -> Path:
    base = Path(root).expanduser().resolve() if root is not None else skill_root()
    return base / KEY_RELATIVE_PATH


def discover_credential(path: str | Path | None = None) -> Credential | None:
    target = Path(path).expanduser().resolve() if path is not None else key_file_path()
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CredentialError(
            "upstream_key_unreadable",
            f"OpenCode Go key file cannot be read: {target}",
            target,
        ) from exc
    lines = text.splitlines()
    value = lines[0].lstrip("\ufeff").strip() if len(lines) == 1 else ""
    if len(lines) != 1 or not value:
        raise CredentialError(
            "upstream_key_file_invalid",
            f"OpenCode Go key file must contain exactly one non-empty line: {target}",
            target,
        )
    return Credential(key=value, source="fixed_local_file", path=target)


def credential_status(path: str | Path | None = None) -> dict[str, object]:
    target = Path(path).expanduser().resolve() if path is not None else key_file_path()
    present = target.is_file()
    return {
        "status": "credential_present" if present else "upstream_key_missing",
        "key_file_present": present,
        "key_file": str(target),
    }


def validate_api_key(
    key: str,
    *,
    timeout: float = 15.0,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> ValidationResult:
    """Validate with the same headers and classifier used by the bridge."""

    headers = dict(UPSTREAM_HEADERS)
    headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(
        UPSTREAM_MODELS_URL,
        method="GET",
        headers=headers,
    )
    try:
        response = opener(request, timeout=timeout)
        status = int(getattr(response, "status", 0))
        body = getattr(response, "read", lambda: b"")().decode("utf-8", "replace")
        close = getattr(response, "close", None)
        if callable(close):
            close()
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read().decode("utf-8", "replace")
    except (OSError, urllib.error.URLError, TimeoutError):
        return ValidationResult("upstream_network_error")
    if status == 200:
        return ValidationResult("valid", status)
    failure = classify_upstream_failure(status, body)
    return ValidationResult(failure.code, failure.http_status)
