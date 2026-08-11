from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = SKILL_ROOT / "runtime"
sys.path.insert(0, str(RUNTIME_ROOT))

from deepseek_subagent.core import atomic  # noqa: E402
from deepseek_subagent.core.errors import ManagerError  # noqa: E402


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_replaces_target_and_leaves_no_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "managed.json"
            target.write_bytes(b"old")

            atomic.atomic_write(target, b"new")

            self.assertEqual(target.read_bytes(), b"new")
            self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])

    def test_create_permission_denied_fails_once_with_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "managed.json"
            with mock.patch.object(atomic.os, "open", side_effect=PermissionError("denied")) as opened:
                with self.assertRaises(ManagerError) as raised:
                    atomic.atomic_write(target, b"new")

            self.assertEqual(opened.call_count, 1)
            self.assertEqual(raised.exception.code, "managed_write_permission_denied")
            self.assertEqual(raised.exception.details["path"], str(target))
            self.assertEqual(raised.exception.details["stage"], "create_sibling")

    def test_replace_permission_denied_removes_created_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "managed.json"
            target.write_bytes(b"old")
            with mock.patch.object(atomic.os, "replace", side_effect=PermissionError("denied")):
                with self.assertRaises(ManagerError) as raised:
                    atomic.atomic_write(target, b"new")

            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(raised.exception.details["stage"], "replace_target")
            self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
