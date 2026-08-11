"""Codex 专用 deepseek-subagent 生命周期 CLI。

产品运行链路固定为 Codex -> opencode-go-bridge -> OpenCode Go ->
deepseek-v4-flash。CLI 不提供宿主或模型供应商选择。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from .core.agent_role import make_role
from .core.errors import ManagerError
from .core.manifest import read_manifest_with_source
from .core.paths import state_paths
from .core.status import compose_status, generic_checks
from .core.transaction import operation_lock
from .platforms.codex.adapter import CodexAdapter, _InertBackend
from .platforms.codex.config import parse_toml_text
from .providers import OpenCodeGoProvider

EXIT_OK = 0
EXIT_PARTIAL = 2
EXIT_TIMEOUT = 3
EXIT_FAILED = 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "setup", "repair", "disable", "uninstall", "bridge"))
    parser.add_argument("bridge_action", nargs="?", choices=("start", "status", "stop", "restart"))
    parser.add_argument("--state-home")
    parser.add_argument("--codex-home")
    parser.add_argument("--bridge-json", help=argparse.SUPPRESS)
    parser.add_argument("--parent-model", action="append", default=[], help=argparse.SUPPRESS)
    parser.add_argument("--workdir")
    parser.add_argument("--port", type=int, default=1981)
    parser.add_argument("--auto-start", action="store_true")
    parser.add_argument("--pythonw")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def result(status: str, **kwargs: Any) -> dict[str, Any]:
    return {"status": status, **kwargs}


def emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(payload.get("status", "unknown"))
    for key, value in payload.items():
        if key != "status":
            print(f"{key}: {value}")


def _backend() -> _InertBackend:
    return _InertBackend()


def _status_payload(state, codex_home: str | None) -> dict[str, Any]:
    manifest, _source, _legacy = read_manifest_with_source(state.state_root)
    if manifest.get("platform_home"):
        codex_home = manifest["platform_home"]
    role_name = (manifest.get("roles") or ["DeepSeek"])[0]
    role = make_role(OpenCodeGoProvider, name=role_name)
    backend = _backend()
    adapter = CodexAdapter()
    return compose_status(
        generic_checks(state, backend),
        adapter.status(state, backend, role, OpenCodeGoProvider, platform_home=codex_home),
    )


def read_codex_models_cache(cache_path: str | None = None) -> dict:
    """只读 Codex 桌面模型目录缓存。"""

    path = Path(cache_path or os.path.expanduser(r"~\.codex\models_cache.json"))
    if not path.is_file():
        raise ManagerError("models_cache_missing", f"Codex 模型目录缓存不存在：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManagerError("models_cache_invalid", "Codex 模型目录缓存无法解析。") from exc
    if not isinstance(payload.get("models"), list):
        raise ManagerError("models_cache_invalid", "Codex 模型目录缓存缺少 models 列表。")
    return payload


def build_codex_catalog(models_cache: dict, model: str) -> dict:
    """保留 Codex 缓存条目，并生成固定为 Multi-Agent V1 的 DeepSeek 条目。"""

    models = [dict(item) for item in models_cache.get("models", []) if isinstance(item, dict)]
    template = next((m for m in models if m.get("slug") == "gpt-5.6-sol"), None)
    if template is None:
        template = next((m for m in models if m.get("multi_agent_version")), None)
    if template is None:
        raise ManagerError("catalog_template_missing", "Codex 模型目录缓存中没有可用模板条目。")
    entry = dict(template)
    entry["slug"] = model
    entry["display_name"] = "DeepSeek V4 Flash"
    entry["multi_agent_version"] = "v1"
    for index, item in enumerate(models):
        if item.get("slug") == model:
            models[index] = entry
            break
    else:
        models.append(entry)
    return {"models": models}


def _codex_setup(
    state,
    codex_home: str | None,
    bridge_json: str,
    operation: str,
    parent_models: Sequence[str] = (),
) -> dict[str, Any]:
    bridge_path = Path(bridge_json)
    if not bridge_path.is_file():
        raise ManagerError("bridge_json_missing", f"桥信息文件不存在：{bridge_path}")
    try:
        bridge_info = json.loads(bridge_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManagerError("bridge_json_invalid", "桥信息文件无法解析。") from exc
    token_script = bridge_info.get("token_script")
    if not token_script or not Path(token_script).is_file():
        raise ManagerError("bridge_token_script_missing", "桥的令牌脚本不存在（桥未运行或目录已被清理）。")

    role = make_role(OpenCodeGoProvider)
    codex_auth = {
        "command": str(Path(sys.executable).resolve()),
        "args": [str(Path(token_script).resolve())],
        "timeout_ms": 5000,
        "refresh_interval_ms": 0,
    }
    bridge_base_url = bridge_info.get("base_url") or f"http://127.0.0.1:{bridge_info.get('port', 0)}/v1"
    adapter = CodexAdapter()
    codex_paths = adapter.resolve_paths(codex_home, role)
    catalog_payload = build_codex_catalog(read_codex_models_cache(), OpenCodeGoProvider.model)
    parent_model = None
    if codex_paths.config.is_file():
        try:
            parent_model = parse_toml_text(codex_paths.config.read_text(encoding="utf-8")).get("model")
        except ManagerError:
            parent_model = None
    managed_parent_models = {item for item in (parent_model, *parent_models) if item}
    for item in catalog_payload.get("models", []):
        if item.get("slug") == OpenCodeGoProvider.model or item.get("slug") in managed_parent_models:
            item["multi_agent_version"] = "v1"

    install = adapter.install_bridge(
        state,
        codex_paths,
        role,
        OpenCodeGoProvider,
        catalog_payload,
        codex_auth,
        bridge_base_url,
    )
    static_status = adapter.status(state, _backend(), role, OpenCodeGoProvider, codex_home)
    return {
        "status": static_status["status"],
        "operation": operation,
        "install": install,
        "static_status": static_status,
    }


def _bridge_payload(args: argparse.Namespace, state) -> dict[str, Any]:
    from .bridges.opencode_go.lifecycle import BridgeLifecycle

    lifecycle = BridgeLifecycle(state.state_root, pythonw=args.pythonw)
    workdir = args.workdir or str(state.state_root / "bridge-runtime")
    if args.bridge_action == "start":
        return lifecycle.start(workdir, port=args.port, auto_start=args.auto_start)
    if args.bridge_action == "status":
        return lifecycle.status(port=args.port)
    if args.bridge_action == "stop":
        return lifecycle.stop()
    return lifecycle.restart(workdir, port=args.port, auto_start=args.auto_start)


def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    state = state_paths(args.state_home)
    try:
        adapter = CodexAdapter()
        if args.command == "bridge":
            payload = _bridge_payload(args, state)
        elif args.command == "status":
            payload = _status_payload(state, args.codex_home)
        else:
            with operation_lock(state):
                if args.command in {"setup", "repair"}:
                    payload = _codex_setup(
                        state,
                        args.codex_home,
                        args.bridge_json or "",
                        args.command,
                        args.parent_model,
                    )
                elif args.command == "disable":
                    payload = adapter.disable(state, _backend(), platform_home=args.codex_home)
                else:
                    payload = adapter.uninstall(state, _backend(), platform_home=args.codex_home)
        emit(payload, args.json)
        non_ok = {"partial", "disabled_with_conflicts", "partially_uninstalled"}
        return EXIT_OK if payload["status"] not in non_ok else EXIT_PARTIAL
    except ManagerError as exc:
        emit(result(exc.code, message=str(exc), **exc.details), args.json)
        return EXIT_PARTIAL
    except subprocess.TimeoutExpired:
        emit(result("timeout", message="操作超时，未输出任何凭据。"), args.json)
        return EXIT_TIMEOUT
    except Exception as exc:
        emit(result("failed", message=f"{type(exc).__name__}: {exc}"), args.json)
        return EXIT_FAILED


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
