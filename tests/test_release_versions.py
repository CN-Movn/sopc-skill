from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_release_versions.py"
SPEC = importlib.util.spec_from_file_location("validate_release_versions", SCRIPT)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


class RepositoryReleaseVersionTests(unittest.TestCase):
    def test_shipped_metadata_is_consistent(self) -> None:
        self.assertEqual([], VALIDATOR.validate(ROOT))

    def test_stale_root_readme_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for skill, version in (
                ("rtl-style", "2.2.2"),
                ("py-hosttool", "1.2.2"),
                ("deepseek-subagent", "1.7.3"),
            ):
                skill_dir = root / skill
                skill_dir.mkdir(parents=True)
                (skill_dir / "VERSION").write_text(version + "\n", encoding="utf-8")
            (root / "README.md").write_text(
                "| Skill | 当前版本 | 主要解决什么问题 | 核心优势 |\n"
                "| :--- | :---: | :--- | :--- |\n"
                "| [`rtl-style`](./rtl-style) | **v2.2.1** | x | x |\n"
                "| [`py-hosttool`](./py-hosttool) | **v1.2.2** | x | x |\n"
                "| [`deepseek-subagent`](./deepseek-subagent) | **v1.7.3** | x | x |\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate(root)
            self.assertIn(
                "README.md version mismatch for rtl-style: declared 2.2.1, VERSION 2.2.2",
                errors,
            )

    def test_missing_root_readme_row_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for skill, version in (
                ("rtl-style", "2.2.2"),
                ("py-hosttool", "1.2.2"),
                ("deepseek-subagent", "1.7.3"),
            ):
                skill_dir = root / skill
                skill_dir.mkdir(parents=True)
                (skill_dir / "VERSION").write_text(version + "\n", encoding="utf-8")
            (root / "README.md").write_text(
                "| Skill | 当前版本 | 主要解决什么问题 | 核心优势 |\n"
                "| :--- | :---: | :--- | :--- |\n"
                "| [`rtl-style`](./rtl-style) | **v2.2.2** | x | x |\n"
                "| [`py-hosttool`](./py-hosttool) | **v1.2.2** | x | x |\n",
                encoding="utf-8",
            )
            errors = VALIDATOR.validate(root)
            self.assertIn(
                "README.md missing current Skill row: deepseek-subagent",
                errors,
            )


if __name__ == "__main__":
    unittest.main()
