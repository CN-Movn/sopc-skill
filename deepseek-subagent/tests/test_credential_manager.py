from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "scripts" / "skill_manager.py"


def load_manager():
    spec = importlib.util.spec_from_file_location("deepseek_skill_manager_test", MANAGER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CredentialManagerTests(unittest.TestCase):
    def test_credentials_status_reports_presence_without_value(self) -> None:
        manager = load_manager()
        secret = "manager-sentinel-key"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "opencode-go.key"
            target.write_text(secret + "\n", encoding="utf-8")
            output = io.StringIO()
            with (
                mock.patch.object(manager, "_local_key_file", return_value=target),
                mock.patch.object(manager, "_state", return_value=mock.Mock(state_root=Path(directory))),
                mock.patch.object(manager, "_lifecycle", return_value=mock.Mock()),
                redirect_stdout(output),
            ):
                code = manager.main(["credentials", "status", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "credential_present")
        self.assertNotIn(secret, output.getvalue())

    def test_credentials_set_and_key_argument_are_removed(self) -> None:
        manager = load_manager()
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            manager.main(["credentials", "set", "--key", "must-not-be-used"])

    def test_status_uses_real_doctor_pipeline(self) -> None:
        manager = load_manager()
        payload = {"status": "partial", "failure_stage": "auth_command", "error_code": "auth_command_failed"}
        with (
            mock.patch.object(manager, "_state", return_value=mock.Mock()),
            mock.patch.object(manager, "_lifecycle", return_value=mock.Mock()),
            mock.patch.object(manager, "_doctor", return_value=(2, payload)) as doctor,
            redirect_stdout(io.StringIO()),
        ):
            code = manager.main(["status", "--json"])
        self.assertEqual(code, 2)
        doctor.assert_called_once()


if __name__ == "__main__":
    unittest.main()
