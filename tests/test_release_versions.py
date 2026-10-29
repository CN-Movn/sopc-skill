from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_release_versions.py"
SPEC = importlib.util.spec_from_file_location("release_validator", SCRIPT)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


class ReleaseVersionTests(unittest.TestCase):
    def make_release(self, root: Path, versions: tuple[str, str] = ("0.1.5", "0.1.5")) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "VERSION").write_text("0.1.5\n", encoding="utf-8")
        rows = []
        for skill, version in zip(VALIDATOR.SKILLS, versions):
            directory = root / skill
            directory.mkdir()
            (directory / "VERSION").write_text(f"{version}\n", encoding="utf-8")
            (directory / "README.md").write_text(f"Current release: v{version}\n", encoding="utf-8")
            (directory / "CHANGELOG.md").write_text(
                f"# Changelog\n\n## [{version}] - 2026-10-29\n", encoding="utf-8"
            )
            rows.append(f"| [`{skill}`](./{skill}) | **{version}** |")
        (root / "README.md").write_text("\n".join(rows) + "\n", encoding="utf-8")

    def test_unified_release_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_release(root)
            self.assertEqual([], VALIDATOR.validate(root))

    def test_skill_version_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_release(root, versions=("0.1.5", "0.1.4"))
            errors = VALIDATOR.validate(root)
            self.assertIn("py-hosttool/VERSION does not match root VERSION", errors)

    def test_stopped_skill_and_stale_link_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_release(root)
            (root / "deepseek-subagent").mkdir()
            with (root / "README.md").open("a", encoding="utf-8") as stream:
                stream.write("[old](./deepseek-subagent)\n")
            errors = VALIDATOR.validate(root)
            self.assertIn("stopped skill still exists: deepseek-subagent", errors)
            self.assertIn("README.md still links to stopped skill: deepseek-subagent", errors)


if __name__ == "__main__":
    unittest.main()
