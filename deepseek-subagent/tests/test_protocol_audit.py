from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

from deepseek_subagent.bridges.opencode_go.protocol_audit import (  # noqa: E402
    AUDIT_FILE,
    AUDIT_MARKER,
    ProtocolAudit,
)


class ProtocolAuditTests(unittest.TestCase):
    def test_disabled_audit_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = ProtocolAudit(directory)
            audit.request({"input": []}, {})
            self.assertFalse((Path(directory) / AUDIT_FILE).exists())

    def test_structure_only_audit_redacts_sensitive_bodies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / AUDIT_MARKER).touch()
            audit = ProtocolAudit(root)
            secret_prompt = "SOURCE_BODY_MUST_NOT_APPEAR"
            secret_output = "TOOL_OUTPUT_MUST_NOT_APPEAR"
            secret_auth = "Bearer LOCAL_TOKEN_MUST_NOT_APPEAR"
            seq = audit.request(
                {
                    "stream": True,
                    "previous_response_id": "resp_private",
                    "input": [
                        {"type": "additional_tools", "tools": [{"type": "function", "name": "shell_command", "parameters": {"type": "object"}}]},
                        {"type": "message", "id": "m1", "role": "user", "content": secret_prompt},
                        {"type": "function_call_output", "call_id": "call_1", "output": secret_output},
                    ],
                    "tools": [{"type": "function", "name": "read_file"}],
                },
                {"Authorization": secret_auth, "X-Session-Id": "session_private"},
            )
            secret_reasoning = "PRIVATE_REASONING_MUST_NOT_APPEAR"
            audit.upstream_request(seq, {
                "stream": True,
                "input": [{
                    "type": "reasoning",
                    "id": "reasoning_1",
                    "content": [{"type": "reasoning_text", "text": secret_reasoning}],
                }],
            })
            audit.upstream_stream(seq, [{
                "type": "response.output_item.done",
                "item": {
                    "type": "reasoning",
                    "id": "reasoning_2",
                    "content": [{"type": "reasoning_text", "text": secret_reasoning}],
                },
            }])
            audit.stream_response(seq, [
                'data: {"type":"response.created","response":{"id":"resp_1"}}',
                'data: {"type":"response.completed","response":{"id":"resp_1","status":"completed"}}',
            ])
            text = (root / AUDIT_FILE).read_text(encoding="utf-8")
            self.assertNotIn(secret_prompt, text)
            self.assertNotIn(secret_output, text)
            self.assertNotIn(secret_auth, text)
            self.assertNotIn(secret_reasoning, text)
            self.assertNotIn("resp_private", text)
            self.assertNotIn("session_private", text)
            records = [json.loads(line) for line in text.splitlines()]
            self.assertEqual(records[0]["input_item_types"], ["additional_tools", "message", "function_call_output"])
            self.assertEqual(records[0]["input_items"][0]["nested_tool_count"], 1)
            self.assertEqual(records[0]["input_items"][0]["nested_tool_names"], ["shell_command"])
            self.assertEqual(records[0]["input_items"][0]["nested_tool_shapes"][0]["keys"], ["name", "parameters", "type"])
            self.assertEqual(records[0]["tool_names"], ["read_file"])
            self.assertEqual(records[1]["reasoning_item_count"], 1)
            self.assertEqual(records[1]["input_items"][0]["content_types"], ["reasoning_text"])
            self.assertEqual(records[1]["input_items"][0]["content_text_length"], len(secret_reasoning))
            self.assertEqual(records[2]["reasoning_item_count"], 1)
            self.assertEqual(records[3]["response_id"], "resp_1")


if __name__ == "__main__":
    unittest.main()
