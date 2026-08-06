from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

from deepseek_subagent.bridges.opencode_go.token_store import (  # noqa: E402
    TOKEN_FILE,
    TOKEN_STATE_FILE,
    describe_token,
    ensure_token,
    restore_token,
    rotate_token,
)


class TokenStoreTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
