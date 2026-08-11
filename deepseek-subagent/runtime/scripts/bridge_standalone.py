#!/usr/bin/env python3
"""Run the managed localhost OpenCode Go compatibility bridge."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

# A managed bridge runs directly from the installed Skill tree.  Keep that
# release tree immutable at runtime even if an older Task Scheduler command
# omitted Python's ``-B`` switch.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deepseek_subagent.bridges.opencode_go.bridge import OpenCodeGoBridge  # noqa: E402
from deepseek_subagent.bridges.opencode_go.control import BRIDGE_ABI_VERSION  # noqa: E402
from deepseek_subagent.bridges.opencode_go.lifecycle import process_creation_time  # noqa: E402
from deepseek_subagent.bridges.opencode_go.token_store import TOKEN_FILE, ensure_token  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--pid-file", default=None)
    args = parser.parse_args()

    # pythonw.exe has no console streams. Redirect them so incidental logging
    # cannot crash the managed background process.
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
    key_file = token_dir / "opencode-go.key"
    token_script = skill_root / "runtime" / "scripts" / "print_bridge_token.py"
    token, token_state = ensure_token(token_dir, legacy_workdir=workdir)
    codex_token_file = token_dir / TOKEN_FILE
    bridge_instance_id = str(uuid.uuid4())
    bridge = OpenCodeGoBridge(
        key_file=str(key_file),
        local_token=token,
        protocol_audit_dir=str(workdir),
        instance_id=bridge_instance_id,
    )
    handle = bridge.start(fixed_port=args.port or None)

    pid = os.getpid()
    info = {
        "port": handle.port,
        "base_url": handle.base_url,
        "pid": pid,
        "workdir": str(workdir),
        "pythonw": str(Path(sys.executable).resolve()),
        "script": str(Path(__file__).resolve()),
        "bridge_instance_id": bridge_instance_id,
        "bridge_abi_version": BRIDGE_ABI_VERSION,
        "launch_mode": "on_demand",
        "process_creation_time": process_creation_time(pid),
        "token_file": str(codex_token_file.resolve()),
        "token_script": str(token_script.resolve()),
        "token_version": token_state["token_version"],
        "token_generation": token_state["token_generation"],
        "token_fingerprint": token_state["token_fingerprint"],
    }
    (workdir / "bridge.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(info, ensure_ascii=False))
    print(f"bridge ready: {handle.base_url} (pid {pid}) - Ctrl+C to stop", flush=True)
    try:
        while not handle.server.shutdown_requested.wait(3600):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        handle.stop()
        _remove_pid()
        print("bridge stopped; persistent local token retained", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
