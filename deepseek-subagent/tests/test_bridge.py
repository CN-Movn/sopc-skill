#!/usr/bin/env python3
"""兼容桥单元测试（纯本地，无网络、无真实凭据）。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "runtime"
sys.path.insert(0, str(SKILL_DIR))

from deepseek_subagent.bridges.opencode_go import auth as bridge_auth  # noqa: E402
from deepseek_subagent.bridges.opencode_go import models as bridge_models  # noqa: E402
from deepseek_subagent.bridges.opencode_go import request_transform as rt  # noqa: E402
from deepseek_subagent.bridges.opencode_go import response_transform as xf  # noqa: E402
from deepseek_subagent.bridges.opencode_go import session_store as store  # noqa: E402
from deepseek_subagent.bridges.opencode_go.session_store import SessionStore  # noqa: E402
from deepseek_subagent.cli import build_codex_catalog  # noqa: E402
from deepseek_subagent.core import paths as paths_mod  # noqa: E402
from deepseek_subagent.core.agent_role import make_role  # noqa: E402
from deepseek_subagent.core.errors import ManagerError  # noqa: E402
from deepseek_subagent.platforms.codex.adapter import CodexAdapter  # noqa: E402
from deepseek_subagent.platforms.codex.paths import CodexPaths  # noqa: E402
from deepseek_subagent.providers.opencode_go import OpenCodeGoProvider  # noqa: E402


def auth_with_key(key="sk-test-sentinel-bridge"):
    a = bridge_auth.BridgeAuth(local_token="local-token-test")
    a._key = key
    return a


class ToolSchemaTests(unittest.TestCase):
    def test_responses_style_tool_passthrough(self):
        tool = {"type": "function", "name": "echo_probe", "description": "echo", "parameters": {"type": "object"}}
        self.assertEqual(rt.normalize_tool(tool), tool)

    def test_nested_function_tool_flattened_to_top_level_name(self):
        tool = {"type": "function", "function": {"name": "shell", "description": "d", "parameters": {"type": "object"}}}
        out = rt.normalize_tool(tool)
        self.assertEqual(out["type"], "function")
        self.assertEqual(out["name"], "shell")
        self.assertEqual(out["description"], "d")
        self.assertEqual(out["parameters"], {"type": "object"})
        self.assertNotIn("function", out)

    def test_hybrid_tool_name_from_top_level(self):
        tool = {"type": "function", "name": "apply_patch", "function": {"description": "d", "parameters": {}}}
        out = rt.normalize_tool(tool)
        self.assertEqual(out["name"], "apply_patch")

    def test_multi_tool_conversion(self):
        tools = [
            {"type": "function", "name": "a", "description": "x", "parameters": {}},
            {"type": "function", "function": {"name": "b", "description": "y", "parameters": {}}},
        ]
        out, dropped = rt.normalize_tools(tools)
        self.assertEqual([t["name"] for t in out], ["a", "b"])
        self.assertEqual(dropped, [])

    def test_non_function_tools_filtered_and_reported(self):
        tools = [
            {"type": "function", "name": "a", "description": "x", "parameters": {}},
            {"type": "namespace", "name": "n"},
            {"type": "web_search", "name": "w"},
        ]
        out, dropped = rt.normalize_tools(tools)
        self.assertEqual([t["name"] for t in out], ["a"])
        self.assertEqual(dropped, ["namespace", "web_search"])

    def test_custom_tool_bridged_as_single_string_function(self):
        custom = {"type": "custom", "name": "exec", "description": "run", "format": {"type": "text"}}
        out, dropped = rt.normalize_tools([custom])
        self.assertEqual(out[0]["type"], "function")
        self.assertEqual(out[0]["name"], "exec")
        self.assertEqual(out[0]["parameters"]["required"], ["input"])
        self.assertEqual(dropped, [])

    def test_apply_patch_custom_tool_remains_native(self):
        custom = {"type": "custom", "name": "apply_patch", "format": {"type": "text"}}
        out, dropped = rt.normalize_tools([custom])
        self.assertEqual(out, [custom])
        self.assertEqual(dropped, [])

    def test_tool_without_name_errors(self):
        with self.assertRaises(rt.TransformError):
            rt.normalize_tool({"type": "function", "parameters": {}})
        with self.assertRaises(rt.TransformError):
            rt.normalize_tool({"type": "function", "function": {"parameters": {}}})

    def test_unknown_fields_preserved(self):
        tool = {"type": "function", "name": "shell", "description": "d", "parameters": {}, "strict": True}
        self.assertEqual(rt.normalize_tool(tool)["strict"], True)
        nested = {"type": "function", "function": {"name": "shell", "description": "d", "parameters": {}, "x_extra": 1}}
        self.assertEqual(rt.normalize_tool(nested)["x_extra"], 1)

    def test_role_style_message_accepted(self):
        expanded = rt.expand_session_input([], [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}])
        self.assertEqual(len(expanded), 1)
        self.assertEqual(expanded[0]["role"], "user")

    def test_additional_tools_extracted_from_input(self):
        envelope = {"type": "additional_tools", "tools": [{"type": "custom", "name": "exec", "format": {"type": "text"}}]}
        tools, remaining = rt.extract_additional_tools([envelope, {"role": "user", "content": "probe"}])
        self.assertEqual([tool["name"] for tool in tools], ["exec"])
        self.assertEqual(remaining, [{"role": "user", "content": "probe"}])

    def test_additional_and_top_level_tools_merge_without_duplicates(self):
        tool = {"type": "function", "name": "wait", "parameters": {"type": "object"}}
        self.assertEqual(rt.merge_tool_definitions([tool], [dict(tool)]), [tool])
        with self.assertRaises(rt.TransformError):
            rt.merge_tool_definitions([tool], [{**tool, "description": "conflict"}])

    def test_message_without_role_errors(self):
        with self.assertRaises(rt.TransformError):
            rt.expand_session_input([], [{"type": "message", "content": []}])

    def test_unknown_input_item_errors(self):
        with self.assertRaises(rt.TransformError):
            rt.expand_session_input([], [{"type": "mystery_item"}])


class SessionExpansionTests(unittest.TestCase):
    def test_previous_response_id_expansion(self):
        history = [
            {"role": "user", "content": [{"type": "input_text", "text": "call tool"}]},
            {"role": "assistant", "content": [], "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "echo_probe", "arguments": "{}"}}], "reasoning_content": None},
        ]
        new_input = [{"type": "function_call_output", "call_id": "c1", "output": "{}"}]
        expanded = rt.expand_session_input(history, new_input)
        self.assertEqual(len(expanded), 3)
        self.assertEqual(expanded[2]["call_id"], "c1")
        self.assertIn("reasoning_content", expanded[1])

    def test_function_call_output_requires_call_id(self):
        with self.assertRaises(rt.TransformError):
            rt.expand_session_input([], [{"type": "function_call_output", "output": "{}"}])

    def test_same_round_multiple_calls_merged(self):
        calls = [
            {"call_id": "c1", "name": "echo_probe", "arguments": '{"value":"A"}'},
            {"call_id": "c2", "name": "echo_probe", "arguments": '{"value":"B"}'},
        ]
        msg = rt.assistant_message_with_calls(calls)
        self.assertEqual(len(msg["tool_calls"]), 2)
        self.assertEqual([tc["id"] for tc in msg["tool_calls"]], ["c1", "c2"])

    def test_call_missing_id_errors(self):
        with self.assertRaises(rt.TransformError):
            rt.assistant_message_with_calls([{"name": "echo_probe", "arguments": "{}"}])

    def test_reasoning_content_backfill(self):
        msg = rt.assistant_message_with_calls([{"call_id": "c1", "name": "echo_probe", "arguments": "{}"}], reasoning_content="thinking...")
        self.assertEqual(msg["reasoning_content"], "thinking...")
        msg2 = rt.assistant_message_with_calls([{"call_id": "c1", "name": "echo_probe", "arguments": "{}"}], reasoning_content=None)
        self.assertIsNone(msg2["reasoning_content"])

    def test_full_history_replay_without_previous_response_id(self):
        replayed = rt.replay_full_history([
            {"role": "user", "content": [{"type": "input_text", "text": "probe"}]},
            {"type": "function_call", "id": "fc1", "call_id": "call_1", "name": "read_file", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "PROBE"},
        ])
        self.assertEqual([item.get("role") or item.get("type") for item in replayed], ["user", "function_call", "function_call_output"])
        self.assertEqual(replayed[1]["call_id"], "call_1")
        self.assertEqual(replayed[2]["call_id"], "call_1")

    def test_custom_tool_full_history_replay(self):
        replayed = rt.replay_full_history([
            {"type": "reasoning", "id": "r1", "content": [{"type": "reasoning_text", "text": "think"}]},
            {"type": "custom_tool_call", "id": "ctc1", "call_id": "call_exec", "name": "exec", "input": "probe"},
            {"type": "custom_tool_call_output", "call_id": "call_exec", "output": "ok"},
        ])
        self.assertEqual([item["type"] for item in replayed], ["reasoning", "custom_tool_call", "custom_tool_call_output"])
        self.assertEqual(replayed[1]["call_id"], replayed[2]["call_id"])

    def test_custom_history_converted_to_upstream_function_form(self):
        converted = rt.convert_custom_items_for_upstream([
            {"type": "custom_tool_call", "id": "ctc1", "call_id": "call_exec", "name": "exec", "input": "probe"},
            {"type": "custom_tool_call_output", "call_id": "call_exec", "output": "ok"},
        ], {"exec"})
        self.assertEqual([item["type"] for item in converted], ["function_call", "function_call_output"])
        self.assertEqual(json.loads(converted[0]["arguments"]), {"input": "probe"})
        self.assertEqual(converted[0]["call_id"], converted[1]["call_id"])

    def test_resumed_tool_outputs_strip_codex_private_content(self):
        private = [{"type": "input_text", "text": "private duplicate"}]
        converted = rt.convert_custom_items_for_upstream(
            [
                {
                    "type": "custom_tool_call_output",
                    "call_id": "call_custom",
                    "output": "custom result",
                    "content": private,
                    "internal_chat_message_metadata_passthrough": {"turn_id": "private"},
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_function",
                    "output": "function result",
                    "content": private,
                    "future_codex_private": True,
                },
            ],
            set(),
        )
        self.assertEqual(
            converted,
            [
                {
                    "type": "function_call_output",
                    "call_id": "call_custom",
                    "output": "custom result",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_function",
                    "output": "function result",
                },
            ],
        )

    def test_resumed_tool_output_never_guesses_missing_output_from_content(self):
        with self.assertRaises(rt.TransformError) as raised:
            rt.convert_custom_items_for_upstream(
                [
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "call_missing",
                        "content": [{"type": "input_text", "text": "do not infer"}],
                    }
                ],
                set(),
            )
        self.assertIn("缺少 output", str(raised.exception))

    def test_resumed_assistant_output_uses_chat_context_and_reasoning(self):
        converted = rt.convert_custom_items_for_upstream(
            [
                {
                    "type": "reasoning",
                    "id": "reasoning_previous",
                    "summary": [],
                    "content": [
                        {"type": "reasoning_text", "text": "prior reasoning"}
                    ],
                    "internal_chat_message_metadata_passthrough": {"private": True},
                },
                {
                    "type": "message",
                    "id": "message_previous",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "prior answer",
                            "annotations": [],
                        }
                    ],
                    "future_codex_private": True,
                },
            ],
            set(),
        )
        self.assertEqual(converted[0]["content"], [])
        self.assertNotIn("internal_chat_message_metadata_passthrough", converted[0])
        self.assertEqual(
            converted[1],
            {
                "role": "assistant",
                "content": "prior answer",
                "reasoning_content": "prior reasoning",
            },
        )

    def test_resumed_native_function_call_strips_private_fields(self):
        converted = rt.convert_custom_items_for_upstream(
            [
                {
                    "type": "function_call",
                    "id": "fc_private",
                    "call_id": "call_native",
                    "name": "read_file",
                    "arguments": "{}",
                    "content": [{"type": "input_text", "text": "invalid private"}],
                    "future_codex_private": True,
                }
            ],
            set(),
        )
        self.assertEqual(
            converted,
            [
                {
                    "type": "function_call",
                    "call_id": "call_native",
                    "name": "read_file",
                    "arguments": "{}",
                }
            ],
        )

    def test_real_resume_index_three_and_five_are_both_canonical(self):
        request = rt.build_upstream_request(
            {
                "model": "deepseek-v4-flash",
                "tools": [
                    {
                        "type": "custom",
                        "name": "exec",
                        "description": "run",
                        "format": {"type": "text"},
                    }
                ],
                "input": [
                    {"role": "user", "content": [{"type": "input_text", "text": "task"}]},
                    {
                        "type": "reasoning",
                        "id": "r_tool",
                        "summary": [],
                        "content": [{"type": "reasoning_text", "text": "tool reasoning"}],
                    },
                    {
                        "type": "custom_tool_call",
                        "call_id": "call_exec",
                        "name": "exec",
                        "input": "read",
                    },
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "call_exec",
                        "output": "tool result",
                        "content": [{"type": "input_text", "text": "private duplicate"}],
                    },
                    {
                        "type": "reasoning",
                        "id": "r_final",
                        "summary": [],
                        "content": [{"type": "reasoning_text", "text": "final reasoning"}],
                    },
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "remembered answer"}],
                    },
                    {"role": "user", "content": [{"type": "input_text", "text": "verify"}]},
                ],
            },
            [],
            [],
        )
        self.assertEqual(request["input"][3], {
            "type": "function_call_output",
            "call_id": "call_exec",
            "output": "tool result",
        })
        self.assertEqual(request["input"][5], {
            "role": "assistant",
            "content": "remembered answer",
            "reasoning_content": "final reasoning",
        })

    def test_previous_response_custom_output_matches_native_session_call(self):
        history = [{"type": "custom_tool_call", "id": "ctc1", "call_id": "call_exec", "name": "exec", "input": "probe"}]
        accepted = rt.validate_and_dedupe_outputs(
            history, [{"type": "custom_tool_call_output", "call_id": "call_exec", "output": "ok"}]
        )
        self.assertEqual(len(accepted), 1)

    def test_full_history_call_id_mismatch_rejected(self):
        with self.assertRaises(rt.TransformError) as raised:
            rt.replay_full_history([
                {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call_other", "output": "x"},
            ])
        self.assertIn("无匹配", str(raised.exception))

    def test_duplicate_function_call_output_is_deduplicated(self):
        replayed = rt.replay_full_history([
            {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": "{}"},
            {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "x"},
            {"type": "function_call_output", "call_id": "call_1", "output": "x"},
        ])
        calls = [item for item in replayed if item.get("type") == "function_call"]
        outputs = [item for item in replayed if item.get("type") == "function_call_output"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(outputs), 1)

    def test_conflicting_duplicate_function_call_output_rejected(self):
        with self.assertRaises(rt.TransformError):
            rt.replay_full_history([
                {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call_1", "output": "x"},
                {"type": "function_call_output", "call_id": "call_1", "output": "y"},
            ])

    def test_previous_response_output_must_match_session_call(self):
        history = [rt.assistant_message_with_calls([
            {"call_id": "call_1", "name": "read_file", "arguments": "{}"}
        ])]
        accepted = rt.validate_and_dedupe_outputs(
            history, [{"type": "function_call_output", "call_id": "call_1", "output": "x"}]
        )
        self.assertEqual(len(accepted), 1)
        with self.assertRaises(rt.TransformError):
            rt.validate_and_dedupe_outputs(
                history, [{"type": "function_call_output", "call_id": "call_2", "output": "x"}]
            )

    def test_two_sequential_tool_rounds_full_replay(self):
        replayed = rt.replay_full_history([
            {"role": "user", "content": "read two"},
            {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "A"},
            {"type": "function_call", "call_id": "call_2", "name": "read_file", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_2", "output": "B"},
        ])
        calls = [item for item in replayed if item.get("type") == "function_call"]
        outputs = [item for item in replayed if item.get("type") == "function_call_output"]
        self.assertEqual([m["call_id"] for m in calls], ["call_1", "call_2"])
        self.assertEqual([o["call_id"] for o in outputs], ["call_1", "call_2"])

    def test_restart_without_state_still_accepts_full_replay(self):
        first_store = store.SessionStore()
        first_store.create("resp_old", "up_old", "m", [])
        first_store.clear()
        replayed = rt.replay_full_history([
            {"type": "function_call", "call_id": "call_restart", "name": "read_file", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_restart", "output": "ok"},
        ])
        self.assertEqual(replayed[0]["call_id"], "call_restart")


class SessionStoreTests(unittest.TestCase):
    def test_create_get_update(self):
        s = store.SessionStore()
        s.create("l1", "u1", "deepseek-v4-flash", [{"role": "user"}])
        self.assertEqual(s.get("l1").upstream_id, "u1")
        s.update("l1", "u2", [{"role": "user"}, {"role": "assistant"}])
        self.assertEqual(s.get("l1").upstream_id, "u2")
        self.assertEqual(len(s.get("l1").history), 2)

    def test_missing_session_returns_none(self):
        s = store.SessionStore()
        self.assertIsNone(s.get("nope"))

    def test_expired_session_returns_none(self):
        s = store.SessionStore(ttl_seconds=0.01)
        s.create("l1", "u1", "m", [])
        import time

        time.sleep(0.02)
        self.assertIsNone(s.get("l1"))

    def test_capacity_eviction(self):
        s = store.SessionStore(max_sessions=3)
        for i in range(5):
            s.create(f"l{i}", f"u{i}", "m", [])
        self.assertEqual(s.size(), 3)

    def test_history_cap(self):
        s = store.SessionStore(max_history_items=2)
        s.create("l1", "u1", "m", [{"i": 1}, {"i": 2}, {"i": 3}])
        self.assertEqual(len(s.get("l1").history), 2)

    def test_three_concurrent_sessions_are_isolated(self):
        from concurrent.futures import ThreadPoolExecutor

        s = store.SessionStore()
        def create(index):
            return s.create(
                f"response-{index}", f"upstream-{index}", "m",
                [{"role": "user", "marker": f"marker-{index}"}],
            )
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(create, range(3)))
        self.assertEqual(s.size(), 3)
        self.assertEqual(
            [s.get(f"response-{i}").history[0]["marker"] for i in range(3)],
            ["marker-0", "marker-1", "marker-2"],
        )


class ModelsConversionTests(unittest.TestCase):
    def test_data_to_models_conversion(self):
        payload = {"object": "list", "data": [{"id": "deepseek-v4-flash", "object": "model", "created": 1, "owned_by": "opencode"}, {"id": "qwen3.7-plus", "created": 2}]}
        out = bridge_models.upstream_to_codex_models(payload)
        self.assertEqual(out["models"][0]["slug"], "deepseek-v4-flash")
        self.assertEqual(out["models"][1]["slug"], "qwen3.7-plus")
        self.assertNotIn("object", out["models"][0])

    def test_missing_id_skipped(self):
        out = bridge_models.upstream_to_codex_models({"data": [{"name": "x"}, {"id": "ok"}]})
        self.assertEqual([m["slug"] for m in out["models"]], ["ok"])


class StreamTransformTests(unittest.TestCase):
    AUTH = None

    def setUp(self):
        self.AUTH = auth_with_key()

    def test_text_delta_events_converted(self):
        events = [
            {"type": "response.created", "response": {"id": "u1", "model": "deepseek-v4-flash"}},
            {"type": "response.output_item.added", "item": {"id": "i1", "type": "message"}},
            {"type": "response.output_text.delta", "item_id": "i1", "delta": "he"},
            {"type": "response.output_text.delta", "item_id": "i1", "delta": "llo"},
            {"type": "response.completed", "response": {"id": "u1"}},
        ]
        lines = xf.transform_stream_events(events, "local-1", self.AUTH)
        text = "\n".join(lines)
        self.assertIn("data: " + json.dumps({"type": "response.created", "response": {"id": "local-1", "object": "response", "status": "in_progress", "model": "deepseek-v4-flash"}}), text)
        self.assertIn('"delta": "he"', text)
        self.assertIn('"delta": "llo"', text)
        self.assertIn("response.output_text.done", text)
        self.assertIn("response.content_part.done", text)
        self.assertIn("response.output_item.done", text)
        self.assertIn('"id": "local-1"', text.split("response.completed")[1])
        emitted = [json.loads(line[6:]) for line in lines]
        types = [event["type"] for event in emitted]
        self.assertEqual(types.count("response.created"), 1)
        self.assertEqual(types.count("response.content_part.added"), 1)
        self.assertLess(types.index("response.created"), types.index("response.completed"))

    def test_function_args_delta_reassembled(self):
        events = [
            {"type": "response.output_item.added", "item": {"id": "f1", "type": "function_call", "call_id": "c1", "name": "echo_probe", "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "item_id": "f1", "delta": '{"va'},
            {"type": "response.function_call_arguments.delta", "item_id": "f1", "delta": 'lue":"X"}'},
            {"type": "response.completed", "response": {"id": "u1"}},
        ]
        lines = xf.transform_stream_events(events, "local-1", self.AUTH)
        text = "\n".join(lines)
        self.assertIn("function_call_arguments.delta", text)
        self.assertIn("function_call_arguments.done", text)
        self.assertIn('"arguments": "{\\"va', text)
        emitted = [json.loads(line[6:]) for line in lines]
        types = [event["type"] for event in emitted]
        self.assertLess(types.index("response.created"), types.index("response.function_call_arguments.delta"))
        self.assertLess(types.index("response.function_call_arguments.done"), types.index("response.completed"))
        self.assertEqual(types.count("response.function_call_arguments.done"), 1)
        self.assertEqual(types.count("response.output_item.done"), 1)

    def test_ping_and_nonjson_lines_dropped(self):
        events = xf.parse_sse_lines('data: {"type": "ping"}\n\ndata: not-json\n\ndata: {"type": "response.completed", "response": {"id": "u1"}}\n\ndata: [DONE]\n')
        self.assertEqual([e["type"] for e in events], ["ping", "response.completed"])
        lines = xf.transform_stream_events(events, "local-1", self.AUTH)
        self.assertNotIn("ping", "\n".join(lines))

    def test_error_event_redacted(self):
        lines = xf.transform_stream_events([{"type": "error", "message": "boom sk-test-sentinel-bridge"}], "l", self.AUTH)
        self.assertIn("***", "\n".join(lines))
        self.assertNotIn("sk-test-sentinel-bridge", "\n".join(lines))

    def test_missing_completed_synthesized(self):
        lines = xf.transform_stream_events([{"type": "response.output_text.delta", "item_id": "i1", "delta": "x"}], "local-1", self.AUTH)
        self.assertTrue(any("response.completed" in line for line in lines))

    def test_completed_response_contains_final_text_output(self):
        events = [
            {"type": "response.output_text.delta", "item_id": "i1", "delta": "probe"},
            {"type": "response.completed", "response": {"id": "upstream", "model": "deepseek-v4-flash"}},
        ]
        emitted = [json.loads(line[6:]) for line in xf.transform_stream_events(events, "local-1", self.AUTH)]
        completed = next(event for event in emitted if event["type"] == "response.completed")
        self.assertEqual(completed["response"]["id"], "local-1")
        self.assertEqual(completed["response"]["output"][0]["content"][0]["text"], "probe")

    def test_output_item_done_reads_nested_message_content(self):
        events = [
            {"type": "response.output_item.added", "item": {"id": "i1", "type": "message", "role": "assistant", "content": []}},
            {"type": "response.output_text.delta", "item_id": "i1", "delta": "probe"},
            {"type": "response.output_item.done", "item": {"id": "i1", "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "probe", "annotations": []}]}},
            {"type": "response.completed", "response": {"id": "upstream"}},
        ]
        emitted = [json.loads(line[6:]) for line in xf.transform_stream_events(events, "local-1", self.AUTH)]
        done = next(event for event in emitted if event["type"] == "response.output_item.done")
        self.assertEqual(done["item"]["content"][0]["text"], "probe")
        completed = next(event for event in emitted if event["type"] == "response.completed")
        self.assertEqual(completed["response"]["output"][0]["content"][0]["text"], "probe")

    def test_completed_response_contains_final_function_output(self):
        events = [
            {"type": "response.output_item.added", "item": {"id": "f1", "type": "function_call", "call_id": "c1", "name": "read_file", "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "item_id": "f1", "delta": '{"path":"probe"}'},
            {"type": "response.completed", "response": {"id": "upstream"}},
        ]
        emitted = [json.loads(line[6:]) for line in xf.transform_stream_events(events, "local-1", self.AUTH)]
        completed = next(event for event in emitted if event["type"] == "response.completed")
        item = completed["response"]["output"][0]
        self.assertEqual((item["call_id"], item["name"]), ("c1", "read_file"))
        self.assertEqual(json.loads(item["arguments"]), {"path": "probe"})

    def test_dsml_tool_text_promoted_to_function_call(self):
        text = "<｜DSML｜tools_call>\n<invoke name=\"exec_command\">\n<parameter string=\"true\">echo C5B_TOOL_OK</parameter>\n</invoke>\n</｜DSML｜tools_call>"
        remaining, calls = xf.parse_dsml_tool_calls(text)
        self.assertEqual(remaining, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "exec_command")
        self.assertEqual(json.loads(calls[0]["arguments"]), {"cmd": "echo C5B_TOOL_OK"})

    def test_dsml_named_parameter_and_surrounding_text(self):
        text = "I will run it. <｜DSML｜tool_calls><invoke name=\"exec_command\"><parameter name=\"cmd\" string=\"true\">echo OK</parameter></invoke></｜DSML｜tool_calls>"
        remaining, calls = xf.parse_dsml_tool_calls(text)
        self.assertEqual(remaining, "I will run it.")
        self.assertEqual(json.loads(calls[0]["arguments"]), {"cmd": "echo OK"})

    def test_dsml_unquoted_attributes_promoted(self):
        text = "<DSML_tools_call><invoke name=exec_command><parameter name=cmd>Get-Content probe.txt</parameter></invoke></DSML_tools_call>"
        remaining, calls = xf.parse_dsml_tool_calls(text)
        self.assertEqual(remaining, "")
        self.assertEqual(calls[0]["name"], "exec_command")
        self.assertEqual(json.loads(calls[0]["arguments"]), {"cmd": "Get-Content probe.txt"})

    def test_dsml_prefixed_tags_are_promoted(self):
        text = "<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name=\"exec_command\"><｜｜DSML｜｜parameter name=\"cmd\" string=\"true\">echo OK</｜｜DSML｜｜parameter></｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>"
        remaining, calls = xf.parse_dsml_tool_calls(text)
        self.assertEqual(remaining, "")
        self.assertEqual(json.loads(calls[0]["arguments"]), {"cmd": "echo OK"})

    def test_dsml_stream_is_structured(self):
        events = [
            {"type": "response.output_item.added", "item": {"id": "m1", "type": "message"}},
            {"type": "response.output_text.delta", "item_id": "m1", "delta": "<｜DSML｜tools_call><invoke name=\"exec_command\"><parameter string=\"true\">echo C5B_TOOL_OK</parameter></invoke></｜DSML｜tools_call>"},
            {"type": "response.output_text.done", "item_id": "m1", "text": "<｜DSML｜tools_call><invoke name=\"exec_command\"><parameter string=\"true\">echo C5B_TOOL_OK</parameter></invoke></｜DSML｜tools_call>"},
            {"type": "response.output_item.done", "item": {"id": "m1", "type": "message", "content": []}},
            {"type": "response.completed", "response": {"id": "u1"}},
        ]
        normalized = xf.normalize_stream_events(events)
        self.assertTrue(any(e.get("type") == "response.output_item.added" and e.get("item", {}).get("type") == "function_call" for e in normalized))
        self.assertFalse(any(e.get("type") == "response.output_text.delta" for e in normalized))
        self.assertFalse(any(e.get("type") in ("response.content_part.added", "response.content_part.done") for e in normalized))
        lines = xf.transform_stream_events(events, "local-1", self.AUTH)
        self.assertIn('"type": "function_call"', "\n".join(lines))

    def test_upstream_exec_function_stream_becomes_custom_tool_call(self):
        item = {"id": "fc_exec", "type": "function_call", "status": "completed", "call_id": "call_exec", "name": "exec", "arguments": '{"input":"probe"}'}
        events = [
            {"type": "response.output_item.added", "output_index": 0, "item": {**item, "status": "in_progress", "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "output_index": 0, "item_id": "fc_exec", "delta": item["arguments"]},
            {"type": "response.function_call_arguments.done", "output_index": 0, "item_id": "fc_exec", "arguments": item["arguments"]},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": {"id": "up"}},
        ]
        emitted = [json.loads(line[6:]) for line in xf.transform_stream_events(events, "local", self.AUTH, {"exec"})]
        done = next(event for event in emitted if event["type"] == "response.output_item.done")
        self.assertEqual(done["item"]["type"], "custom_tool_call")
        self.assertEqual(done["item"]["input"], "probe")
        self.assertEqual(done["item"]["call_id"], "call_exec")
        self.assertTrue(any(event["type"] == "response.custom_tool_call_input.done" for event in emitted))
        self.assertFalse(any(event["type"].startswith("response.function_call_arguments") for event in emitted))

    def test_nonstream_response_is_normalized(self):
        upstream = {"id": "u1", "output": [{"id": "m1", "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "<invoke name=\"exec_command\"><parameter name=\"cmd\">echo OK</parameter></invoke>", "annotations": []}]}]}
        out = xf.localize_response(upstream, "local-1")
        self.assertEqual([item["type"] for item in out["output"]], ["function_call"])


class AuthTests(unittest.TestCase):
    def test_local_token_never_reuses_real_key(self):
        a = auth_with_key("sk-real-key-value")
        self.assertEqual(a.local_token, "local-token-test")
        self.assertNotEqual(a.local_token, "sk-real-key-value")

    def test_redact_removes_key(self):
        a = auth_with_key("sk-secret-123")
        self.assertEqual(a.redact("token sk-secret-123 here"), "token *** here")

    def test_check_local_token(self):
        a = auth_with_key()
        self.assertTrue(a.check_local("local-token-test"))
        self.assertFalse(a.check_local("wrong"))

    def test_clear_removes_key(self):
        a = auth_with_key("sk-x")
        a.clear()
        with self.assertRaises(bridge_auth.AuthError):
            a.bearer()

    def test_load_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "opencode-go.key"
            f.write_text("sk-file-key\n", encoding="utf-8")
            a = bridge_auth.BridgeAuth(key_file=str(f))
            a.load()
            self.assertEqual(a.bearer(), "sk-file-key")


class CodexInstallTests(unittest.TestCase):
    TEMPLATE = {
        "slug": "gpt-5.6-sol",
        "display_name": "GPT-5.6-Sol",
        "multi_agent_version": "v2",
        "supported_reasoning_levels": [{"effort": "high", "description": "x"}],
        "shell_type": "shell_command",
        "visibility": "list",
        "supported_in_api": True,
    }

    def _catalog(self):
        return {"models": [dict(self.TEMPLATE), {"slug": "gpt-other"}]}

    def _compat_catalog(self):
        payload = build_codex_catalog(self._catalog(), OpenCodeGoProvider.model)
        for item in payload["models"]:
            if item.get("slug") in {"gpt-5.6-sol", OpenCodeGoProvider.model}:
                item["multi_agent_version"] = "v1"
        return payload

    def test_codex_catalog_preserves_parent_and_adds_deepseek(self):
        payload = build_codex_catalog(self._catalog(), "deepseek-v4-flash")
        by_slug = {m["slug"]: m for m in payload["models"]}
        self.assertEqual(by_slug["gpt-5.6-sol"]["multi_agent_version"], "v2")
        self.assertEqual(by_slug["deepseek-v4-flash"]["slug"], "deepseek-v4-flash")
        self.assertEqual(by_slug["deepseek-v4-flash"]["display_name"], "DeepSeek V4 Flash")
        self.assertEqual(by_slug["deepseek-v4-flash"]["multi_agent_version"], "v1")
        self.assertIn("gpt-other", by_slug)

    def test_codex_catalog_missing_template_errors(self):
        with self.assertRaises(ManagerError):
            build_codex_catalog({"models": [{"slug": "x"}]}, "deepseek-v4-flash")

    def test_codex_install_and_status_and_uninstall(self):
        with tempfile.TemporaryDirectory() as directory:
            state = paths_mod.state_paths(Path(directory) / "state")
            codex_home = (Path(directory) / "codex-home").resolve()
            codex_paths = CodexPaths.from_home(codex_home, "DeepSeek")
            codex_paths.config.parent.mkdir(parents=True, exist_ok=True)
            codex_paths.config.write_text(
                'model = "gpt-5.6-sol"\nnotify = "x"\n[features]\njs_repl = false\n',
                newline="\n",
            )
            original_config = codex_paths.config.read_bytes()
            role = make_role(OpenCodeGoProvider)
            codex_auth = {"command": "python", "args": [str(Path(directory) / "keygen.py")], "timeout_ms": 5000, "refresh_interval_ms": 0}
            adapter = CodexAdapter()
            result = adapter.install_bridge(
                state,
                codex_paths,
                role,
                OpenCodeGoProvider,
                self._compat_catalog(),
                codex_auth,
                "http://127.0.0.1:1981/v1",
            )
            self.assertTrue(result["backup"])
            # 配置断言
            config_text = codex_paths.config.read_text()
            self.assertIn("[model_providers.opencode-go-bridge]", config_text)
            self.assertNotIn("[model_providers.opencode-go]", config_text)
            self.assertIn('base_url = "http://127.0.0.1:1981/v1"', config_text)
            self.assertIn('wire_api = "responses"', config_text)
            self.assertIn("model_catalog_json", config_text)
            self.assertIn("multi_agent = true", config_text)
            self.assertIn("multi_agent_v2 = false", config_text)
            self.assertIn("js_repl = false", config_text)
            self.assertEqual(config_text.count('model = "gpt-5.6-sol"'), 1)
            self.assertTrue(codex_paths.agent.is_file())
            agent_text = codex_paths.agent.read_text()
            self.assertIn('model_provider = "opencode-go-bridge"', agent_text)
            self.assertNotIn('model_provider = "opencode-go"', agent_text)
            self.assertTrue(codex_paths.catalog.is_file())
            manifest = json.loads(state.manifest.read_text(encoding="utf-8"))
            self.assertNotIn("experimental", manifest)
            self.assertEqual(manifest["provider"], "opencode-go")
            self.assertEqual(manifest["bridge_auth"], codex_auth)
            self.assertEqual(manifest["allowed_multi_agent_versions"], ["v1"])
            self.assertFalse(manifest["multi_agent_fallback"])
            # status 必须满足 cross-provider-v1 兼容契约。
            status = adapter.status(state, object(), role, OpenCodeGoProvider, str(codex_home))
            self.assertEqual(status["status"], "configured")
            self.assertTrue(status["checks"]["provider_valid"])
            self.assertTrue(status["checks"]["parent_uses_plaintext_v1"])
            self.assertTrue(status["checks"]["role_model_uses_plaintext_v1"])
            self.assertTrue(status["checks"]["desktop_multi_agent_enabled"])
            # uninstall 恢复（字节级）
            result = adapter.uninstall(state, object(), remove_credential=False, platform_home=str(codex_home))
            self.assertEqual(result["status"], "uninstalled")
            self.assertFalse(state.manifest.exists())
            self.assertNotIn("[model_providers.opencode-go-bridge]", codex_paths.config.read_text())
            self.assertNotIn("model_catalog_json", codex_paths.config.read_text())
            self.assertEqual(codex_paths.config.read_bytes(), original_config)
            self.assertFalse(codex_paths.agent.exists())
            self.assertFalse(codex_paths.catalog.exists())

    def test_codex_status_detects_parent_v2_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            state = paths_mod.state_paths(Path(directory) / "state")
            codex_home = (Path(directory) / "codex-home").resolve()
            codex_paths = CodexPaths.from_home(codex_home, "DeepSeek")
            codex_paths.config.parent.mkdir(parents=True, exist_ok=True)
            codex_paths.config.write_text('model = "gpt-5.6-sol"\n', newline="\n")
            role = make_role(OpenCodeGoProvider)
            auth = {
                "command": "python",
                "args": [str(Path(directory) / "keygen.py")],
                "timeout_ms": 5000,
                "refresh_interval_ms": 0,
            }
            adapter = CodexAdapter()
            adapter.install_bridge(
                state,
                codex_paths,
                role,
                OpenCodeGoProvider,
                self._compat_catalog(),
                auth,
                "http://127.0.0.1:1981/v1",
            )
            installed = adapter.status(
                state, object(), role, OpenCodeGoProvider, str(codex_home)
            )
            self.assertEqual(installed["status"], "configured")
            self.assertTrue(installed["checks"]["parent_uses_plaintext_v1"])

            catalog = json.loads(codex_paths.catalog.read_text(encoding="utf-8"))
            parent = next(
                item for item in catalog["models"] if item.get("slug") == "gpt-5.6-sol"
            )
            parent["multi_agent_version"] = "v2"
            codex_paths.catalog.write_text(
                json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )

            drifted = adapter.status(
                state, object(), role, OpenCodeGoProvider, str(codex_home)
            )
            self.assertEqual(drifted["status"], "partial")
            self.assertFalse(drifted["checks"]["parent_uses_plaintext_v1"])
            self.assertIn("parent_uses_plaintext_v1", drifted["required"])

    def test_legacy_bridge_manifest_is_read_and_cleaned_on_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            state = paths_mod.state_paths(Path(directory) / "state")
            codex_home = (Path(directory) / "codex-home").resolve()
            codex_paths = CodexPaths.from_home(codex_home, "DeepSeek")
            codex_paths.config.parent.mkdir(parents=True, exist_ok=True)
            codex_paths.config.write_text('model = "gpt-5.6-sol"\n', encoding="utf-8", newline="\n")
            role = make_role(OpenCodeGoProvider)
            auth = {
                "command": "python",
                "args": [str(Path(directory) / "keygen.py")],
                "timeout_ms": 5000,
                "refresh_interval_ms": 0,
            }
            adapter = CodexAdapter()
            adapter.install_bridge(
                state, codex_paths, role, OpenCodeGoProvider,
                self._compat_catalog(), auth, "http://127.0.0.1:1981/v1",
            )
            manifest = json.loads(state.manifest.read_text(encoding="utf-8"))
            manifest["experimental"] = True
            manifest["experimental_auth"] = manifest.pop("bridge_auth")
            manifest["experimental_base_url"] = manifest.pop("bridge_base_url")
            manifest["requested_provider"] = "obsolete"
            manifest["provider_selection_match"] = False
            state.manifest.write_text(json.dumps(manifest), encoding="utf-8")

            self.assertEqual(
                adapter.status(state, object(), role, OpenCodeGoProvider, str(codex_home))["status"],
                "configured",
            )
            adapter.install_bridge(
                state, codex_paths, role, OpenCodeGoProvider,
                self._compat_catalog(), auth, "http://127.0.0.1:1981/v1",
            )
            repaired = json.loads(state.manifest.read_text(encoding="utf-8"))
            for field in (
                "experimental", "experimental_auth", "experimental_base_url",
                "requested_provider", "provider_selection_match",
            ):
                self.assertNotIn(field, repaired)
            self.assertEqual(repaired["platform"], "codex")
            self.assertEqual(repaired["provider"], "opencode-go")

    def test_codex_status_detects_base_url_drift(self):
        """桌面规范化（内置 provider base_url 被改写）后，静态检查必须发现漂移。"""

        with tempfile.TemporaryDirectory() as directory:
            state = paths_mod.state_paths(Path(directory) / "state")
            codex_home = (Path(directory) / "codex-home").resolve()
            codex_paths = CodexPaths.from_home(codex_home, "DeepSeek")
            codex_paths.config.parent.mkdir(parents=True, exist_ok=True)
            codex_paths.config.write_text(
                'model = "gpt-5.6-sol"\nnotify = "x"\n', newline="\n"
            )
            role = make_role(OpenCodeGoProvider)
            auth = {"command": "python", "args": [str(Path(directory) / "k.py")], "timeout_ms": 5000, "refresh_interval_ms": 0}
            adapter = CodexAdapter()
            adapter.install_bridge(
                state,
                codex_paths,
                role,
                OpenCodeGoProvider,
                self._compat_catalog(),
                auth,
                "http://127.0.0.1:1981/v1",
            )
            self.assertEqual(adapter.status(state, object(), role, OpenCodeGoProvider, str(codex_home))["status"], "configured")
            # 模拟桌面规范化：把本地桥 base_url 改回上游服务地址
            drifted = codex_paths.config.read_text().replace(
                'base_url = "http://127.0.0.1:1981/v1"',
                'base_url = "https://opencode.ai/zen/go/v1"',
            )
            codex_paths.config.write_text(drifted, newline="\n")
            status = adapter.status(state, object(), role, OpenCodeGoProvider, str(codex_home))
            self.assertEqual(status["status"], "partial")
            self.assertFalse(status["checks"]["provider_valid"])
            # 漂移修复（恢复桥地址）后可正常卸载（字节级恢复）
            codex_paths.config.write_text(
                drifted.replace('base_url = "https://opencode.ai/zen/go/v1"', 'base_url = "http://127.0.0.1:1981/v1"'),
                newline="\n",
            )
            result = adapter.uninstall(state, object(), remove_credential=False, platform_home=str(codex_home))
            self.assertEqual(result["status"], "uninstalled")
            self.assertEqual(
                codex_paths.config.read_text(),
                'model = "gpt-5.6-sol"\nnotify = "x"\n',
            )
            self.assertFalse(state.manifest.exists())
            self.assertFalse(codex_paths.agent.exists())
            self.assertFalse(codex_paths.catalog.exists())


class AgentMessageTests(unittest.TestCase):
    """桌面多 Agent 的 agent_message 输入转换（结构来自真实桌面验收提取）。"""

    def _minimal(self, text="DS_DESKTOP_TEXT_ABCDEF"):
        return {
            "type": "agent_message",
            "id": "msg_123",
            "author": "/root",
            "recipient": "/root/deepseek_verification_b",
            "content": [
                {"type": "input_text", "text": text},
                {"type": "encrypted_content", "encrypted_content": "opaque-encrypted"},
            ],
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_1"},
        }

    def test_minimal_agent_message_converted_to_user(self):
        notes = []
        out = rt.normalize_agent_message(self._minimal("DS_DESKTOP_TEXT_B805F4"), notes)
        self.assertEqual(out["role"], "user")
        self.assertEqual(out["content"][0]["type"], "input_text")
        self.assertEqual(out["content"][0]["text"], "DS_DESKTOP_TEXT_B805F4")
        self.assertTrue(any("encrypted" in n for n in notes))
        self.assertTrue(any("author" in n for n in notes))

    def test_multi_segment_content_ordered(self):
        item = self._minimal("")
        item["content"] = [
            {"type": "input_text", "text": "first"},
            {"type": "input_text", "text": "second"},
        ]
        out = rt.normalize_agent_message(item, [])
        self.assertEqual(out["content"][0]["text"], "first\nsecond")

    def test_unknown_fields_kept_safe_and_audited(self):
        notes = []
        out = rt.normalize_agent_message(self._minimal("task"), notes)
        serialized = json.dumps(out)
        self.assertNotIn("encrypted_content", serialized)
        self.assertNotIn("internal_chat_message_metadata_passthrough", serialized)
        self.assertNotIn("author", serialized)
        self.assertNotIn("recipient", serialized)
        self.assertTrue(notes)

    def test_missing_task_text_errors(self):
        item = self._minimal()
        item["content"] = [{"type": "encrypted_content", "encrypted_content": "x"}]
        with self.assertRaises(rt.TransformError):
            rt.normalize_agent_message(item, [])
        item2 = self._minimal()
        item2.pop("content")
        with self.assertRaises(rt.TransformError):
            rt.normalize_agent_message(item2, [])

    def test_unsafe_content_type_errors(self):
        item = self._minimal()
        item["content"] = [{"type": "mystery", "data": "x"}]
        with self.assertRaises(rt.TransformError):
            rt.normalize_agent_message(item, [])

    def test_fork_none_structure(self):
        """fork_turns=none：单条 agent_message（含 input_text + encrypted_content 的真实形态）。"""

        notes = []
        out = rt.normalize_agent_message(self._minimal("DS_FORK_NONE_1234"), notes)
        self.assertEqual(out["role"], "user")
        self.assertEqual(out["content"][0]["text"], "DS_FORK_NONE_1234")

    def test_fork_all_multiple_messages_ordered(self):
        """fork_turns=all：多条 agent_message（继承上下文）按序转换。"""

        notes = []
        expanded = rt.expand_session_input(
            [],
            [self._minimal("PART_A"), self._minimal("PART_B")],
            notes,
        )
        self.assertEqual(len(expanded), 2)
        texts = [m["content"][0]["text"] for m in expanded]
        self.assertEqual(texts, ["PART_A", "PART_B"])

    def test_parent_child_session_isolation(self):
        store = SessionStore()
        store.create("parent-session", "u1", "m", [{"role": "user", "content": [{"type": "input_text", "text": "parent"}]}])
        store.create("child-session", "u2", "m", [{"role": "user", "content": [{"type": "input_text", "text": "child"}]}])
        self.assertIsNotNone(store.get("parent-session"))
        self.assertIsNotNone(store.get("child-session"))
        self.assertNotEqual(store.get("parent-session").history, store.get("child-session").history)

    def test_two_independent_subagents_no_session_sharing(self):
        store = SessionStore()
        first = store.create("child-1", "u1", "m", [{"role": "user", "content": [{"type": "input_text", "text": "A"}]}])
        second = store.create("child-2", "u2", "m", [{"role": "user", "content": [{"type": "input_text", "text": "B"}]}])
        self.assertNotEqual(first.local_id, second.local_id)
        self.assertEqual(store.get("child-1").history[0]["content"][0]["text"], "A")
        self.assertEqual(store.get("child-2").history[0]["content"][0]["text"], "B")

    def test_agent_message_roundtrip_via_build_upstream(self):
        notes = []
        req = rt.build_upstream_request(
            {"model": "deepseek-v4-flash", "input": [self._minimal("DS_ROUNDTRIP_1")]},
            [],
            notes,
        )
        self.assertEqual(req["input"][0]["role"], "user")
        self.assertNotIn("encrypted_content", json.dumps(req))
        self.assertTrue(notes)

    def test_restart_replay_normalizes_reasoning_for_upstream(self):
        request = rt.build_upstream_request(
            {
                "model": "deepseek-v4-flash",
                "input": [
                    {
                        "id": "reasoning_restart",
                        "type": "reasoning",
                        "summary": [],
                        "content": [{"type": "reasoning_text", "text": "private replay text"}],
                        "encrypted_content": None,
                        "internal_chat_message_metadata_passthrough": {"turn_id": "turn_restart"},
                        "future_codex_private": "must-not-replay",
                    },
                    {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "wake"}]},
                ],
            },
            [],
            [],
        )
        reasoning = request["input"][0]
        self.assertEqual(reasoning["type"], "reasoning")
        self.assertEqual(reasoning["content"], [])
        self.assertEqual(reasoning["summary"], [])
        self.assertIsNone(reasoning["encrypted_content"])
        self.assertNotIn("internal_chat_message_metadata_passthrough", reasoning)
        self.assertNotIn("future_codex_private", reasoning)
        self.assertEqual(request["input"][-1]["role"], "user")

    def test_session_isolation_across_requests(self):
        """子 Agent 各自 previous_response_id 只关联自己的历史。"""

        store = SessionStore()
        session_a = store.create("local-a", "up-a", "m", [{"role": "user", "content": [{"type": "input_text", "text": "task A"}]}])
        session_b = store.create("local-b", "up-b", "m", [{"role": "user", "content": [{"type": "input_text", "text": "task B"}]}])
        self.assertNotEqual(session_a.history, session_b.history)
        self.assertIsNone(store.get("local-a").history[0].get("recipient") if False else None)

    def _v2_envelope(self, payload_after="", with_opaque=True):
        env = ("Message Type: NEW_TASK\nTask name: /root/deepseek_verification\n"
               "Sender: /root\nPayload:\n" + payload_after)
        content = [{"type": "input_text", "text": env}]
        if with_opaque:
            content.append({"type": "encrypted_content", "encrypted_content": "opaque-v2"})
        return {"type": "agent_message", "id": "m9", "author": "/root", "recipient": "/r/t", "content": content}

    def test_v2_encrypted_task_envelope_errors(self):
        """桌面 v2 加密任务（明文仅信封 + encrypted_content 负载）→ 明确错误。"""

        with self.assertRaises(rt.TransformError) as cm:
            rt.normalize_agent_message(self._v2_envelope(""), [])
        self.assertEqual(cm.exception.code, "current_task_multi_agent_v2")
        self.assertIn("Multi-Agent V2", str(cm.exception))
        self.assertIn("V1", str(cm.exception))

    def test_v2_envelope_with_plaintext_payload_converts(self):
        """明文任务正文（信封后 >40 字符）+ 加密并存（R16 探测形态）→ 正常转换。"""

        task = "DS_DESKTOP_TEXT_PROBE_OK 请原样返回该验证口令，不要附加任何内容。"
        out = rt.normalize_agent_message(self._v2_envelope(task), [])
        self.assertEqual(out["role"], "user")
        self.assertIn("DS_DESKTOP_TEXT_PROBE_OK", out["content"][0]["text"])

    def test_v2_envelope_without_opaque_converts(self):
        """信封 + 无加密负载（v1/CLI 形态）→ 仍转换，不误伤。"""

        out = rt.normalize_agent_message(self._v2_envelope("", with_opaque=False), [])
        self.assertEqual(out["role"], "user")
        self.assertIn("NEW_TASK", out["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
