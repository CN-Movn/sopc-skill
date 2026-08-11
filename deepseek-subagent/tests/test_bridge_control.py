from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

from deepseek_subagent.bridges.opencode_go.auth import BridgeAuth  # noqa: E402
from deepseek_subagent.bridges.opencode_go.control import BRIDGE_ABI_VERSION  # noqa: E402
from deepseek_subagent.bridges.opencode_go.server import BridgeServer  # noqa: E402
from deepseek_subagent.bridges.opencode_go.session_store import SessionStore  # noqa: E402


class BridgeControlTests(unittest.TestCase):
    def setUp(self) -> None:
        auth = BridgeAuth(local_token="local-control-test-token")
        auth._key = "upstream-test-key"
        self.server = BridgeServer(
            auth,
            SessionStore(),
            port=0,
            instance_id="instance-under-test",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _request(self, path: str, token: str, payload: dict | None = None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=body,
            method="GET" if payload is None else "POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_health_publishes_abi_and_instance_identity(self) -> None:
        status, payload = self._request("/health", "local-control-test-token")
        self.assertEqual(status, 200)
        self.assertEqual(payload["bridge_abi_version"], BRIDGE_ABI_VERSION)
        self.assertEqual(payload["bridge_instance_id"], "instance-under-test")

    def test_shutdown_rejects_invalid_token_and_instance(self) -> None:
        status, payload = self._request(
            "/control/shutdown",
            "wrong-token",
            {"bridge_instance_id": "instance-under-test"},
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "local_bridge_token_invalid")
        self.assertFalse(self.server.shutdown_requested.is_set())

        status, payload = self._request(
            "/control/shutdown",
            "local-control-test-token",
            {"bridge_instance_id": "wrong-instance"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "bridge_instance_mismatch")
        self.assertFalse(self.server.shutdown_requested.is_set())

    def test_shutdown_accepts_only_authenticated_matching_instance(self) -> None:
        status, payload = self._request(
            "/control/shutdown",
            "local-control-test-token",
            {"bridge_instance_id": "instance-under-test"},
        )
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "shutdown_accepted")
        self.assertTrue(self.server.shutdown_requested.wait(1))


if __name__ == "__main__":
    unittest.main()
