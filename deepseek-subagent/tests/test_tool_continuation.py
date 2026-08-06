from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

from deepseek_subagent.bridges.opencode_go.auth import BridgeAuth  # noqa: E402
from deepseek_subagent.bridges.opencode_go.protocol_audit import AUDIT_MARKER, ProtocolAudit  # noqa: E402
from deepseek_subagent.bridges.opencode_go.server import BridgeServer  # noqa: E402
from deepseek_subagent.bridges.opencode_go.session_store import SessionStore  # noqa: E402


def _sse(events):
    return "\n\n".join("data: " + json.dumps(event) for event in events) + "\n\n"


def _function_response():
    item = {"id": "fc_item_1", "type": "function_call", "status": "completed", "call_id": "call_1", "name": "read_file", "arguments": '{"path":"probe"}'}
    return _sse([
        {"type": "response.created", "response": {"id": "up_1", "model": "deepseek-v4-flash"}},
        {"type": "response.output_item.added", "output_index": 0, "item": {**item, "status": "in_progress", "arguments": ""}},
        {"type": "response.function_call_arguments.delta", "item_id": "fc_item_1", "delta": item["arguments"]},
        {"type": "response.function_call_arguments.done", "item_id": "fc_item_1", "arguments": item["arguments"]},
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": {"id": "up_1", "status": "completed", "model": "deepseek-v4-flash"}},
    ])


def _text_response(response_id="up_2", text="DONE"):
    item = {"id": "msg_2", "type": "message", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": text}]}
    return _sse([
        {"type": "response.created", "response": {"id": response_id, "model": "deepseek-v4-flash"}},
        {"type": "response.output_item.added", "output_index": 0, "item": {**item, "status": "in_progress", "content": []}},
        {"type": "response.output_text.delta", "item_id": "msg_2", "delta": text},
        {"type": "response.output_text.done", "item_id": "msg_2", "text": text},
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": {"id": response_id, "status": "completed", "model": "deepseek-v4-flash"}},
    ])


def _custom_response():
    reasoning = {"id": "reasoning_custom", "type": "reasoning", "status": "completed", "summary": [], "content": [{"type": "reasoning_text", "text": "think"}]}
    item = {"id": "ctc_item_1", "type": "function_call", "status": "completed", "call_id": "call_exec", "name": "exec", "arguments": '{"input":"probe"}'}
    return _sse([
        {"type": "response.created", "response": {"id": "up_custom", "model": "deepseek-v4-flash"}},
        {"type": "response.output_item.added", "output_index": 0, "item": {**reasoning, "status": "in_progress", "content": []}},
        {"type": "response.reasoning_text.delta", "item_id": "reasoning_custom", "delta": "think"},
        {"type": "response.reasoning_text.done", "item_id": "reasoning_custom", "text": "think"},
        {"type": "response.output_item.done", "output_index": 0, "item": reasoning},
        {"type": "response.output_item.added", "output_index": 0, "item": {**item, "status": "in_progress", "arguments": ""}},
        {"type": "response.function_call_arguments.delta", "item_id": "ctc_item_1", "delta": item["arguments"]},
        {"type": "response.function_call_arguments.done", "item_id": "ctc_item_1", "arguments": item["arguments"]},
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": {"id": "up_custom", "status": "completed", "model": "deepseek-v4-flash"}},
    ])


class ToolContinuationHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / AUDIT_MARKER).touch()
        auth = BridgeAuth(local_token="local-test-token")
        auth._key = "upstream-test-key"
        self.server = BridgeServer(auth, SessionStore(), port=0, protocol_audit=ProtocolAudit(root))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1/responses"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def _post(self, payload):
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": "Bearer local-test-token", "Content-Type": "application/json", "Session-Id": "session-test"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8")

    @mock.patch("deepseek_subagent.bridges.opencode_go.server.upstream_call")
    def test_full_history_second_request_reaches_upstream(self, upstream):
        captured = []
        def fake(_auth, _path, payload, **_kwargs):
            captured.append(payload)
            return (200, _function_response() if len(captured) == 1 else _text_response())
        upstream.side_effect = fake
        status, first = self._post({"model": "deepseek-v4-flash", "stream": True, "store": False, "input": [{"role": "user", "content": "read"}]})
        self.assertEqual(status, 200)
        first_events = [json.loads(line[6:]) for line in first.splitlines() if line.startswith("data: ")]
        first_id = next(event["response"]["id"] for event in first_events if event["type"] == "response.created")
        call_item = next(event["item"] for event in first_events if event["type"] == "response.output_item.done" and event.get("item", {}).get("type") == "function_call")
        status, second = self._post({
            "model": "deepseek-v4-flash", "stream": True, "store": False,
            "input": [
                {"role": "user", "content": "read"},
                call_item,
                {"type": "function_call_output", "call_id": call_item["call_id"], "output": "PROBE"},
            ],
        })
        self.assertEqual(status, 200)
        self.assertEqual(len(captured), 2)
        self.assertNotEqual(first_id, "up_1")
        replayed_call = next(item for item in captured[1]["input"] if item.get("type") == "function_call")
        output = next(item for item in captured[1]["input"] if item.get("type") == "function_call_output")
        self.assertEqual(replayed_call["call_id"], "call_1")
        self.assertEqual(output["call_id"], "call_1")
        self.assertIn("DONE", second)

    @mock.patch("deepseek_subagent.bridges.opencode_go.server.upstream_call")
    def test_mismatched_call_id_rejected_before_upstream(self, upstream):
        payload = {
            "model": "deepseek-v4-flash", "stream": True,
            "input": [
                {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call_other", "output": "x"},
            ],
        }
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._post(payload)
        self.assertEqual(raised.exception.code, 400)
        self.assertFalse(upstream.called)

    @mock.patch("deepseek_subagent.bridges.opencode_go.server.upstream_call")
    def test_additional_custom_tool_lifted_and_replayed(self, upstream):
        captured = []
        def fake(_auth, _path, payload, **_kwargs):
            captured.append(payload)
            return (200, _custom_response() if len(captured) == 1 else _text_response())
        upstream.side_effect = fake
        envelope = {"type": "additional_tools", "tools": [
            {"type": "custom", "name": "exec", "description": "run", "format": {"type": "text"}},
            {"type": "function", "name": "wait", "parameters": {"type": "object"}, "strict": True},
        ]}
        status, first = self._post({"model": "deepseek-v4-flash", "stream": True, "input": [envelope, {"role": "user", "content": "read"}]})
        self.assertEqual(status, 200)
        self.assertEqual([tool["name"] for tool in captured[0]["tools"]], ["exec", "wait"])
        self.assertFalse(any(item.get("type") == "additional_tools" for item in captured[0]["input"]))
        events = [json.loads(line[6:]) for line in first.splitlines() if line.startswith("data: ")]
        reasoning_item = next(event["item"] for event in events if event["type"] == "response.output_item.done" and event.get("item", {}).get("type") == "reasoning")
        call = next(event["item"] for event in events if event["type"] == "response.output_item.done" and event.get("item", {}).get("type") == "custom_tool_call")
        status, second = self._post({
            "model": "deepseek-v4-flash", "stream": True,
            "input": [envelope, {"role": "user", "content": "read"}, reasoning_item, call, {"type": "custom_tool_call_output", "call_id": call["call_id"], "output": "ok"}],
        })
        self.assertEqual(status, 200)
        replayed_call = next(item for item in captured[1]["input"] if item.get("type") == "function_call")
        replayed_output = next(item for item in captured[1]["input"] if item.get("type") == "function_call_output")
        self.assertEqual(replayed_call["call_id"], replayed_output["call_id"])
        self.assertEqual(json.loads(replayed_call["arguments"]), {"input": "probe"})
        self.assertEqual(next(item for item in captured[1]["input"] if item.get("type") == "reasoning")["content"][0]["text"], "think")
        self.assertIn("DONE", second)

    @mock.patch("deepseek_subagent.bridges.opencode_go.server.upstream_call")
    def test_previous_response_path_preserves_reasoning_and_custom_call(self, upstream):
        captured = []
        def fake(_auth, _path, payload, **_kwargs):
            captured.append(payload)
            return (200, _custom_response() if len(captured) == 1 else _text_response())
        upstream.side_effect = fake
        envelope = {"type": "additional_tools", "tools": [{"type": "custom", "name": "exec", "format": {"type": "text"}}]}
        status, first = self._post({"model": "deepseek-v4-flash", "stream": True, "input": [envelope, {"role": "user", "content": "read"}]})
        self.assertEqual(status, 200)
        events = [json.loads(line[6:]) for line in first.splitlines() if line.startswith("data: ")]
        local_id = next(event["response"]["id"] for event in events if event["type"] == "response.created")
        status, second = self._post({
            "model": "deepseek-v4-flash", "stream": True, "previous_response_id": local_id,
            "input": [envelope, {"type": "custom_tool_call_output", "call_id": "call_exec", "output": "ok"}],
        })
        self.assertEqual(status, 200)
        self.assertEqual([item.get("type") for item in captured[1]["input"] if item.get("type")], ["reasoning", "function_call", "function_call_output"])
        self.assertIn("DONE", second)


if __name__ == "__main__":
    unittest.main()
