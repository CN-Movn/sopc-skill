"""请求转换：Codex Responses 请求 → OpenCode Go 可接受的显式上下文。

要点（探针/实验验证的网关约束）：
1. 工具 schema 必须为顶层 name 形态
   {type:"function", name, description, parameters}（responses 风格）；
   function 嵌套形态会被网关拒绝（tools[i].function 缺 name，实验确认）。
2. 工具结果回传需要显式 assistant 上下文（chat 风格 tool_calls），
   不能只依赖 previous_response_id；
3. 同轮多个 function_call 必须合并为单条 assistant 消息；
4. 多轮回传 assistant 消息必须显式携带 reasoning_content 字段。
"""

from __future__ import annotations

import json
from typing import Any


class TransformError(ValueError):
    def __init__(self, message: str, code: str = "transform_error") -> None:
        super().__init__(message)
        self.code = code


_TEXT_CONTENT_TYPES = ("input_text", "text", "output_text")
_OPAQUE_CONTENT_TYPES = ("encrypted_content",)
_CALL_TYPES = ("function_call", "custom_tool_call")
_OUTPUT_TYPES = ("function_call_output", "custom_tool_call_output")


def _is_empty_v2_envelope(segments: list[str], content: list[dict[str, Any]]) -> bool:
    """检测 Codex 桌面 v2 加密任务形态：明文只有 NEW_TASK 信封，
    Payload 之后无实质正文，且存在不透明（encrypted_content）负载。
    该形态下模型只会收到空任务（探测已 100% 复现 'payload is empty' 假响应），
    应明确报错而非假成功。"""
    has_opaque = any(
        isinstance(p, dict) and p.get("type") in _OPAQUE_CONTENT_TYPES for p in content
    )
    if not has_opaque:
        return False
    joined = "\n".join(segments).strip()
    if "Message Type:" not in joined and "NEW_TASK" not in joined:
        return False
    if "Payload:" in joined:
        rest = joined.split("Payload:", 1)[1].strip()
    else:
        rest = joined
    body_lines = [
        line.strip()
        for line in rest.splitlines()
        if line.strip() and not line.strip().startswith(("Task name:", "Sender:"))
    ]
    return len(" ".join(body_lines)) < 40


def normalize_agent_message(item: dict[str, Any], notes: list[str]) -> dict[str, Any]:
    """把 Codex 桌面多 Agent 的 agent_message 输入转换为标准 user 消息。

    真实结构（2026-08-05 桌面验收提取）：
      {type: "agent_message", id, author, recipient,
       content: [{type: "input_text", text}, {type: "encrypted_content", ...}],
       internal_chat_message_metadata_passthrough: {turn_id}}

    转换规则：
    - content 中文本类 item（input_text/text/output_text 的 text 字段）按顺序
      拼接为 user 消息正文，多段不丢段；
    - 不透明类 item（encrypted_content）不转发，记录审计；
    - author/recipient/internal_chat_message_metadata_passthrough 为父 Agent
      控制字段，不进入正文，记录审计；
    - content 缺失或没有任何文本段 → TransformError（不静默丢弃）；
    - 无法安全解释的 content item 类型 → TransformError。
    """

    if not isinstance(item, dict):
        raise TransformError("agent_message 必须是对象")
    content = item.get("content")
    if not isinstance(content, list):
        raise TransformError("agent_message 缺少 content 数组（无法提取任务正文）")
    segments: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            raise TransformError("agent_message content 项必须是对象")
        ptype = part.get("type")
        if ptype in _TEXT_CONTENT_TYPES:
            text = part.get("text")
            if not isinstance(text, str):
                raise TransformError(f"agent_message 文本项 {ptype!r} 缺少 text 字段")
            segments.append(text)
        elif ptype in _OPAQUE_CONTENT_TYPES:
            notes.append(f"agent_message 跳过不透明 content 项：{ptype!r}（不转发）")
        else:
            raise TransformError(f"agent_message 无法安全解释的 content 项类型：{ptype!r}")
    if not segments:
        raise TransformError("agent_message 没有任何可用的任务正文")
    if _is_empty_v2_envelope(segments, content):
        raise TransformError(
            "当前任务错误地使用了 Multi-Agent V2：agent_message 的任务负载仅存在于 "
            "encrypted_content 中。DeepSeek cross-provider 只允许 V1；桥不会解密或猜测 V2 "
            "负载。请让 V1 配置生效后创建新的 Codex task；如需继续工作，只能在用户明确授权后"
            "创建继任 Agent，并从项目交接日志接续。",
            code="current_task_multi_agent_v2",
        )
    if item.get("author") is not None:
        notes.append(f"agent_message author 控制字段未进入正文（author={item.get('author')!r}）")
    if item.get("recipient") is not None:
        notes.append(f"agent_message recipient 控制字段未进入正文（recipient={item.get('recipient')!r}）")
    if item.get("internal_chat_message_metadata_passthrough") is not None:
        notes.append("agent_message internal_chat_message_metadata_passthrough 未转发")
    return {
        "role": "user",
        "content": [{"type": "input_text", "text": "\n".join(segments)}],
    }


def normalize_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """把 Codex 工具定义转换为网关接受的顶层 name 形态。

    支持：
    - 顶层 name 形态：{type, name, description, parameters}（原样保留）；
    - function 嵌套形态：{type, function:{name, ...}} → 字段提升到顶层；
    - 混合形态：{type, name, function:{...}} → function 字段补全顶层。
    function 内部未知字段全部保留（提升到顶层；网关容忍未知字段）。
    """

    if not isinstance(tool, dict):
        raise TransformError(f"tool 必须是对象，得到 {type(tool).__name__}")
    ttype = tool.get("type")
    if ttype != "function":
        # 非 function 类型（如 Codex 的 namespace 工具）由 normalize_tools 过滤
        return dict(tool)
    fn = tool.get("function")
    if fn is None:
        out = dict(tool)
        if not out.get("name"):
            raise TransformError("tool 缺少 name")
        return out
    if not isinstance(fn, dict):
        raise TransformError("tool.function 必须是对象")
    name = tool.get("name") or fn.get("name")
    if not name:
        raise TransformError("tool 缺少 name（function 嵌套与顶层均无）")
    out: dict[str, Any] = {"type": "function", "name": name}
    for key in ("description", "parameters"):
        if key in fn:
            out[key] = fn[key]
        elif key in tool:
            out[key] = tool[key]
    for key, value in fn.items():
        if key not in ("name", "description", "parameters"):
            out[key] = value
    for key, value in tool.items():
        if key not in ("type", "function", "name", "description", "parameters"):
            out[key] = value
    return out


def custom_tool_as_function(tool: dict[str, Any]) -> dict[str, Any]:
    """Represent a Codex free-form custom tool as an upstream function tool.

    OpenCode Go accepts ``apply_patch`` as a native custom tool but rejects
    Codex's ``exec`` custom tool.  A single string parameter preserves the
    free-form payload; the response bridge converts the resulting function
    call back to a native custom_tool_call before Codex sees it.
    """

    name = tool.get("name")
    if not name:
        raise TransformError("custom tool 缺少 name")
    tool_format = tool.get("format") if isinstance(tool.get("format"), dict) else {"type": "text"}
    format_description = json.dumps(tool_format, ensure_ascii=False, separators=(",", ":"))
    description = str(tool.get("description") or "")
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": "Exact free-form custom-tool input. Original format: " + format_description,
                }
            },
            "required": ["input"],
            "additionalProperties": False,
        },
        "strict": True,
    }


def normalize_tools(tools: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """转换 Responses 工具并过滤无法安全转发的 Codex 独有类型。

    - function 类型：转换为网关接受的顶层 name 形态；
    - custom 类型：保留 name/description/format，让上游生成原生
      custom_tool_call（Codex 的 exec 工具使用该形态）；
    - 非 function 类型（namespace / web_search 等 Codex 独有工具）：
      网关 serde 只接受 function 工具，无法安全转换，过滤并返回 dropped
      类型列表供审计；不静默删除——由调用方记录。
    """

    converted: list[dict[str, Any]] = []
    dropped: list[str] = []
    for tool in tools:
        if isinstance(tool, dict) and tool.get("type") == "function":
            converted.append(normalize_tool(tool))
        elif isinstance(tool, dict) and tool.get("type") == "custom":
            if tool.get("name") == "apply_patch":
                converted.append(dict(tool))
            else:
                converted.append(custom_tool_as_function(tool))
        else:
            dropped.append(tool.get("type", "unknown") if isinstance(tool, dict) else "unknown")
    return converted, sorted(set(dropped))


def extract_additional_tools(input_items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract Codex's additional_tools envelope from the Responses input.

    Codex desktop supplies worker tools inside an input item rather than the
    top-level ``tools`` field.  The envelope is control metadata, not model
    conversation history, so it is removed after its definitions are lifted.
    """

    tools: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    for item in input_items:
        if not isinstance(item, dict):
            raise TransformError("输入 item 必须是对象")
        if item.get("type") != "additional_tools":
            remaining.append(item)
            continue
        nested = item.get("tools")
        if not isinstance(nested, list):
            raise TransformError("additional_tools 缺少 tools 数组")
        for tool in nested:
            if not isinstance(tool, dict):
                raise TransformError("additional_tools.tools 项必须是对象")
            tools.append(tool)
    return tools, remaining


def merge_tool_definitions(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge tool definitions deterministically and reject name/type conflicts."""

    merged: list[dict[str, Any]] = []
    signatures: dict[tuple[str, str], str] = {}
    for group in groups:
        for tool in group:
            if not isinstance(tool, dict):
                raise TransformError("tool 必须是对象")
            tool_type = str(tool.get("type") or "")
            function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
            name = str(tool.get("name") or function.get("name") or "")
            key = (tool_type, name)
            signature = json.dumps(tool, ensure_ascii=False, sort_keys=True, default=str)
            previous = signatures.get(key)
            if previous is not None:
                if previous != signature:
                    raise TransformError(f"重复工具定义冲突：type={tool_type!r} name={name!r}")
                continue
            signatures[key] = signature
            merged.append(tool)
    return merged


def custom_tool_names(tools: list[dict[str, Any]]) -> set[str]:
    return {
        str(tool.get("name"))
        for tool in tools
        if isinstance(tool, dict)
        and tool.get("type") == "custom"
        and tool.get("name")
        and tool.get("name") != "apply_patch"
    }


def _content_text(content: Any, *, context: str) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise TransformError(f"{context} content 必须是字符串或数组")
    segments: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            raise TransformError(f"{context} content 项必须是对象")
        part_type = part.get("type")
        if part_type in _TEXT_CONTENT_TYPES:
            text = part.get("text")
        elif part_type == "reasoning_text":
            text = part.get("text")
        elif part_type == "refusal":
            text = part.get("refusal")
        else:
            raise TransformError(f"{context} 无法安全重放 content 类型：{part_type!r}")
        if not isinstance(text, str):
            raise TransformError(f"{context} content 项 {part_type!r} 缺少文本")
        segments.append(text)
    return "\n".join(segments)


def _assistant_message_for_upstream(
    item: dict[str, Any], reasoning_content: str | None
) -> dict[str, Any]:
    """Convert a persisted Responses output message to chat-style context."""

    text = _content_text(item.get("content"), context="assistant message")
    explicit_reasoning = item.get("reasoning_content")
    if explicit_reasoning is not None and not isinstance(explicit_reasoning, str):
        raise TransformError("assistant message reasoning_content 必须是字符串或 null")
    return {
        "role": "assistant",
        "content": text,
        "reasoning_content": explicit_reasoning
        if explicit_reasoning is not None
        else reasoning_content,
    }


def convert_custom_items_for_upstream(
    items: list[dict[str, Any]], names: set[str]
) -> list[dict[str, Any]]:
    """Convert Codex custom call history to the upstream function form."""

    converted: list[dict[str, Any]] = []
    pending_reasoning_content: str | None = None
    for item in items:
        if not isinstance(item, dict):
            raise TransformError("输入 item 必须是对象")
        item_type = item.get("type")
        name = str(item.get("name") or "")
        if item_type == "reasoning":
            # Codex persists visible reasoning_text in ``content``.  The
            # OpenCode Go Responses shim accepts replayed reasoning metadata
            # but requires this array to be empty.  Normalize only at the
            # upstream boundary so the local Codex transcript stays intact.
            normalized = {
                key: item[key]
                for key in ("type", "id", "summary", "encrypted_content", "status")
                if key in item
            }
            normalized["content"] = []
            converted.append(normalized)
            content = item.get("content")
            pending_reasoning_content = (
                _content_text(content, context="reasoning item") if content else None
            )
            continue
        if item_type == "message" and item.get("role") == "assistant":
            converted.append(
                _assistant_message_for_upstream(item, pending_reasoning_content)
            )
            pending_reasoning_content = None
            continue
        if item_type == "function_call":
            call_id = item.get("call_id")
            name = item.get("name")
            arguments = item.get("arguments")
            if not call_id or not name or not isinstance(arguments, str):
                raise TransformError("function_call 缺少 call_id、name 或字符串 arguments")
            converted.append(
                {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": name,
                    "arguments": arguments,
                }
            )
            continue
        if item_type == "custom_tool_call" and name in names:
            raw_input = item.get("input")
            if not isinstance(raw_input, str):
                raise TransformError(f"custom_tool_call {name!r} 缺少字符串 input")
            converted.append({
                "id": item.get("id"),
                "type": "function_call",
                "status": item.get("status"),
                "call_id": item.get("call_id"),
                "name": name,
                "arguments": json.dumps({"input": raw_input}, ensure_ascii=False),
            })
            continue
        if item_type in _OUTPUT_TYPES:
            call_id = item.get("call_id")
            if not call_id:
                raise TransformError(f"{item_type} 缺少 call_id")
            if "output" not in item:
                raise TransformError(
                    f"{item_type} 缺少 output；不会从 Codex 私有 content 字段猜测工具结果"
                )
            # Codex-persisted output items may carry private ``content`` and
            # control metadata.  OpenCode Go's Responses schema accepts the
            # canonical call_id/output form; forwarding the private content
            # array produces ArrayParam/content validation failures on a
            # resumed full-history replay.
            converted.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": item["output"],
                }
            )
            continue
        converted.append(item)
    return converted


def extract_tool_calls(input_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract native Responses function/custom calls."""

    return [item for item in input_items if isinstance(item, dict) and item.get("type") in _CALL_TYPES]


def extract_function_call_outputs(input_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in input_items
        if isinstance(item, dict) and item.get("type") in _OUTPUT_TYPES
    ]


def assistant_message_with_calls(
    calls: list[dict[str, Any]],
    reasoning_content: Any = None,
) -> dict[str, Any]:
    """同轮多个 function_call 合并为单条 assistant 消息（chat 风格 tool_calls）。"""

    tool_calls = [
        {
            "id": call.get("call_id") or call.get("id"),
            "type": "function",
            "function": {
                "name": call.get("name"),
                "arguments": call.get("arguments") or "{}",
            },
        }
        for call in calls
    ]
    missing = [call for call, tc in zip(calls, tool_calls) if not tc["id"] or not tc["function"]["name"]]
    if missing:
        raise TransformError("function_call 缺少 call_id 或 name，无法构造上下文")
    return {
        "role": "assistant",
        "content": [],
        "tool_calls": tool_calls,
        "reasoning_content": reasoning_content,
    }


def _output_signature(item: dict[str, Any]) -> str:
    """Compare duplicate outputs in memory without exposing their contents."""
    return json.dumps(item.get("output"), ensure_ascii=False, sort_keys=True, default=str)


def replay_full_history(input_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Codex's stateless full-history replay to explicit tool context.

    Codex may omit previous_response_id and replay the earlier function_call plus
    its function_call_output.  Calls become assistant tool_calls; outputs keep
    their Responses form for the OpenCode Go adapter.  Every output must match a
    call in this same request, so no global or cross-thread guess is possible.
    """
    calls_by_id: dict[str, dict[str, Any]] = {}
    outputs_by_id: dict[str, str] = {}
    for item in input_items:
        if not isinstance(item, dict):
            continue
        if item.get("type") in _CALL_TYPES:
            call_id = item.get("call_id")
            if not call_id or not item.get("name"):
                raise TransformError("function_call 缺少 call_id 或 name")
            previous = calls_by_id.get(call_id)
            if previous and previous != item:
                raise TransformError(f"重复 function_call 的 call_id 冲突：{call_id}")
            calls_by_id[call_id] = item
        elif item.get("type") in _OUTPUT_TYPES:
            call_id = item.get("call_id")
            if not call_id:
                raise TransformError("function_call_output 缺少 call_id")
            signature = _output_signature(item)
            previous = outputs_by_id.get(call_id)
            if previous is not None and previous != signature:
                raise TransformError(f"重复 function_call_output 的 call_id 内容冲突：{call_id}")
            outputs_by_id[call_id] = signature
    unmatched_outputs = sorted(set(outputs_by_id) - set(calls_by_id))
    if unmatched_outputs:
        raise TransformError(
            "function_call_output call_id 无匹配 function_call：" + ",".join(unmatched_outputs)
        )
    incomplete_calls = sorted(set(calls_by_id) - set(outputs_by_id))
    if incomplete_calls:
        raise TransformError(
            "无 previous_response_id 的历史重放包含未完成 function_call："
            + ",".join(incomplete_calls)
        )

    expanded: list[dict[str, Any]] = []
    emitted_calls: set[str] = set()
    emitted_outputs: set[str] = set()

    for item in input_items:
        if not isinstance(item, dict):
            raise TransformError("输入 item 必须是对象")
        item_type = item.get("type")
        if item_type in _CALL_TYPES:
            call_id = item["call_id"]
            if call_id in emitted_calls:
                continue
            emitted_calls.add(call_id)
            expanded.append(item)
            continue
        if item_type in _OUTPUT_TYPES:
            call_id = item["call_id"]
            if call_id in emitted_outputs:
                continue
            emitted_outputs.add(call_id)
        expanded.append(item)
    return expanded


def known_tool_call_ids(history: list[dict[str, Any]]) -> set[str]:
    known: set[str] = set()
    for item in history:
        if not isinstance(item, dict):
            continue
        if item.get("type") in _CALL_TYPES and item.get("call_id"):
            known.add(str(item["call_id"]))
            continue
        if item.get("role") != "assistant":
            continue
        for call in item.get("tool_calls") or []:
            if isinstance(call, dict) and call.get("id"):
                known.add(str(call["id"]))
    return known


def validate_and_dedupe_outputs(
    history: list[dict[str, Any]], new_input: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    known = known_tool_call_ids(history)
    seen: dict[str, str] = {}
    result: list[dict[str, Any]] = []
    for item in new_input:
        if not isinstance(item, dict) or item.get("type") not in _OUTPUT_TYPES:
            result.append(item)
            continue
        call_id = item.get("call_id")
        if not call_id or call_id not in known:
            raise TransformError(f"function_call_output call_id 不匹配当前会话：{call_id}")
        signature = _output_signature(item)
        if call_id in seen:
            if seen[call_id] != signature:
                raise TransformError(f"重复 function_call_output 内容冲突：{call_id}")
            continue
        seen[call_id] = signature
        result.append(item)
    return result


def expand_session_input(
    history: list[dict[str, Any]],
    new_input: list[dict[str, Any]],
    notes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """展开为上游显式上下文：历史 + 新 function_call_output + 新用户消息。

    - 每个 call_id 一一对应（历史 assistant 消息必须与历史输出对应）；
    - 同一轮多个 function_call 已由历史中的单条 assistant 消息表达；
    - 不允许遗漏未完成的工具调用（history 校验在 session_store）。
    - agent_message（桌面子 Agent 任务）规范化为 user 消息；不透明/控制
      字段跳过并记录审计 notes。
    """

    notes = notes if notes is not None else []
    expanded = list(history)
    for item in new_input:
        if not isinstance(item, dict):
            raise TransformError("输入 item 必须是对象")
        item_type = item.get("type")
        if item_type == "agent_message":
            expanded.append(normalize_agent_message(item, notes))
        elif item_type in _OUTPUT_TYPES:
            if not item.get("call_id"):
                raise TransformError("function_call_output 缺少 call_id")
            expanded.append(item)
        elif item_type in (*_CALL_TYPES, "reasoning"):
            expanded.append(item)
        elif item_type in ("message",) or item_type is None:
            if "role" not in item:
                raise TransformError("message item 缺少 role")
            expanded.append(item)
        else:
            raise TransformError(f"不支持的输入 item 类型：{item_type!r}")
    return expanded


def build_upstream_request(
    codex_request: dict[str, Any],
    history: list[dict[str, Any]],
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """构造上游 /v1/responses 请求体。

    history 为上游可用的显式上下文（含 assistant 消息与 reasoning_content、
    function_call_output），new input 为本次 Codex 新增的 item。
    """

    tools = codex_request.get("tools")
    original_tools = tools if isinstance(tools, list) else []
    names = custom_tool_names(original_tools)
    expanded_input = expand_session_input(history, codex_request.get("input") or [], notes)
    upstream: dict[str, Any] = {
        "model": codex_request.get("model"),
        "input": convert_custom_items_for_upstream(expanded_input, names),
    }
    if tools is not None:
        converted_tools, dropped = normalize_tools(tools)
        if dropped:
            upstream["_dropped_tool_types"] = dropped
        upstream["tools"] = converted_tools
    if codex_request.get("stream"):
        upstream["stream"] = True
    return upstream
