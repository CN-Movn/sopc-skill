"""Opt-in, structure-only protocol audit for bridge diagnostics.

Create ``protocol-audit.enabled`` in the bridge work directory to enable.
Delete it to disable.  The JSONL output never records prompts, source text,
tool output, credentials, tokens, authorization, or cookies.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

AUDIT_MARKER = "protocol-audit.enabled"
AUDIT_FILE = "protocol-audit.jsonl"


def _masked(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _item_summary(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"type": type(item).__name__}
    content = item.get("content") if isinstance(item.get("content"), list) else []
    content_types: list[Any] = []
    text_length = 0
    for part in content:
        if not isinstance(part, dict):
            content_types.append(type(part).__name__)
            continue
        content_types.append(part.get("type"))
        if isinstance(part.get("text"), str):
            text_length += len(part["text"])
    nested_tools = item.get("tools") if isinstance(item.get("tools"), list) else []
    return {
        "type": item.get("type") or ("message" if item.get("role") else None),
        "id": item.get("id"),
        "call_id": item.get("call_id"),
        "name": item.get("name"),
        "role": item.get("role"),
        "status": item.get("status"),
        "content_types": content_types,
        "content_text_length": text_length,
        "has_encrypted_content": bool(item.get("encrypted_content")),
        "nested_tool_count": len(nested_tools),
        "nested_tool_names": [
            tool.get("name") or (
                (tool.get("function") or {}).get("name")
                if isinstance(tool.get("function"), dict) else None
            )
            for tool in nested_tools
            if isinstance(tool, dict)
        ],
        "nested_tool_shapes": [
            {
                "name": tool.get("name") or (
                    (tool.get("function") or {}).get("name")
                    if isinstance(tool.get("function"), dict) else None
                ),
                "type": tool.get("type"),
                "keys": sorted(tool.keys()),
                "function_keys": sorted((tool.get("function") or {}).keys())
                if isinstance(tool.get("function"), dict) else [],
            }
            for tool in nested_tools
            if isinstance(tool, dict)
        ],
    }


class ProtocolAudit:
    def __init__(self, workdir: str | Path | None = None) -> None:
        self.workdir = Path(workdir) if workdir else None
        self._lock = threading.Lock()
        self._sequence = 0

    @property
    def enabled(self) -> bool:
        return bool(self.workdir and (self.workdir / AUDIT_MARKER).is_file())

    def _emit(self, record: dict[str, Any]) -> None:
        if not self.enabled or self.workdir is None:
            return
        safe = {"at": datetime.now().isoformat(timespec="milliseconds"), **record}
        encoded = json.dumps(safe, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            with (self.workdir / AUDIT_FILE).open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)

    def request(self, payload: dict[str, Any], headers: Mapping[str, str]) -> int:
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        inputs = payload.get("input") if isinstance(payload.get("input"), list) else []
        tools = payload.get("tools") if isinstance(payload.get("tools"), list) else []
        summaries = [_item_summary(item) for item in inputs]
        identifiers: dict[str, Any] = {}
        for key in ("response_id", "thread_id", "session_id", "conversation_id"):
            if payload.get(key) is not None:
                identifiers[key] = _masked(payload.get(key))
        header_ids: dict[str, Any] = {}
        for key, value in headers.items():
            lowered = key.lower()
            if any(part in lowered for part in ("thread", "session", "conversation", "response")):
                header_ids[lowered] = _masked(value)
        self._emit({
            "kind": "codex_request",
            "request_sequence": sequence,
            "endpoint": "/v1/responses",
            "stream": bool(payload.get("stream")),
            "store": payload.get("store"),
            "previous_response_id_present": payload.get("previous_response_id") is not None,
            "previous_response_id": _masked(payload.get("previous_response_id")),
            "input_item_types": [item.get("type") for item in summaries],
            "input_items": summaries,
            "tools_count": len(tools),
            "tool_names": [
                tool.get("name") or ((tool.get("function") or {}).get("name") if isinstance(tool, dict) else None)
                for tool in tools
                if isinstance(tool, dict)
            ],
            "contains_function_call": any(item.get("type") == "function_call" for item in summaries),
            "contains_function_call_output": any(item.get("type") == "function_call_output" for item in summaries),
            "identifiers": identifiers,
            "header_identifiers": header_ids,
        })
        return sequence

    def rejection(self, sequence: int, code: str) -> None:
        self._emit({"kind": "bridge_rejection", "request_sequence": sequence, "code": code})

    def upstream(self, sequence: int, status: int) -> None:
        self._emit({"kind": "upstream_response", "request_sequence": sequence, "http_status": status})

    def upstream_request(self, sequence: int, payload: dict[str, Any]) -> None:
        inputs = payload.get("input") if isinstance(payload.get("input"), list) else []
        self._emit({
            "kind": "upstream_request",
            "request_sequence": sequence,
            "stream": bool(payload.get("stream")),
            "input_item_types": [
                _item_summary(item).get("type") for item in inputs
            ],
            "input_items": [_item_summary(item) for item in inputs],
            "reasoning_item_count": sum(
                1 for item in inputs if isinstance(item, dict) and item.get("type") == "reasoning"
            ),
        })

    def upstream_stream(self, sequence: int, events: list[dict[str, Any]]) -> None:
        done_items = [
            event.get("item")
            for event in events
            if event.get("type") == "response.output_item.done"
            and isinstance(event.get("item"), dict)
        ]
        self._emit({
            "kind": "upstream_stream_response",
            "request_sequence": sequence,
            "event_types": [event.get("type") for event in events],
            "output_items": [_item_summary(item) for item in done_items],
            "reasoning_item_count": sum(
                1 for item in done_items if item.get("type") == "reasoning"
            ),
        })

    def stream_response(self, sequence: int, lines: list[str]) -> None:
        events: list[dict[str, Any]] = []
        for line in lines:
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            events.append(event)
        items = []
        response_id = None
        completed_fields: list[str] = []
        for event in events:
            response = event.get("response") if isinstance(event.get("response"), dict) else {}
            response_id = response.get("id") or response_id
            if event.get("type") == "response.completed":
                completed_fields = sorted(response.keys())
            item = event.get("item") if isinstance(event.get("item"), dict) else None
            if item:
                items.append(_item_summary(item))
        self._emit({
            "kind": "bridge_stream_response",
            "request_sequence": sequence,
            "response_id": response_id,
            "event_types": [event.get("type") for event in events],
            "output_items": items,
            "response_completed_fields": completed_fields,
        })

    def json_response(self, sequence: int, response: dict[str, Any]) -> None:
        outputs = response.get("output") if isinstance(response.get("output"), list) else []
        self._emit({
            "kind": "bridge_json_response",
            "request_sequence": sequence,
            "response_id": response.get("id"),
            "previous_response_id": _masked(response.get("previous_response_id")),
            "output_items": [_item_summary(item) for item in outputs],
            "response_fields": sorted(response.keys()),
        })
