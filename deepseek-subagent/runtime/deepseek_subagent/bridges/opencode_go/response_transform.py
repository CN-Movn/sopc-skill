"""响应转换：上游 /v1/responses → Codex 期望的 Responses 格式。

- 非流式：替换 response id 为本地 id（Codex 用它作为 previous_response_id），
  其余输出结构（message/function_call items、usage）透传。
- 流式：解析上游 SSE 事件，注入本地 id，并补齐标准 Responses 终态事件
  （output_text.done / content_part.done / output_item.done /
  function_call_arguments.done），过滤非 JSON 心跳行。
"""

from __future__ import annotations

import json
import html
import re
import uuid
from typing import Any, Iterable

from .auth import BridgeAuth


def localize_response(upstream: dict[str, Any], local_id: str, custom_tool_names: set[str] | None = None) -> dict[str, Any]:
    out = normalize_response(upstream, custom_tool_names)
    out["id"] = local_id
    out.setdefault("object", "response")
    out.setdefault("status", "completed")
    return out


# DeepSeek may emit its native DSML tool syntax as ordinary assistant text when
# the upstream adapter does not recognize the requested tool schema.  Convert
# only the well-delimited invoke blocks; unrelated assistant text remains text.
_INVOKE_RE = re.compile(
    r"<(?:(?:[^>]*DSML[^>]*)?)invoke\s+name\s*=\s*(?:(['\"])(?P<quoted_name>[^'\"]+)\1|(?P<bare_name>[^\s>]+))\s*>(?P<body>.*?)</(?:(?:[^>]*DSML[^>]*)?)invoke>",
    re.IGNORECASE | re.DOTALL,
)
_PARAM_RE = re.compile(
    r"<(?:(?:[^>]*DSML[^>]*)?)parameter\b(?P<attrs>[^>]*)>(?P<body>.*?)</(?:(?:[^>]*DSML[^>]*)?)parameter>",
    re.IGNORECASE | re.DOTALL,
)
_ATTR_RE = re.compile(
    r"(?P<key>[A-Za-z_][\w-]*)\s*=\s*(?:(['\"])(?P<quoted_value>.*?)\2|(?P<bare_value>[^\s>]+))",
    re.DOTALL,
)
_DSML_OPEN_RE = re.compile(r"<[^>]*DSML[^>]*(?:tools_call|tool_calls|function_calls)[^>]*>", re.IGNORECASE)
_DSML_CLOSE_RE = re.compile(r"</[^>]*DSML[^>]*(?:tools_call|tool_calls|function_calls)[^>]*>", re.IGNORECASE)


def _attrs(raw: str) -> dict[str, str]:
    return {
        m.group("key").lower(): html.unescape(m.group("quoted_value") or m.group("bare_value") or "")
        for m in _ATTR_RE.finditer(raw)
    }


def _text_from_content(item: dict[str, Any]) -> str:
    text = item.get("text")
    if isinstance(text, str):
        return text
    content = item.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        value = part.get("text")
        if isinstance(value, str):
            parts.append(value)
    return "".join(parts)


def parse_dsml_tool_calls(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Extract DeepSeek DSML invoke blocks and return (remaining_text, calls)."""
    if not isinstance(text, str) or "invoke" not in text.lower():
        return text, []
    calls: list[dict[str, Any]] = []
    consumed: list[tuple[int, int]] = []
    for invoke in _INVOKE_RE.finditer(text):
        name = html.unescape(invoke.group("quoted_name") or invoke.group("bare_name") or "").strip()
        if not name:
            continue
        arguments: dict[str, Any] = {}
        unnamed: list[str] = []
        for parameter in _PARAM_RE.finditer(invoke.group("body")):
            attrs = _attrs(parameter.group("attrs"))
            value = html.unescape(parameter.group("body")).strip()
            key = attrs.get("name") or attrs.get("key")
            if key:
                arguments[key] = value
            else:
                unnamed.append(value)
        if unnamed:
            # DeepSeek's common exec_command form omits the parameter name.
            key = "cmd" if name == "exec_command" else "input"
            if key not in arguments:
                arguments[key] = unnamed[0] if len(unnamed) == 1 else unnamed
        calls.append({
            "id": "dsml_" + uuid.uuid4().hex[:20],
            "type": "function_call",
            "status": "completed",
            "call_id": "call_dsml_" + uuid.uuid4().hex[:20],
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        })
        consumed.append(invoke.span())
    if not calls:
        return text, []
    remaining = text
    for start, end in reversed(consumed):
        remaining = remaining[:start] + remaining[end:]
    remaining = _DSML_OPEN_RE.sub("", remaining)
    remaining = _DSML_CLOSE_RE.sub("", remaining)
    return remaining.strip(), calls


def _custom_input(arguments: Any) -> str:
    if not isinstance(arguments, str):
        return ""
    try:
        decoded = json.loads(arguments)
    except json.JSONDecodeError:
        return arguments
    if isinstance(decoded, dict) and isinstance(decoded.get("input"), str):
        return decoded["input"]
    return arguments


def _function_as_custom(item: dict[str, Any], names: set[str]) -> dict[str, Any]:
    if item.get("type") != "function_call" or item.get("name") not in names:
        return item
    return {
        "id": item.get("id"),
        "type": "custom_tool_call",
        "status": item.get("status"),
        "call_id": item.get("call_id"),
        "name": item.get("name"),
        "input": _custom_input(item.get("arguments")),
    }


def normalize_response(upstream: dict[str, Any], custom_tool_names: set[str] | None = None) -> dict[str, Any]:
    """Promote DSML embedded in message output into Responses function_call items."""
    out = dict(upstream)
    outputs = out.get("output")
    if not isinstance(outputs, list):
        return out
    names = custom_tool_names or set()
    normalized: list[dict[str, Any]] = []
    for item in outputs:
        if isinstance(item, dict) and item.get("type") == "function_call" and item.get("name") in names:
            normalized.append(_function_as_custom(item, names))
            continue
        if not isinstance(item, dict) or item.get("type") != "message":
            normalized.append(item)
            continue
        text = _text_from_content(item)
        remaining, calls = parse_dsml_tool_calls(text)
        if not calls:
            normalized.append(item)
            continue
        if remaining:
            message = dict(item)
            message["content"] = [{"type": "output_text", "text": remaining, "annotations": []}]
            message.pop("text", None)
            normalized.append(message)
        normalized.extend(calls)
    out["output"] = normalized
    return out


def normalize_custom_tool_events(events: Iterable[dict[str, Any]], names: set[str]) -> list[dict[str, Any]]:
    if not names:
        return list(events)
    source = list(events)
    custom_ids: dict[str, dict[str, Any]] = {}
    for event in source:
        if event.get("type") not in ("response.output_item.added", "response.output_item.done"):
            continue
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if item.get("type") == "function_call" and item.get("name") in names:
            item_id = str(item.get("id") or item.get("call_id") or "")
            if item_id:
                custom_ids[item_id] = item
    if not custom_ids:
        return source
    out: list[dict[str, Any]] = []
    for event in source:
        etype = event.get("type")
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        item_id = str(event.get("item_id") or item.get("id") or item.get("call_id") or "")
        if item_id not in custom_ids:
            out.append(event)
            continue
        original = custom_ids[item_id]
        if etype == "response.output_item.added":
            out.append({
                **event,
                "item": {
                    "id": original.get("id"),
                    "type": "custom_tool_call",
                    "status": "in_progress",
                    "call_id": original.get("call_id"),
                    "name": original.get("name"),
                    "input": "",
                },
            })
            continue
        if etype == "response.function_call_arguments.delta":
            continue
        if etype == "response.function_call_arguments.done":
            custom_input = _custom_input(event.get("arguments"))
            if custom_input:
                out.append({"type": "response.custom_tool_call_input.delta", "output_index": event.get("output_index", 0), "item_id": item_id, "delta": custom_input})
            out.append({"type": "response.custom_tool_call_input.done", "output_index": event.get("output_index", 0), "item_id": item_id, "input": custom_input})
            continue
        if etype == "response.output_item.done":
            out.append({**event, "item": _function_as_custom(item, names)})
            continue
        out.append(event)
    return out


def normalize_stream_events(events: Iterable[dict[str, Any]], custom_tool_names: set[str] | None = None) -> list[dict[str, Any]]:
    """Convert DSML text in a streamed message into structured function events."""
    source = normalize_custom_tool_events(events, custom_tool_names or set())
    texts: dict[str, str] = {}
    for event in source:
        if event.get("type") == "response.output_text.delta":
            item_id = str(event.get("item_id") or "")
            texts[item_id] = texts.get(item_id, "") + str(event.get("delta") or "")
        elif event.get("type") == "response.output_text.done":
            item_id = str(event.get("item_id") or "")
            if isinstance(event.get("text"), str):
                texts[item_id] = event["text"]
    parsed: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for item_id, text in texts.items():
        remaining, calls = parse_dsml_tool_calls(text)
        if calls:
            parsed[item_id] = (remaining, calls)
    if not parsed:
        return source

    out: list[dict[str, Any]] = []
    inserted: set[str] = set()
    emitted_text: set[str] = set()
    for event in source:
        etype = event.get("type")
        item_id = str(event.get("item_id") or "")
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        event_item_id = str(item.get("id") or "")
        target_id = item_id or event_item_id
        if target_id not in parsed:
            out.append(event)
            continue
        remaining, calls = parsed[target_id]
        if etype == "response.output_text.delta":
            if remaining and target_id not in emitted_text:
                out.append({**event, "delta": remaining})
                emitted_text.add(target_id)
            continue
        if etype == "response.output_text.done":
            if remaining:
                out.append({**event, "text": remaining})
            continue
        if etype in ("response.content_part.added", "response.content_part.done"):
            if not remaining:
                continue
            rewritten = dict(event)
            part = event.get("part") if isinstance(event.get("part"), dict) else {}
            rewritten["part"] = {**part, "type": "output_text", "text": remaining, "annotations": part.get("annotations", [])}
            out.append(rewritten)
            continue
        if etype == "response.output_item.done" and item.get("type") == "message":
            if remaining:
                rewritten = dict(event)
                rewritten["item"] = {**item, "content": [{"type": "output_text", "text": remaining, "annotations": []}]}
                out.append(rewritten)
            if target_id not in inserted:
                out.extend(_function_events(calls))
                inserted.add(target_id)
            continue
        if etype == "response.output_item.added" and item.get("type") == "message" and not remaining:
            # Replace the empty DSML message with the function-call sequence.
            if target_id not in inserted:
                out.extend(_function_events(calls))
                inserted.add(target_id)
            continue
        out.append(event)
    for target_id, (_remaining, calls) in parsed.items():
        if target_id not in inserted:
            out.extend(_function_events(calls))
    return out


def _function_events(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for call in calls:
        item = {k: call[k] for k in ("id", "type", "status", "name", "call_id", "arguments")}
        events.extend([
            {"type": "response.output_item.added", "output_index": 0, "item": {**item, "status": "in_progress", "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "output_index": 0, "item_id": call["id"], "delta": call["arguments"]},
            {"type": "response.function_call_arguments.done", "output_index": 0, "item_id": call["id"], "arguments": call["arguments"]},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
        ])
    return events


def parse_sse_lines(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            events.append(json.loads(data))
        except json.JSONDecodeError:
            continue
    return events


def transform_stream_events(
    events: Iterable[dict[str, Any]],
    local_id: str,
    auth: BridgeAuth,
    custom_tool_names: set[str] | None = None,
) -> list[str]:
    """转换上游 SSE 事件为 Codex 可消费的 SSE 行（UTF-8 文本）。"""

    events = normalize_stream_events(events, custom_tool_names)
    out_lines: list[str] = []
    open_text: dict[str, Any] | None = None
    open_function: dict[str, Any] | None = None
    seen_completed = False
    seen_created = False
    emitted_terminal_keys: set[tuple[str, str]] = set()
    completed_outputs: dict[str, dict[str, Any]] = {}
    completed_output_order: list[str] = []

    def emit(obj: dict[str, Any]) -> None:
        etype = str(obj.get("type") or "")
        identity = ""
        if etype in ("response.output_item.added", "response.output_item.done"):
            item = obj.get("item") if isinstance(obj.get("item"), dict) else {}
            identity = str(item.get("id") or item.get("call_id") or "")
        elif etype in (
            "response.function_call_arguments.done",
            "response.output_text.done",
            "response.content_part.done",
        ):
            identity = str(obj.get("item_id") or "")
        if identity and etype.endswith((".added", ".done")):
            key = (etype, identity)
            if key in emitted_terminal_keys:
                return
            emitted_terminal_keys.add(key)
        if etype == "response.output_item.done":
            item = obj.get("item") if isinstance(obj.get("item"), dict) else None
            if item is not None:
                item_key = str(item.get("id") or item.get("call_id") or len(completed_output_order))
                if item_key not in completed_outputs:
                    completed_output_order.append(item_key)
                completed_outputs[item_key] = dict(item)
        out_lines.append("data: " + json.dumps(obj, ensure_ascii=False))

    def completed_response(source: dict[str, Any] | None = None) -> dict[str, Any]:
        upstream = source if isinstance(source, dict) else {}
        response = {
            **upstream,
            "id": local_id,
            "object": "response",
            "status": "completed",
            "output": [completed_outputs[key] for key in completed_output_order],
        }
        return response

    def ensure_created() -> None:
        nonlocal seen_created
        if seen_created:
            return
        seen_created = True
        emit({"type": "response.created", "response": {"id": local_id, "object": "response", "status": "in_progress", "model": None}})

    def ensure_text_started(item_id: Any) -> None:
        if open_text is None:
            return
        if not item_id:
            item_id = "msg_" + uuid.uuid4().hex[:16]
        open_text["id"] = item_id
        if not open_text.get("item_added"):
            open_text["item_added"] = True
            emit({"type": "response.output_item.added", "output_index": 0, "item": {"id": item_id, "type": "message", "status": "in_progress", "role": "assistant", "content": []}})
        if not open_text.get("content_added"):
            open_text["content_added"] = True
            emit({"type": "response.content_part.added", "item_id": item_id, "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}})

    def done_events_for(item: dict[str, Any]) -> list[dict[str, Any]]:
        item_id = item.get("id") or item.get("item_id") or item.get("call_id")
        if not item_id:
            return []
        if item.get("type") == "message":
            content_id = f"content_{item_id}"
            final_text = _text_from_content(item)
            return [
                {"type": "response.output_text.done", "item_id": item_id, "output_index": 0, "content_index": 0, "text": final_text},
                {"type": "response.content_part.done", "item_id": item_id, "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": final_text, "annotations": []}},
                {"type": "response.output_item.done", "output_index": 0, "item": {"id": item_id, "type": "message", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": final_text, "annotations": []}]}},
            ]
        if item.get("type") == "function_call":
            return [
                {"type": "response.function_call_arguments.done", "item_id": item_id, "output_index": 0, "arguments": item.get("arguments", "")},
                {"type": "response.output_item.done", "output_index": 0, "item": {"id": item_id, "type": "function_call", "status": "completed", "call_id": item.get("call_id"), "name": item.get("name"), "arguments": item.get("arguments", "")}},
            ]
        return []

    for event in events:
        etype = event.get("type")
        if etype in ("ping", "heartbeat"):
            continue
        if etype == "error":
            emit({"type": "error", "code": event.get("code") or "upstream_error", "message": auth.redact(str(event.get("message", "")))})
            continue
        if etype == "response.created":
            if seen_created:
                continue
            seen_created = True
            emit({"type": "response.created", "response": {"id": local_id, "object": "response", "status": "in_progress", "model": event.get("response", {}).get("model") if isinstance(event.get("response"), dict) else None}})
            continue
        if isinstance(etype, str) and etype.startswith("response."):
            ensure_created()
        localized = dict(event)
        if isinstance(event.get("response"), dict):
            localized["response"] = {**event["response"], "id": local_id}
        if etype == "response.output_text.delta":
            ensure_created()
            item_id = event.get("item_id")
            if open_text is None:
                open_text = {"id": item_id, "type": "message", "text": ""}
            ensure_text_started(item_id)
            open_text["text"] += event.get("delta", "")
            if item_id:
                open_text["id"] = item_id
            emit(localized)
            continue
        if etype == "response.output_text.done":
            if open_text is not None:
                open_text["text"] = event.get("text", open_text["text"])
            emit(localized)
            continue
        if etype == "response.function_call_arguments.delta":
            if open_function is None:
                open_function = {"id": event.get("item_id"), "type": "function_call", "arguments": ""}
            open_function["arguments"] += event.get("delta", "")
            open_function["id"] = event.get("item_id", open_function["id"])
            emit(localized)
            continue
        if etype == "response.function_call_arguments.done":
            if open_function is not None:
                open_function["arguments"] = event.get("arguments", open_function["arguments"])
            emit(localized)
            continue
        if etype == "response.output_item.added":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                open_function = {
                    "id": item.get("id") or item.get("call_id"),
                    "type": "function_call",
                    "call_id": item.get("call_id"),
                    "name": item.get("name"),
                    "arguments": "",
                }
            elif item.get("type") == "message":
                open_text = {"id": item.get("id"), "type": "message", "text": "", "item_added": True, "content_added": False}
            emit(localized)
            continue
        if etype == "response.content_part.added":
            if open_text is not None:
                open_text["content_added"] = True
            emit(localized)
            continue
        if etype == "response.output_item.done":
            item = event.get("item") or {}
            for done in done_events_for(item):
                emit(done)
            emit(localized)
            continue
        if etype == "response.completed":
            seen_completed = True
            ensure_created()
            if open_text is not None:
                for done in done_events_for(open_text):
                    emit(done)
            if open_function is not None:
                for done in done_events_for(open_function):
                    emit(done)
            emit({"type": "response.completed", "response": completed_response(event.get("response"))})
            continue
        emit(localized)

    if not seen_completed:
        if open_text is not None:
            for done in done_events_for(open_text):
                emit(done)
        if open_function is not None:
            for done in done_events_for(open_function):
                emit(done)
        emit({"type": "response.completed", "response": completed_response()})
    return out_lines
