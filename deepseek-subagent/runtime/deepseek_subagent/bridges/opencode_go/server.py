"""本地 HTTP 服务器：Codex 面向接口。

- 仅监听 127.0.0.1，随机空闲端口；
- GET  /v1/models   → 上游 models → Codex models[] 转换；
- POST /v1/responses → 请求转换 → 上游 → 响应转换（含流式）；
- 本地令牌校验（Codex → 桥 Bearer），真实 Key 仅存在于桥内存；
- 日志脱敏：只记录方法/路径/状态/延迟/事件类型，不记录请求正文、
  响应正文、推理内容、API Key、Authorization、Cookie 或认证文件内容。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .auth import BridgeAuth
from .models import upstream_to_codex_models
from .request_transform import (
    TransformError,
    build_upstream_request,
    extract_additional_tools,
    extract_function_call_outputs,
    extract_tool_calls,
    replay_full_history,
    merge_tool_definitions,
    custom_tool_names,
    validate_and_dedupe_outputs,
)
from .response_transform import localize_response, parse_sse_lines, transform_stream_events
from .session_store import SessionStore
from .protocol_audit import ProtocolAudit
from .upstream import UPSTREAM_BASE, UPSTREAM_HEADERS, classify_upstream_failure


class UpstreamError(RuntimeError):
    def __init__(self, status: int, message: str, code: str = "upstream_request_failed", client_status: int = 502):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.client_status = client_status


def classified_upstream_error(status: int, body: str = "") -> UpstreamError:
    failure = classify_upstream_failure(status, body)
    return UpstreamError(status, failure.message, failure.code, failure.client_status)


def upstream_call(auth: BridgeAuth, path: str, payload: dict[str, Any] | None = None, method: str = "POST", timeout: float = 180.0) -> tuple[int, str]:
    headers = dict(UPSTREAM_HEADERS)
    headers["Authorization"] = "Bearer " + auth.bearer()
    req = urllib.request.Request(UPSTREAM_BASE + path, method=method, headers=headers)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(payload).encode()
    def send() -> tuple[int, str]:
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")
        except Exception as exc:
            failure = classify_upstream_failure(0)
            raise UpstreamError(0, failure.message, failure.code, failure.client_status) from exc

    return send()


class BridgeHandler(BaseHTTPRequestHandler):
    server: "BridgeServer"

    def log_message(self, fmt, *args):  # 静默默认日志
        return

    def _log(self, kind: str, detail: str) -> None:
        self.server.audit(kind, detail)

    def _check_auth(self) -> bool:
        header = self.headers.get("Authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        if not token or not self.server.auth.check_local(token):
            self._respond_json(401, {"error": {"type": "local_authentication_error", "code": "local_bridge_token_invalid", "message": "The localhost bridge token was rejected."}})
            return False
        return True

    def _respond_upstream_error(self, exc: UpstreamError) -> None:
        error_type = "authentication_error" if exc.code == "upstream_key_invalid" else "upstream_error"
        self._respond_json(
            exc.client_status,
            {"error": {"type": error_type, "code": exc.code, "message": exc.message}},
        )

    def _respond_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond_sse(self, lines: list[str]) -> None:
        body = ("\n\n".join(lines) + "\n\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path not in ("/health", "/v1/models"):
            self._respond_json(404, {"error": {"type": "invalid_request_error", "code": "not_found", "message": "unknown path"}})
            return
        if not self._check_auth():
            return
        if self.path == "/health":
            self._respond_json(200, {"status": "ok"})
            return
        start = time.monotonic()
        try:
            status, raw = upstream_call(self.server.auth, "/models", method="GET")
            if status != 200:
                raise classified_upstream_error(status, raw)
            converted = upstream_to_codex_models(json.loads(raw))
            self._respond_json(200, converted)
            self._log("models", f"status=200 latency={time.monotonic()-start:.2f}s models={len(converted.get('models', []))}")
        except UpstreamError as exc:
            self._respond_upstream_error(exc)
            self._log("models", f"upstream_error code={exc.code} status={exc.status}")
        except Exception as exc:
            self._respond_json(502, {"error": {"type": "upstream_error", "code": "upstream_response_invalid", "message": "OpenCode Go returned an invalid response."}})
            self._log("models", f"error status=502")

    def do_POST(self):
        if self.path != "/v1/responses":
            self._respond_json(404, {"error": {"type": "invalid_request_error", "code": "not_found", "message": "unknown path"}})
            return
        if not self._check_auth():
            return
        start = time.monotonic()
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length).decode("utf-8", "replace")
            codex_request = json.loads(raw_body)
        except Exception:
            self._respond_json(400, {"error": {"type": "invalid_request_error", "code": "invalid_request", "message": "invalid JSON body"}})
            return
        request_sequence = self.server.protocol_audit.request(codex_request, self.headers)
        try:
            stream = bool(codex_request.get("stream"))
            local_id = uuid.uuid4().hex
            input_types = sorted({i.get("type", "role") for i in (codex_request.get("input") or []) if isinstance(i, dict)})
            top_keys = sorted(k for k in codex_request.keys() if k not in ("input",))
            self.server.audit_lines.append(
                f"[responses meta] local_id={local_id} model={codex_request.get('model')} stream={stream} input_types={input_types} top_keys={top_keys}"
            )
            prev = codex_request.get("previous_response_id")
            history: list[dict[str, Any]] = []
            effective_input = list(codex_request.get("input") or [])
            nested_tools, effective_input = extract_additional_tools(effective_input)
            if prev:
                session = self.server.sessions.get(prev)
                if session is None:
                    self._respond_json(400, {"error": {"type": "invalid_request_error", "code": "invalid_previous_response_id", "message": f"unknown or expired session: {prev}"}})
                    self._log("responses", f"session_missing previous={prev} latency={time.monotonic()-start:.2f}s")
                    return
                history = session.history
                effective_input = validate_and_dedupe_outputs(history, effective_input)
            else:
                new_calls = extract_tool_calls(effective_input)
                if new_calls:
                    effective_input = replay_full_history(effective_input)
            transform_notes: list[str] = []
            top_tools = codex_request.get("tools") if isinstance(codex_request.get("tools"), list) else []
            effective_tools = merge_tool_definitions(top_tools, nested_tools)
            bridged_custom_names = custom_tool_names(effective_tools)
            effective_request = {**codex_request, "input": effective_input, "tools": effective_tools}
            upstream_request = build_upstream_request(effective_request, history, transform_notes)
            for note in transform_notes:
                self.server.audit_lines.append(f"[responses transform] {self.server.auth.redact(note)[:300]}")
            dropped = upstream_request.pop("_dropped_tool_types", None)
            tool_types = sorted({t.get("type") for t in (codex_request.get("tools") or []) if isinstance(t, dict)})
            audit = f"[responses tools] types={tool_types} count={len(codex_request.get('tools') or [])}"
            if dropped:
                audit += f" dropped={dropped}"
            self.server.audit_lines.append(audit)
            self.server.protocol_audit.upstream_request(request_sequence, upstream_request)
            status, raw = upstream_call(self.server.auth, "/responses", upstream_request)
            self.server.protocol_audit.upstream(request_sequence, status)
            if status != 200:
                self.server.audit_lines.append(f"[responses upstream] status={status}")
                raise classified_upstream_error(status, raw)
            if stream:
                events = parse_sse_lines(raw)
                self.server.protocol_audit.upstream_stream(request_sequence, events)
                lines = transform_stream_events(events, local_id, self.server.auth, bridged_custom_names)
                self.server.protocol_audit.stream_response(request_sequence, lines)
                emitted_kinds = []
                for line in lines:
                    try:
                        emitted_kinds.append(json.loads(line[6:]).get("type"))
                    except Exception:
                        emitted_kinds.append("NONJSON")
                self.server.audit_lines.append(f"[responses stream emitted] local_id={local_id} kinds={emitted_kinds}")
                self._respond_sse(lines)
                self.server.audit_stream(local_id, events)
            else:
                upstream = json.loads(raw)
                localized = localize_response(upstream, local_id, bridged_custom_names)
                self.server.protocol_audit.json_response(request_sequence, localized)
                self._respond_json(200, localized)
                self.server.audit_response(local_id, upstream)
            # 会话更新：记录上游输出中的 function_call / message，构造显式上下文历史
            self.server.update_session(local_id, prev, codex_request, history, raw, effective_input, bridged_custom_names)
            self._log("responses", f"status={status} stream={stream} latency={time.monotonic()-start:.2f}s local_id={local_id}")
        except TransformError as exc:
            self._respond_json(400, {"error": {"type": "invalid_request_error", "code": "transform_error", "message": self.server.auth.redact(str(exc))}})
            self._log("responses", f"transform_error latency={time.monotonic()-start:.2f}s")
        except UpstreamError as exc:
            self._respond_upstream_error(exc)
            self._log("responses", f"upstream_error code={exc.code} status={exc.status} latency={time.monotonic()-start:.2f}s")
        except Exception as exc:
            self.server.audit_lines.append(f"[responses internal] {self.server.auth.redact(repr(exc))[:500]}")
            self._respond_json(500, {"error": {"type": "server_error", "code": "internal", "message": self.server.auth.redact(str(exc))}})
            self._log("responses", f"internal_error latency={time.monotonic()-start:.2f}s")


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, auth: BridgeAuth, sessions: SessionStore, port: int = 0, protocol_audit: ProtocolAudit | None = None):
        super().__init__(("127.0.0.1", port), BridgeHandler)
        self.auth = auth
        self.sessions = sessions
        self.audit_lines: list[str] = []
        self.protocol_audit = protocol_audit or ProtocolAudit()

    def audit(self, kind: str, detail: str) -> None:
        self.audit_lines.append(f"[{kind}] {detail}")

    def audit_stream(self, local_id: str, events: list[dict[str, Any]]) -> None:
        kinds = sorted({e.get("type") for e in events if e.get("type")})
        self.audit_lines.append(f"[responses stream] local_id={local_id} event_kinds={kinds}")

    def audit_response(self, local_id: str, upstream: dict[str, Any]) -> None:
        kinds = [o.get("type") for o in upstream.get("output", [])]
        self.audit_lines.append(f"[responses body] local_id={local_id} output_types={kinds}")

    def update_session(
        self,
        local_id: str,
        prev: str | None,
        codex_request: dict[str, Any],
        history: list[dict[str, Any]],
        upstream_raw: str,
        effective_input: list[dict[str, Any]] | None = None,
        custom_tool_names: set[str] | None = None,
    ) -> None:
        from .response_transform import normalize_response, normalize_stream_events, parse_sse_lines

        model = codex_request.get("model")
        new_items = [
            item
            for item in (effective_input if effective_input is not None else (codex_request.get("input") or []))
            if isinstance(item, dict)
        ]
        if prev:
            session = self.sessions.get(prev)
            if session is None:
                return
            history = session.history
        history = list(history)
        for item in new_items:
            history.append(item)
        try:
            upstream = json.loads(upstream_raw) if not codex_request.get("stream") else None
            if upstream is None and codex_request.get("stream"):
                events = normalize_stream_events(parse_sse_lines(upstream_raw), custom_tool_names)
                outputs = []
                for event in events:
                    if event.get("type") == "response.output_item.done" and isinstance(event.get("item"), dict):
                        outputs.append(event["item"])
                upstream = {"output": outputs}
        except Exception:
            upstream = {"output": []}
        upstream = normalize_response(upstream, custom_tool_names)
        seen_outputs: set[tuple[str, str]] = set()
        for item in upstream.get("output") or []:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "")
            if item_type not in ("reasoning", "message", "function_call", "custom_tool_call"):
                continue
            identity = str(item.get("id") or item.get("call_id") or "")
            key = (item_type, identity)
            if identity and key in seen_outputs:
                continue
            if identity:
                seen_outputs.add(key)
            history.append(item)
        upstream_id = upstream.get("id") if isinstance(upstream, dict) else None
        if prev:
            self.sessions.update(prev, upstream_id or "", history)
        self.sessions.create(local_id, upstream_id or "", model or "", history)
