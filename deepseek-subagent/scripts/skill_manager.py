#!/usr/bin/env python3
"""Stable lifecycle entrypoint for the installed deepseek-subagent Skill."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = SKILL_DIR / "runtime"
sys.path.insert(0, str(RUNTIME_DIR))

from deepseek_subagent.bridges.opencode_go.credentials import (  # noqa: E402
    CredentialError,
    credential_status,
    discover_credential,
    key_file_path,
)
from deepseek_subagent.bridges.opencode_go.lifecycle import BridgeLifecycle  # noqa: E402
from deepseek_subagent.bridges.opencode_go.token_store import purge_token  # noqa: E402
from deepseek_subagent.cli import run as run_cli  # noqa: E402
from deepseek_subagent.core.agent_role import make_role  # noqa: E402
from deepseek_subagent.core.errors import ManagerError  # noqa: E402
from deepseek_subagent.core.manifest import read_manifest, write_manifest  # noqa: E402
from deepseek_subagent.core.paths import state_paths  # noqa: E402
from deepseek_subagent.platforms.codex.paths import CodexPaths  # noqa: E402
from deepseek_subagent.platforms.codex.verify import direct_test  # noqa: E402
from deepseek_subagent.providers import OpenCodeGoProvider  # noqa: E402

BRIDGE_WORKDIR_NAME = "bridge-runtime"
PORT = 1981


def _state():
    return state_paths(None)


def _local_key_file() -> Path:
    return key_file_path(SKILL_DIR)


def _published_token_file() -> Path:
    return SKILL_DIR / ".local" / "local-bridge-token.txt"


def _lifecycle(state) -> BridgeLifecycle:
    return BridgeLifecycle(
        state.state_root,
        script=str(RUNTIME_DIR / "scripts" / "bridge_standalone.py"),
    )


def _capture(argv: list[str]) -> tuple[int, dict[str, Any]]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = run_cli(argv + ["--json"])
    text = output.getvalue().strip()
    try:
        payload = json.loads(text) if text else {"status": "no_output"}
    except json.JSONDecodeError:
        payload = {"status": "invalid_cli_output", "output": text[-2000:]}
    return code, payload


def _runtime_payload(lifecycle: BridgeLifecycle) -> dict[str, Any]:
    return lifecycle.status(port=PORT)


def _require_key() -> None:
    target = _local_key_file()
    try:
        credential = discover_credential(target)
    except CredentialError as exc:
        raise ManagerError(exc.code, str(exc), {"key_file": str(exc.path)}) from exc
    if credential is None:
        raise ManagerError(
            "upstream_key_missing",
            f"OpenCode Go key file is missing: {target}",
            {"key_file": str(target)},
        )


def _ensure_bridge(state) -> dict[str, Any]:
    _require_key()
    lifecycle = _lifecycle(state)
    expected_workdir = state.state_root / BRIDGE_WORKDIR_NAME
    current = lifecycle.status(port=PORT)
    runtime_file = state.state_root / "bridge-runtime.json"
    runtime: dict[str, Any] = {}
    if runtime_file.is_file():
        try:
            runtime = json.loads(runtime_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            runtime = {}
    bridge_info: dict[str, Any] = {}
    bridge_json = expected_workdir / "bridge.json"
    if bridge_json.is_file():
        try:
            bridge_info = json.loads(bridge_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            bridge_info = {}
    expected_script = str((RUNTIME_DIR / "scripts" / "bridge_standalone.py").resolve())
    if current.get("status") == "running":
        runtime_matches = runtime.get("script") == expected_script and runtime.get("workdir") == str(expected_workdir.resolve())
        auth_matches = (
            bridge_info.get("token_file") == str(_published_token_file().resolve())
            and bridge_info.get("token_script") == str((RUNTIME_DIR / "scripts" / "print_bridge_token.py").resolve())
        )
        if runtime_matches and auth_matches:
            return current
        return lifecycle.restart(str(expected_workdir), port=PORT, auto_start=True)
    return lifecycle.start(str(expected_workdir), port=PORT, auto_start=True)


def _annotate_manifest(state, bridge: dict[str, Any]) -> None:
    manifest = read_manifest(state.state_root)
    if not manifest:
        return
    baseline_backup = manifest.get("baseline_backup")
    if not baseline_backup:
        for candidate in sorted((state.state_root / "backups").glob("*")):
            backup_manifest = candidate / "backup_manifest.json"
            if not backup_manifest.is_file():
                continue
            try:
                entries = json.loads(backup_manifest.read_text(encoding="utf-8")).get("entries", [])
            except (OSError, json.JSONDecodeError):
                continue
            existed = {Path(item.get("target", "")).name: item.get("existed") for item in entries}
            if existed.get("models-with-deepseek.json") is False and existed.get("DeepSeek.toml") is False:
                baseline_backup = str(candidate.resolve())
                break
    manifest.update(
        {
            "skill_version": (SKILL_DIR / "VERSION").read_text(encoding="utf-8").strip(),
            "skill_root": str(SKILL_DIR.resolve()),
            "runtime_root": str(RUNTIME_DIR.resolve()),
            "bridge_script": str((RUNTIME_DIR / "scripts" / "bridge_standalone.py").resolve()),
            "bridge_workdir": bridge.get("workdir"),
            "baseline_backup": baseline_backup,
        }
    )
    write_manifest(state.state_root, manifest)


def _failed_doctor(payload: dict[str, Any], stage: str, code: str, message: str) -> dict[str, Any]:
    return {
        **payload,
        "status": "partial",
        "failure_stage": stage,
        "error_code": code,
        "message": message,
    }


def _json_request(url: str, token: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    request = urllib.request.Request(url, method="POST" if payload is not None else "GET", headers=headers)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
        request.data = json.dumps(payload).encode("utf-8")
    try:
        with urllib.request.urlopen(request, timeout=180 if payload is not None else 10) as response:
            status = int(response.status)
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read().decode("utf-8", "replace")
    except (OSError, urllib.error.URLError, TimeoutError):
        return 0, {"error": {"code": "localhost_unreachable"}}
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        body = {"error": {"code": "invalid_bridge_response"}}
    return status, body if isinstance(body, dict) else {}


def _doctor(state) -> tuple[int, dict[str, Any]]:
    static_code, static = _capture(["status"])
    payload: dict[str, Any] = {
        "status": "partial",
        "checks": {
            "static_configuration": static.get("status") == "configured",
            "key_file_present": False,
            "bridge_process": False,
            "auth_command": False,
            "localhost_authentication": False,
            "codex_auth_context": False,
            "end_to_end_inference": False,
        },
    }
    if static_code != 0 or static.get("status") != "configured":
        return 2, _failed_doctor(payload, "static_configuration", "codex_configuration_invalid", "Codex managed configuration is incomplete or inconsistent.")

    key_state = credential_status(_local_key_file())
    payload["checks"]["key_file_present"] = key_state.get("status") == "credential_present"
    if not payload["checks"]["key_file_present"]:
        code = str(key_state.get("status") or "upstream_key_missing")
        return 2, _failed_doctor(payload, "upstream_key_file", code, f"OpenCode Go key file is unavailable: {_local_key_file()}")
    try:
        fixed_credential = discover_credential(_local_key_file())
    except CredentialError as exc:
        code = exc.code
        return 2, _failed_doctor(payload, "upstream_key_file", code, str(exc))
    if fixed_credential is None:
        return 2, _failed_doctor(payload, "upstream_key_file", "upstream_key_missing", f"OpenCode Go key file is unavailable: {_local_key_file()}")

    lifecycle = _lifecycle(state)
    bridge = lifecycle.status(port=PORT)
    payload["bridge"] = bridge
    payload["checks"]["bridge_process"] = bridge.get("status") == "running"
    if not payload["checks"]["bridge_process"]:
        return 2, _failed_doctor(payload, "bridge_process", "bridge_not_running", "The managed localhost bridge is not running.")

    manifest = read_manifest(state.state_root)
    codex_home = Path(manifest.get("platform_home") or Path.home() / ".codex")
    try:
        parsed = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
        provider = (parsed.get("model_providers") or {}).get("opencode-go-bridge") or {}
        auth = provider.get("auth") or {}
        command = auth.get("command")
        args = auth.get("args") or []
        timeout = max(float(auth.get("timeout_ms") or 5000) / 1000.0, 1.0)
        if not isinstance(command, str) or not isinstance(args, list):
            raise ValueError("invalid auth.command")
        proc = subprocess.run(
            [command, *[str(item) for item in args]],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env={**os.environ, "CODEX_HOME": str(codex_home)},
        )
        token = proc.stdout.strip() if proc.returncode == 0 else ""
    except (OSError, ValueError, tomllib.TOMLDecodeError, subprocess.SubprocessError):
        provider = {}
        token = ""
    payload["checks"]["auth_command"] = bool(token)
    if not token:
        return 2, _failed_doctor(payload, "auth_command", "auth_command_failed", "Codex auth.command did not return the localhost bridge token.")

    base_url = str(provider.get("base_url") or bridge.get("base_url") or "").rstrip("/")
    health_url = base_url[:-3] + "/health" if base_url.endswith("/v1") else base_url + "/health"
    health_status, health = _json_request(health_url, token)
    payload["checks"]["localhost_authentication"] = health_status == 200
    if health_status != 200:
        code = str(((health.get("error") or {}).get("code")) or "localhost_unreachable")
        return 2, _failed_doctor(payload, "localhost_authentication", code, "The configured auth.command could not authenticate to the localhost bridge.")

    codex_bin = str((static.get("checks") or {}).get("desktop_codex_path") or "")
    if not codex_bin:
        return 2, _failed_doctor(payload, "codex_auth_context", "codex_runtime_missing", "The Codex runtime required for the real auth.command check was not found.")
    try:
        role = make_role(OpenCodeGoProvider)
        direct_test(state, CodexPaths.from_home(codex_home, role.name), codex_bin, role)
    except ManagerError as exc:
        stage = "codex_auth_context" if exc.code == "auth_command_sandbox_failed" else "end_to_end_inference"
        return 2, _failed_doctor(payload, stage, exc.code, str(exc))
    payload["checks"]["codex_auth_context"] = True
    payload["checks"]["end_to_end_inference"] = True
    payload.update({"status": "configured", "failure_stage": None, "error_code": None})
    return 0, payload


def _purge_after_exit() -> None:
    command = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "Start-Sleep -Milliseconds 700; "
        f"Remove-Item -LiteralPath '{str(SKILL_DIR).replace(chr(39), chr(39) + chr(39))}' -Recurse -Force"
    )
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("setup", "status", "doctor", "repair", "disable", "uninstall", "bridge", "credentials"),
    )
    parser.add_argument(
        "bridge_action",
        nargs="?",
        choices=("start", "status", "stop", "restart", "rotate-token"),
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--e2e", action="store_true", help="Run the real Codex-to-OpenCode-Go inference check.")
    parser.add_argument("--keep-skill", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    state = _state()
    lifecycle = _lifecycle(state)

    if args.command == "credentials":
        if (args.bridge_action or "status") != "status":
            parser.error("credentials supports only status")
        payload = credential_status(_local_key_file())
        code = 0 if payload.get("status") == "credential_present" else 2
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload.get("status", "unknown"))
        return code

    if args.command == "bridge":
        action = args.bridge_action or "status"
        if action == "status":
            payload = _runtime_payload(lifecycle)
        elif action == "start":
            payload = _ensure_bridge(state)
        elif action == "stop":
            payload = lifecycle.stop()
        elif action == "restart":
            _require_key()
            payload = lifecycle.restart(str(state.state_root / BRIDGE_WORKDIR_NAME), port=PORT, auto_start=True)
        else:
            _require_key()
            payload = lifecycle.rotate_token(str(state.state_root / BRIDGE_WORKDIR_NAME), port=PORT, auto_start=True)
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload.get("status", "unknown"))
        return 0

    if args.command in {"setup", "repair"}:
        bridge = _ensure_bridge(state)
        bridge_json = Path(bridge.get("workdir", "")) / "bridge.json"
        code, payload = _capture([args.command, "--bridge-json", str(bridge_json)])
        if code == 0:
            _annotate_manifest(state, bridge)
            payload["bridge"] = bridge
        else:
            payload["bridge"] = lifecycle.status(port=PORT)
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload.get("status", "unknown"))
        return code

    if args.command in {"status", "doctor"}:
        code, payload = _doctor(state)
        payload["skill_root"] = str(SKILL_DIR.resolve())
        payload["runtime_root"] = str(RUNTIME_DIR.resolve())
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload.get("status", "unknown"))
        return code

    lifecycle.stop()
    code, payload = _capture([args.command])
    payload["bridge"] = lifecycle.status(port=PORT)
    if args.command == "uninstall" and code == 0 and not args.keep_skill:
        purge_token(SKILL_DIR / ".local")
        payload["skill_removal_scheduled"] = True
        _purge_after_exit()
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload.get("status", "unknown"))
    return code


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except ManagerError as exc:
        payload = {"status": exc.code, "message": str(exc), **exc.details}
        arguments = list(argv) if argv is not None else sys.argv[1:]
        print(json.dumps(payload, ensure_ascii=False, indent=2) if "--json" in arguments else payload["status"])
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
