#!/usr/bin/env python3
"""本地兼容桥独立进程（受控验收用）。

用法：
    python bridge_standalone.py --workdir <dir>

行为：
- 仅监听 127.0.0.1 随机端口；
- OpenCode Go API Key 只存在于本进程内存；
- 使用 Skill .local/local-bridge-token.txt 中的稳定本地令牌，bridge 与
  Codex auth.command 读取同一个文件；
- 写 <workdir>/bridge.json：{port, pid, token_file, token_script}；
- 前台运行；Ctrl+C 或 kill <pid> 停止（进程退出即清理内存凭据）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deepseek_subagent.bridges.opencode_go.bridge import OpenCodeGoBridge  # noqa: E402
from deepseek_subagent.bridges.opencode_go.token_store import TOKEN_FILE, ensure_token  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--pid-file", default=None)
    args = parser.parse_args()

    # pythonw.exe（后台无窗口模式）下 stdout/stderr 为 None，print 会崩溃；
    # 重定向到 devnull 保持进程稳定。
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    workdir = Path(args.workdir).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    pid_file = (
        Path(args.pid_file).expanduser().resolve()
        if args.pid_file
        else workdir / "bridge.pid"
    )

    def _write_pid() -> None:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()), encoding="utf-8")

    def _remove_pid() -> None:
        pid_file.unlink(missing_ok=True)

    _write_pid()

    skill_root = Path(__file__).resolve().parents[2]
    token_dir = skill_root / ".local"
    key_file = skill_root / ".local" / "opencode-go.key"
    token_script = skill_root / "runtime" / "scripts" / "print_bridge_token.py"
    token, token_state = ensure_token(token_dir, legacy_workdir=workdir)
    codex_token_file = token_dir / TOKEN_FILE
    bridge = OpenCodeGoBridge(key_file=str(key_file), local_token=token, protocol_audit_dir=str(workdir))
    handle = bridge.start(fixed_port=args.port or None)

    info = {
        "port": handle.port,
        "base_url": handle.base_url,
        "pid": os.getpid(),
        "token_file": str(codex_token_file),
        "token_script": str(token_script),
        "token_version": token_state["token_version"],
        "token_generation": token_state["token_generation"],
        "token_fingerprint": token_state["token_fingerprint"],
    }
    (workdir / "bridge.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(json.dumps(info))
    print(f"bridge ready: {handle.base_url} (pid {os.getpid()}) — Ctrl+C 停止", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        handle.stop()
        _remove_pid()
        print("bridge stopped; persistent local token retained", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
