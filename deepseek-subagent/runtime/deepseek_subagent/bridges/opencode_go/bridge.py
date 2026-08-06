"""OpenCode Go → Codex 本地兼容桥。

方案选择（方案 A：桥调用上游 /v1/responses）：
- 上游 responses 输出结构（message/function_call items、usage）与 Codex
  期望接近，响应转换最少；
- 上游全部约束（chat 风格工具 schema、显式 assistant 上下文、
  reasoning_content、同轮多 function_call 合并）已由最小探针验证，
  本桥负责在请求侧完成这些转换；
- 方案 B（桥调用 /chat/completions）需要完整 Responses↔Chat 双向转换
  （消息、工具、流式事件），复杂度更高，留作后续备选。

部署边界：仅监听 127.0.0.1 随机端口；不写系统服务；不长期后台运行；
探针结束后 stop() 退出并清理；日志只含方法/路径/状态/延迟/事件类型。
"""

from __future__ import annotations

import threading
from typing import Any

from .auth import BridgeAuth
from .server import BridgeServer
from .session_store import SessionStore
from .protocol_audit import ProtocolAudit


class BridgeHandle:
    def __init__(self, server: BridgeServer, auth: BridgeAuth, thread: threading.Thread):
        self.server = server
        self.auth = auth
        self.thread = thread

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def local_token(self) -> str:
        return self.auth.local_token

    def audit_lines(self) -> list[str]:
        return list(self.server.audit_lines)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self.thread.is_alive():
            self.thread.join(timeout=5)
        self.auth.clear()


class OpenCodeGoBridge:
    def __init__(self, key_file: str | None = None, local_token: str | None = None, max_sessions: int = 100, ttl_seconds: float = 1800.0, protocol_audit_dir: str | None = None):
        self.auth = BridgeAuth(local_token=local_token, key_file=key_file)
        self.sessions = SessionStore(max_sessions=max_sessions, ttl_seconds=ttl_seconds)
        self.protocol_audit = ProtocolAudit(protocol_audit_dir)

    def start(self, fixed_port: int | None = None) -> BridgeHandle:
        self.auth.load()
        server = BridgeServer(self.auth, self.sessions, port=fixed_port or 0, protocol_audit=self.protocol_audit)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return BridgeHandle(server, self.auth, thread)


__all__ = ["BridgeHandle", "OpenCodeGoBridge"]
