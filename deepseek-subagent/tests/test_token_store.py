from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

from deepseek_subagent.bridges.opencode_go import token_store  # noqa: E402
from deepseek_subagent.bridges.opencode_go.token_store import (  # noqa: E402
    ACL_PRINCIPALS_FILE,
    TOKEN_FILE,
    TOKEN_STATE_FILE,
    _atomic_secret_write,
    _resolve_windows_principal_sids,
    _write_principal_state,
    describe_token,
    ensure_token,
    repair_token_acl,
    restore_token,
    rotate_token,
    token_fingerprint,
)
from deepseek_subagent.core.errors import ManagerError  # noqa: E402


class TokenStoreTests(unittest.TestCase):
    def test_concurrent_first_install_serializes_token_pair_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".local"
            start = threading.Barrier(8)
            counter_lock = threading.Lock()
            active = 0
            maximum = 0
            tokens: list[str] = []
            errors: list[BaseException] = []
            original_write_pair = token_store._write_pair

            def slow_write_pair(*args, **kwargs):
                nonlocal active, maximum
                with counter_lock:
                    active += 1
                    maximum = max(maximum, active)
                try:
                    time.sleep(0.03)
                    return original_write_pair(*args, **kwargs)
                finally:
                    with counter_lock:
                        active -= 1

            def worker():
                try:
                    start.wait(timeout=5)
                    token, _state = ensure_token(root)
                    tokens.append(token)
                except BaseException as exc:  # pragma: no cover - asserted below
                    errors.append(exc)

            with (
                mock.patch.object(token_store, "_write_pair", side_effect=slow_write_pair),
                mock.patch.object(token_store, "_restrict_to_current_user"),
            ):
                threads = [threading.Thread(target=worker) for _ in range(8)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)

            self.assertEqual(errors, [])
            self.assertEqual(len(tokens), 8)
            self.assertEqual(len(set(tokens)), 1)
            self.assertEqual(maximum, 1)
            self.assertTrue(describe_token(root)["token_state_consistent"])

    def test_legacy_runtime_token_migrates_without_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "runtime"
            local = root / ".local"
            legacy.mkdir()
            legacy.joinpath("token.txt").write_text("legacy-token\n", encoding="utf-8")
            legacy.joinpath("token-state.json").write_text(
                json.dumps(
                    {
                        "token_version": 1,
                        "token_generation": 7,
                        "token_fingerprint": "ignored-during-migration",
                        "created_at": "2026-08-01T00:00:00",
                        "rotated_at": None,
                    }
                ),
                encoding="utf-8",
            )
            token, state = ensure_token(local, legacy_workdir=legacy)
            self.assertEqual(token, "legacy-token")
            self.assertEqual(state["token_generation"], 7)
            self.assertFalse((legacy / "token.txt").exists())
            self.assertFalse((legacy / "token-state.json").exists())

    def test_ensure_reuses_existing_token_and_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            first_token, first = ensure_token(workdir)
            second_token, second = ensure_token(workdir)
            self.assertEqual(first_token, second_token)
            self.assertEqual(first["token_generation"], 1)
            self.assertEqual(first, second)

    def test_rotation_is_explicit_and_increments_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            token, before = ensure_token(workdir)
            after = rotate_token(workdir)
            new_token = (workdir / TOKEN_FILE).read_text(encoding="utf-8").strip()
            self.assertNotEqual(token, new_token)
            self.assertEqual(after["token_generation"], before["token_generation"] + 1)
            self.assertNotEqual(after["token_fingerprint"], before["token_fingerprint"])

    def test_restore_preserves_token_and_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            token, state = ensure_token(workdir)
            (workdir / TOKEN_FILE).unlink()
            restore_token(workdir, token, state)
            restored, restored_state = ensure_token(workdir)
            self.assertEqual(restored, token)
            self.assertEqual(restored_state["token_generation"], state["token_generation"])

    def test_describe_detects_state_mismatch_without_exposing_token(self):
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            token, _ = ensure_token(workdir)
            state_path = workdir / TOKEN_STATE_FILE
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["token_fingerprint"] = "sha256:0000000000000000"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            description = describe_token(workdir)
            self.assertFalse(description["token_state_consistent"])
            self.assertNotIn(token, json.dumps(description))

    def test_ensure_repairs_metadata_mismatch_without_rotating_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token, before = ensure_token(root)
            state_path = root / TOKEN_STATE_FILE
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["token_fingerprint"] = "sha256:0000000000000000"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            repaired_token, repaired = ensure_token(root)
            self.assertEqual(repaired_token, token)
            self.assertEqual(repaired["token_generation"], before["token_generation"])
            self.assertTrue(describe_token(root)["token_state_consistent"])

    def test_empty_existing_token_fails_without_implicit_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / TOKEN_FILE).write_text("", encoding="utf-8")
            with self.assertRaises(ManagerError) as raised:
                ensure_token(root)
            self.assertEqual(raised.exception.code, "local_bridge_token_invalid")
            self.assertEqual((root / TOKEN_FILE).read_text(encoding="utf-8"), "")

    def test_missing_token_with_existing_metadata_fails_without_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / TOKEN_STATE_FILE).write_text(
                json.dumps({"token_version": 1, "token_generation": 9}),
                encoding="utf-8",
            )
            with self.assertRaises(ManagerError) as raised:
                ensure_token(root)
            self.assertEqual(raised.exception.code, "local_bridge_token_missing")
            self.assertFalse((root / TOKEN_FILE).exists())

    def test_acl_failure_preserves_existing_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / TOKEN_FILE
            target.write_text("original-token\n", encoding="utf-8")
            with (
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store._restrict_to_current_user",
                    side_effect=OSError("acl failed"),
                ),
                self.assertRaises(OSError),
            ):
                _atomic_secret_write(target, b"replacement-token\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "original-token\n")

    def test_windows_acl_keeps_sandbox_and_real_profile_owner(self):
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps(
                {
                    "current_sid": "S-1-5-21-100-200-300-4001",
                    "current_name": "MOVN-COMPANY\\CodexSandboxOffline",
                    "profile_sid": "S-1-5-21-100-200-300-500",
                    "profile_name": "MOVN-COMPANY\\Administrator",
                    "profile_path": "C:\\Users\\Administrator",
                }
            ),
        )
        with mock.patch(
            "deepseek_subagent.bridges.opencode_go.token_store.subprocess.run",
            return_value=completed,
        ):
            principals = _resolve_windows_principal_sids(
                Path("C:/Users/Administrator/.codex/skills/deepseek-subagent/.local/local-bridge-token.txt")
            )
        self.assertEqual(
            principals,
            ["S-1-5-21-100-200-300-4001", "S-1-5-21-100-200-300-500"],
        )

    SID_SANDBOX = "S-1-5-21-100-200-300-4001"
    SID_ADMIN = "S-1-5-21-100-200-300-500"

    def _sid_payload(self, current_sid: str, current_name: str):
        return mock.Mock(
            returncode=0,
            stdout=json.dumps(
                {
                    "current_sid": current_sid,
                    "current_name": current_name,
                    "profile_sid": self.SID_ADMIN,
                    "profile_name": "MOVN-COMPANY\\Administrator",
                    "profile_path": "C:\\Users\\Administrator",
                }
            ),
        )

    def _consistent_token(self, token_dir: Path, token: str) -> None:
        (token_dir / TOKEN_FILE).write_bytes((token + "\n").encode("utf-8"))
        (token_dir / TOKEN_STATE_FILE).write_text(
            json.dumps(
                {
                    "token_version": 1,
                    "token_generation": 1,
                    "token_fingerprint": token_fingerprint(token),
                    "created_at": "2026-08-01T00:00:00",
                    "rotated_at": None,
                }
            ),
            encoding="utf-8",
        )

    def test_acl_principals_sandbox_elevated_new_sandbox_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token_dir = root / ".local"
            token_dir.mkdir()
            target = token_dir / TOKEN_FILE
            self._consistent_token(token_dir, "stable-token")

            with mock.patch(
                "deepseek_subagent.bridges.opencode_go.token_store.subprocess.run",
                return_value=self._sid_payload(self.SID_SANDBOX, "MOVN-COMPANY\\CodexSandboxOffline"),
            ):
                principals = _resolve_windows_principal_sids(target, token_dir)
            self.assertEqual(set(principals), {self.SID_SANDBOX, self.SID_ADMIN})
            state = json.loads((token_dir / ACL_PRINCIPALS_FILE).read_text(encoding="utf-8"))
            self.assertEqual(state["profile_owner_sid"], self.SID_ADMIN)
            self.assertEqual(state["sandbox_sids"], [self.SID_SANDBOX])

            elevated = self._sid_payload(self.SID_ADMIN, "MOVN-COMPANY\\Administrator")
            with mock.patch(
                "deepseek_subagent.bridges.opencode_go.token_store.subprocess.run",
                return_value=elevated,
            ):
                principals = _resolve_windows_principal_sids(target, token_dir)
            self.assertEqual(set(principals), {self.SID_SANDBOX, self.SID_ADMIN})

            with (
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store.subprocess.run",
                    return_value=elevated,
                ),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store._restrict_to_current_user"
                ) as restrict,
            ):
                repair_token_acl(token_dir)
            granted = restrict.call_args_list[0].kwargs["principals"]
            self.assertIn(self.SID_SANDBOX, granted)
            self.assertIn(self.SID_ADMIN, granted)

            with mock.patch(
                "deepseek_subagent.bridges.opencode_go.token_store.repair_token_acl"
            ) as repair:
                token, state = ensure_token(token_dir)
            self.assertEqual(token, "stable-token")
            self.assertEqual(state["token_generation"], 1)
            repair.assert_not_called()
            self.assertEqual((token_dir / TOKEN_FILE).read_bytes(), b"stable-token\n")

    def test_acl_principals_fresh_install_creates_state_and_hardens(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token_dir = root / ".local"
            token_dir.mkdir()
            target = token_dir / TOKEN_FILE
            target.write_bytes(b"t\n")
            with mock.patch(
                "deepseek_subagent.bridges.opencode_go.token_store.subprocess.run",
                return_value=self._sid_payload(self.SID_SANDBOX, "MOVN-COMPANY\\CodexSandboxOffline"),
            ):
                principals = _resolve_windows_principal_sids(target, token_dir)
            self.assertIn(self.SID_SANDBOX, principals)
            self.assertIn(self.SID_ADMIN, principals)
            self.assertTrue((token_dir / ACL_PRINCIPALS_FILE).is_file())
            state = json.loads((token_dir / ACL_PRINCIPALS_FILE).read_text(encoding="utf-8"))
            self.assertEqual(state["sandbox_sids"], [self.SID_SANDBOX])

    def test_acl_principals_corrupt_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token_dir = root / ".local"
            token_dir.mkdir()
            target = token_dir / TOKEN_FILE
            target.write_bytes(b"t\n")
            elevated = self._sid_payload(self.SID_ADMIN, "MOVN-COMPANY\\Administrator")

            (token_dir / ACL_PRINCIPALS_FILE).write_text("{broken", encoding="utf-8")
            with mock.patch(
                "deepseek_subagent.bridges.opencode_go.token_store.subprocess.run",
                return_value=elevated,
            ):
                with self.assertRaises(ManagerError) as raised:
                    _resolve_windows_principal_sids(target, token_dir)
            self.assertEqual(raised.exception.code, "local_bridge_acl_principal_state_invalid")

            (token_dir / ACL_PRINCIPALS_FILE).write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "profile_owner_sid": "not-a-sid",
                        "sandbox_sids": ["S-1-5-21-999"],
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch(
                "deepseek_subagent.bridges.opencode_go.token_store.subprocess.run",
                return_value=elevated,
            ):
                with self.assertRaises(ManagerError) as raised:
                    _resolve_windows_principal_sids(target, token_dir)
            self.assertEqual(raised.exception.code, "local_bridge_acl_principal_state_invalid")

            (token_dir / ACL_PRINCIPALS_FILE).write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "profile_owner_sid": self.SID_ADMIN,
                        "sandbox_sids": "corrupted",
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch(
                "deepseek_subagent.bridges.opencode_go.token_store.subprocess.run",
                return_value=elevated,
            ):
                with self.assertRaises(ManagerError) as raised:
                    _resolve_windows_principal_sids(target, token_dir)
            self.assertEqual(raised.exception.code, "local_bridge_acl_principal_state_invalid")

            (token_dir / ACL_PRINCIPALS_FILE).write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "profile_owner_sid": self.SID_ADMIN,
                        "sandbox_sids": [self.SID_SANDBOX, "not-a-sid"],
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch(
                "deepseek_subagent.bridges.opencode_go.token_store.subprocess.run",
                return_value=elevated,
            ):
                with self.assertRaises(ManagerError) as raised:
                    _resolve_windows_principal_sids(target, token_dir)
            self.assertEqual(raised.exception.code, "local_bridge_acl_principal_state_invalid")

    def test_acl_principal_state_write_failure_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token_dir = root / ".local"
            token_dir.mkdir()
            target = token_dir / TOKEN_FILE
            target.write_bytes(b"t\n")
            sandbox = self._sid_payload(self.SID_SANDBOX, "MOVN-COMPANY\\CodexSandboxOffline")

            with (
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store.subprocess.run",
                    return_value=sandbox,
                ),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store.atomic_write",
                    side_effect=OSError(5, "Access is denied"),
                ),
            ):
                with self.assertRaises(ManagerError) as raised:
                    _resolve_windows_principal_sids(target, token_dir)
            self.assertEqual(raised.exception.code, "local_bridge_acl_principal_state_write_failed")

            with (
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store.subprocess.run",
                    return_value=sandbox,
                ),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store.atomic_write",
                    side_effect=OSError(5, "Access is denied"),
                ),
            ):
                with self.assertRaises(ManagerError) as raised:
                    _write_principal_state(token_dir, {"schema_version": 1})
            self.assertEqual(raised.exception.code, "local_bridge_acl_principal_state_write_failed")

    def test_historical_admin_only_acl_bootstrap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token_dir = root / ".local"
            token_dir.mkdir()
            target = token_dir / TOKEN_FILE
            self._consistent_token(token_dir, "historical-token")
            token_before = (token_dir / TOKEN_FILE).read_bytes()
            state_before = (token_dir / TOKEN_STATE_FILE).read_bytes()
            sandbox_payload = self._sid_payload(self.SID_SANDBOX, "MOVN-COMPANY\\CodexSandboxOffline")
            elevated_payload = self._sid_payload(self.SID_ADMIN, "MOVN-COMPANY\\Administrator")

            sandbox_read = {"count": 0}
            real_read = Path.read_text

            def blocked_read(instance, *args, **kwargs):
                if str(instance) == str(target) and sandbox_read["count"] == 0:
                    sandbox_read["count"] += 1
                    raise PermissionError(13, "Access is denied")
                return real_read(instance, *args, **kwargs)

            with (
                mock.patch.object(Path, "read_text", autospec=True, side_effect=blocked_read),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store.subprocess.run",
                    return_value=sandbox_payload,
                ),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store._restrict_to_current_user",
                    side_effect=OSError(5, "Access is denied"),
                ),
            ):
                with self.assertRaises(ManagerError) as raised:
                    ensure_token(token_dir)
            self.assertEqual(raised.exception.code, "local_bridge_token_acl_repair_failed")
            state = json.loads((token_dir / ACL_PRINCIPALS_FILE).read_text(encoding="utf-8"))
            self.assertEqual(state["profile_owner_sid"], self.SID_ADMIN)
            self.assertEqual(state["sandbox_sids"], [self.SID_SANDBOX])
            self.assertEqual((token_dir / TOKEN_FILE).read_bytes(), token_before)

            with (
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store.subprocess.run",
                    return_value=elevated_payload,
                ),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store._restrict_to_current_user"
                ) as restrict,
            ):
                repair_token_acl(token_dir)
            granted = restrict.call_args_list[0].kwargs["principals"]
            self.assertIn(self.SID_SANDBOX, granted)
            self.assertIn(self.SID_ADMIN, granted)
            self.assertEqual((token_dir / TOKEN_FILE).read_bytes(), token_before)
            self.assertEqual((token_dir / TOKEN_STATE_FILE).read_bytes(), state_before)

            with mock.patch(
                "deepseek_subagent.bridges.opencode_go.token_store.repair_token_acl"
            ) as repair:
                token, state = ensure_token(token_dir)
            self.assertEqual(token, "historical-token")
            self.assertEqual(state["token_generation"], 1)
            repair.assert_not_called()
            self.assertEqual((token_dir / TOKEN_FILE).read_bytes(), token_before)

    def test_acl_repair_does_not_change_token_or_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token_path = root / TOKEN_FILE
            state_path = root / TOKEN_STATE_FILE
            token_path.write_bytes(b"stable-token\n")
            state_path.write_bytes(
                json.dumps(
                    {
                        "token_version": 1,
                        "token_generation": 7,
                        "token_fingerprint": "sha256:unchanged",
                    }
                ).encode("utf-8")
            )
            token_before = token_path.read_bytes()
            state_before = state_path.read_bytes()
            with (
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store._resolve_windows_principal_sids",
                    return_value=["S-1-5-21-100-200-300-4001", "S-1-5-21-100-200-300-500"],
                ),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store._restrict_to_current_user"
                ) as restrict,
            ):
                result = repair_token_acl(root)
            self.assertEqual(result["status"], "token_acl_repaired")
            self.assertEqual(restrict.call_count, 2)
            granted = restrict.call_args_list[0].kwargs["principals"]
            self.assertIs(restrict.call_args_list[1].kwargs["principals"], granted)
            self.assertEqual(token_path.read_bytes(), token_before)
            self.assertEqual(state_path.read_bytes(), state_before)

    def test_ensure_consistent_existing_token_skips_acl_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token, _state = ensure_token(root)
            token_before = (root / TOKEN_FILE).read_bytes()
            state_before = (root / TOKEN_STATE_FILE).read_bytes()
            with mock.patch(
                "deepseek_subagent.bridges.opencode_go.token_store.repair_token_acl"
            ) as repair:
                reused, _ = ensure_token(root)
            self.assertEqual(reused, token)
            repair.assert_not_called()
            self.assertEqual((root / TOKEN_FILE).read_bytes(), token_before)
            self.assertEqual((root / TOKEN_STATE_FILE).read_bytes(), state_before)

    def test_ensure_access_denied_repairs_acl_once_then_reads(self):
        real_read = Path.read_text
        attempts = {"count": 0}

        def flaky_read(instance, *args, **kwargs):
            if str(instance) == str(token_path) and attempts["count"] == 0:
                attempts["count"] += 1
                raise PermissionError(13, "Access is denied")
            return real_read(instance, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token_path = root / TOKEN_FILE
            state_path = root / TOKEN_STATE_FILE
            token_path.write_bytes(b"stable-token\n")
            state_path.write_bytes(
                json.dumps(
                    {
                        "token_version": 1,
                        "token_generation": 7,
                        "token_fingerprint": token_fingerprint("stable-token"),
                        "created_at": "2026-08-01T00:00:00",
                    }
                ).encode("utf-8")
            )
            original = token_path.read_bytes()
            with (
                mock.patch.object(Path, "read_text", autospec=True, side_effect=flaky_read),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store.repair_token_acl"
                ) as repair,
            ):
                token, state = ensure_token(root)
            self.assertEqual(token, "stable-token")
            self.assertEqual(state["token_generation"], 7)
            self.assertEqual(repair.call_count, 1)
            self.assertEqual(token_path.read_bytes(), original)

    def test_ensure_access_denied_fails_closed_after_one_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token_path = root / TOKEN_FILE
            token_path.write_bytes(b"stable-token\n")
            with (
                mock.patch.object(
                    Path,
                    "read_text",
                    autospec=True,
                    side_effect=PermissionError(13, "Access is denied"),
                ),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store.repair_token_acl"
                ) as repair,
            ):
                with self.assertRaises(ManagerError) as raised:
                    ensure_token(root)
            self.assertEqual(raised.exception.code, "local_bridge_token_unreadable")
            self.assertEqual(repair.call_count, 1)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token_path = root / TOKEN_FILE
            token_path.write_bytes(b"stable-token\n")
            with (
                mock.patch.object(
                    Path,
                    "read_text",
                    autospec=True,
                    side_effect=PermissionError(13, "Access is denied"),
                ),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store.repair_token_acl",
                    side_effect=OSError("acl failed"),
                ),
            ):
                with self.assertRaises(ManagerError) as raised:
                    ensure_token(root)
            self.assertEqual(raised.exception.code, "local_bridge_token_acl_repair_failed")

    def test_ensure_fresh_creation_still_hardens_acl(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store._resolve_windows_principal_sids",
                    return_value=["S-1-5-21-100-200-300-4001"],
                ),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store._restrict_to_current_user"
                ) as restrict,
            ):
                token, _ = ensure_token(root)
            self.assertTrue(token)
            self.assertEqual(restrict.call_count, 2)
            self.assertTrue((root / TOKEN_FILE).is_file())
            self.assertTrue((root / TOKEN_STATE_FILE).is_file())


if __name__ == "__main__":
    unittest.main()
