from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

from deepseek_subagent.core.agent_handoff import (  # noqa: E402
    default_handoff_root,
    handoff_path,
    initialize_handoff,
    issue_handoff_turn,
    legacy_handoff_path,
    verify_handoff_update,
)
from deepseek_subagent.core.errors import ManagerError  # noqa: E402


class AgentHandoffTests(unittest.TestCase):
    def test_stable_role_and_scope_produce_deterministic_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "handoffs"
            first = initialize_handoff(root, "Tom", "ARQ RX scheduler", root=store)
            second = initialize_handoff(root, "Tom", "ARQ RX scheduler", root=store)
            other = handoff_path(root, "Tom", "ARQ TX scheduler", root=store)
        self.assertEqual(first["handoff_file"], second["handoff_file"])
        self.assertTrue(Path(first["handoff_file"]).name.startswith("tom--"))
        self.assertNotEqual(Path(first["handoff_file"]), other)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])

    def test_default_root_is_the_installed_skill_local_handoffs(self) -> None:
        default = default_handoff_root()
        self.assertTrue(str(default).endswith(os.path.join(".local", "handoffs")))
        self.assertTrue(default.is_absolute())

    def test_fresh_handoff_root_is_created_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            store = root / "local" / "handoffs"
            self.assertFalse(store.exists())
            initialized = initialize_handoff(project, "Tom", "scope", root=store)
            self.assertTrue(store.is_dir())
            self.assertEqual(Path(initialized["handoff_root"]), store.resolve())
            self.assertEqual(Path(initialized["handoff_file"]).parent, store.resolve())
            self.assertTrue(Path(initialized["handoff_file"]).is_file())

    def test_project_working_directory_is_never_polluted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            initialize_handoff(project, "Tom", "scope", root=root / "handoffs")
            self.assertFalse((project / ".deepseek-subagent").exists())
            self.assertFalse((project / ".deepseek-subagent" / "handoffs").exists())

    def test_identical_role_and_scope_in_different_projects_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "handoffs"
            project_a = root / "project-a"
            project_a.mkdir()
            project_b = root / "project-b"
            project_b.mkdir()
            first = initialize_handoff(project_a, "Tom", "ARQ RX scheduler", root=store)
            second = initialize_handoff(project_b, "Tom", "ARQ RX scheduler", root=store)
            self.assertEqual(first["stable_role"], second["stable_role"])
            self.assertEqual(first["scope"], second["scope"])
            self.assertNotEqual(first["handoff_file"], second["handoff_file"])
            self.assertNotEqual(first["handoff_key"], second["handoff_key"])

    def test_equivalent_project_path_forms_resolve_to_one_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "proj"
            project.mkdir()
            store = root / "handoffs"
            plain = handoff_path(project, "Tom", "scope", root=store)
            trailing = handoff_path(str(project) + os.sep, "Tom", "scope", root=store)
            dotted = handoff_path(project / ".", "Tom", "scope", root=store)
            self.assertEqual(plain, trailing)
            self.assertEqual(plain, dotted)

    def test_existing_log_is_preserved_and_incompatible_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "handoffs"
            initialized = initialize_handoff(root, "Reviewer", "scope", root=store)
            path = Path(initialized["handoff_file"])
            path.write_text(path.read_text(encoding="utf-8") + "\nkept\n", encoding="utf-8")
            initialize_handoff(root, "Reviewer", "scope", root=store)
            self.assertIn("kept", path.read_text(encoding="utf-8"))

            conflict = handoff_path(root, "Other", "scope", root=store)
            conflict.parent.mkdir(parents=True, exist_ok=True)
            conflict.write_text("unrelated", encoding="utf-8")
            with self.assertRaises(ManagerError) as raised:
                initialize_handoff(root, "Other", "scope", root=store)
            self.assertEqual(raised.exception.code, "handoff_file_conflict")

    def test_turn_update_requires_append_and_exact_token_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            initialized = initialize_handoff(Path(directory), "Tom", "scope", root=Path(directory) / "handoffs")
            path = Path(initialized["handoff_file"])
            turn = issue_handoff_turn(path)
            missing = verify_handoff_update(
                path,
                turn["baseline_size"],
                turn["baseline_sha256"],
                turn["turn_token"],
            )
            self.assertFalse(missing["updated"])
            self.assertEqual(missing["error_code"], "handoff_update_missing")

            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n### now — task\n<!-- deepseek-subagent-turn token=00000000-0000-4000-8000-000000000000 -->\n")
            wrong = verify_handoff_update(
                path,
                turn["baseline_size"],
                turn["baseline_sha256"],
                turn["turn_token"],
            )
            self.assertFalse(wrong["updated"])
            self.assertEqual(wrong["error_code"], "handoff_turn_marker_missing")

            next_turn = issue_handoff_turn(path)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    "\n### now — verified task\n"
                    + next_turn["required_marker"]
                    + "\n- Decisions and result: complete\n"
                )
            verified = verify_handoff_update(
                path,
                next_turn["baseline_size"],
                next_turn["baseline_sha256"],
                next_turn["turn_token"],
            )
            self.assertTrue(verified["updated"])
            self.assertEqual(verified["status"], "handoff_update_verified")

    def test_rewritten_history_is_rejected_even_when_file_grows_with_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            initialized = initialize_handoff(Path(directory), "Tom", "scope", root=Path(directory) / "handoffs")
            path = Path(initialized["handoff_file"])
            turn = issue_handoff_turn(path)
            replacement = (
                "# replacement history\n\n"
                + turn["required_marker"]
                + "\n"
                + ("fabricated prior record\n" * 200)
            )
            self.assertGreater(len(replacement.encode("utf-8")), turn["baseline_size"])
            path.write_text(replacement, encoding="utf-8")

            result = verify_handoff_update(
                path,
                turn["baseline_size"],
                turn["baseline_sha256"],
                turn["turn_token"],
            )

            self.assertFalse(result["updated"])
            self.assertEqual(result["status"], "handoff_history_modified")
            self.assertEqual(result["error_code"], "handoff_history_modified")

    def test_legacy_project_local_log_is_detected_reported_and_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            store = root / "handoffs"
            legacy_key = hashlib.sha256("tom\nscope".encode("utf-8")).hexdigest()[:12]
            legacy = legacy_handoff_path(project, "Tom", "scope")
            self.assertTrue(legacy.name.endswith(f"--{legacy_key}.md"))
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text(
                f"<!-- deepseek-subagent-handoff:v1 key={legacy_key} -->\n# legacy log\n",
                encoding="utf-8",
            )
            original = legacy.read_bytes()

            initialized = initialize_handoff(project, "Tom", "scope", root=store)

            self.assertTrue(initialized["legacy_handoff_detected"])
            self.assertTrue(initialized["legacy_handoff_verified"])
            self.assertFalse(initialized["legacy_handoff_migrated"])
            self.assertEqual(Path(initialized["legacy_handoff_path"]), legacy)
            self.assertEqual(legacy.read_bytes(), original)
            self.assertNotEqual(Path(initialized["handoff_file"]), legacy)
            self.assertTrue(Path(initialized["handoff_file"]).is_file())
            self.assertTrue(legacy.is_file())

    def test_legacy_file_without_marker_is_reported_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            legacy = legacy_handoff_path(project, "Tom", "scope")
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text("unrelated legacy content\n", encoding="utf-8")
            initialized = initialize_handoff(project, "Tom", "scope", root=root / "handoffs")
            self.assertTrue(initialized["legacy_handoff_detected"])
            self.assertFalse(initialized["legacy_handoff_verified"])
            self.assertFalse(initialized["legacy_handoff_migrated"])

    def test_project_root_must_be_existing_absolute_directory(self) -> None:
        with self.assertRaises(ManagerError) as relative:
            initialize_handoff(Path("relative"), "Tom", "scope")
        self.assertEqual(relative.exception.code, "handoff_project_root_not_absolute")
        with self.assertRaises(ManagerError) as missing:
            initialize_handoff(Path(tempfile.gettempdir()) / "does-not-exist-anywhere", "Tom", "scope")
        self.assertEqual(missing.exception.code, "handoff_project_root_missing")


if __name__ == "__main__":
    unittest.main()
