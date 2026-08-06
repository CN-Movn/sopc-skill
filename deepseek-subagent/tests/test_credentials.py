from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from deepseek_subagent.bridges.opencode_go.auth import AuthError, BridgeAuth  # noqa: E402
from deepseek_subagent.bridges.opencode_go.credentials import (  # noqa: E402
    CredentialError,
    credential_status,
    discover_credential,
    key_file_path,
    validate_api_key,
)
from deepseek_subagent.bridges.opencode_go.server import upstream_call  # noqa: E402
from deepseek_subagent.bridges.opencode_go.upstream import (  # noqa: E402
    UPSTREAM_HEADERS,
    classify_upstream_failure,
)


class FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b"{}"):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        return None


def http_error(status: int, body: bytes = b"{}") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.invalid", status, "rejected", {}, io.BytesIO(body))


class CredentialTests(unittest.TestCase):
    def test_fixed_path_is_under_skill_local_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(key_file_path(directory), Path(directory).resolve() / ".local" / "opencode-go.key")

    def test_missing_key_reports_exact_path_without_searching_elsewhere(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "opencode-go.key"
            self.assertIsNone(discover_credential(target))
            status = credential_status(target)
            self.assertEqual(status["status"], "upstream_key_missing")
            self.assertEqual(status["key_file"], str(target.resolve()))

    def test_single_line_key_is_loaded_from_fixed_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "opencode-go.key"
            target.write_text("fixed-key\n", encoding="utf-8")
            found = discover_credential(target)
            self.assertEqual(found.source, "fixed_local_file")
            self.assertEqual(found.key, "fixed-key")

    def test_multiline_or_empty_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "opencode-go.key"
            for value in ("", "one\ntwo\n"):
                target.write_text(value, encoding="utf-8")
                with self.subTest(value=value), self.assertRaises(CredentialError) as raised:
                    discover_credential(target)
                self.assertEqual(raised.exception.code, "upstream_key_file_invalid")

    def test_bridge_auth_never_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "opencode-go.key"
            target.write_text("fixed-key\n", encoding="utf-8")
            auth = BridgeAuth(local_token="local", key_file=str(target))
            auth.load()
            self.assertEqual(auth.credential_source, "fixed_local_file")
            self.assertEqual(auth.bearer(), "fixed-key")

    def test_missing_bridge_key_reports_stable_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "missing.key"
            auth = BridgeAuth(key_file=str(target))
            with self.assertRaises(AuthError) as raised:
                auth.load()
            self.assertEqual(raised.exception.code, "upstream_key_missing")
            self.assertIn(str(target.resolve()), str(raised.exception))

    def test_validation_and_bridge_use_same_stable_headers(self) -> None:
        opener = mock.Mock(return_value=FakeResponse())
        result = validate_api_key("sentinel", opener=opener)
        self.assertEqual(result.status, "valid")
        request = opener.call_args.args[0]
        actual = {key.lower(): value for key, value in request.header_items()}
        for key, value in UPSTREAM_HEADERS.items():
            self.assertEqual(actual.get(key.lower()), value)

    def test_403_waf_is_not_invalid_key(self) -> None:
        result = validate_api_key(
            "sentinel",
            opener=mock.Mock(side_effect=http_error(403, b"Cloudflare Ray ID - Error code: 1010")),
        )
        self.assertEqual(result.status, "upstream_waf_blocked")

    def test_explicit_403_auth_failure_is_invalid_key(self) -> None:
        result = validate_api_key(
            "sentinel",
            opener=mock.Mock(side_effect=http_error(403, b'{"error":"invalid api key"}')),
        )
        self.assertEqual(result.status, "upstream_key_invalid")

    def test_network_and_service_failures_are_separate(self) -> None:
        network = validate_api_key("sentinel", opener=mock.Mock(side_effect=urllib.error.URLError("offline")))
        service = validate_api_key("sentinel", opener=mock.Mock(side_effect=http_error(503)))
        self.assertEqual(network.status, "upstream_network_error")
        self.assertEqual(service.status, "upstream_service_unavailable")

    def test_unclassified_403_is_forbidden_not_invalid(self) -> None:
        self.assertEqual(classify_upstream_failure(403, "policy denied").code, "upstream_forbidden")

    def test_upstream_call_does_not_retry_or_echo_key(self) -> None:
        auth = mock.Mock()
        auth.bearer.return_value = "sentinel-secret"
        with mock.patch(
            "deepseek_subagent.bridges.opencode_go.server.urllib.request.urlopen",
            side_effect=http_error(403, b"Cloudflare Ray ID - Error code: 1010"),
        ) as opener:
            status, body = upstream_call(auth, "/models", method="GET")
        self.assertEqual(status, 403)
        self.assertEqual(opener.call_count, 1)
        self.assertNotIn("sentinel-secret", body)

    def test_status_never_emits_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "opencode-go.key"
            secret = "sensitive-sentinel-key"
            target.write_text(secret + "\n", encoding="utf-8")
            self.assertNotIn(secret, json.dumps(credential_status(target)))


if __name__ == "__main__":
    unittest.main()
