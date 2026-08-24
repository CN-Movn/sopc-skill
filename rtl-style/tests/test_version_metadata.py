from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_version.py"
SPEC = importlib.util.spec_from_file_location("rtl_validate_version", SCRIPT)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


class VersionMetadataTests(unittest.TestCase):
    def test_shipped_metadata_is_consistent(self) -> None:
        self.assertEqual([], VALIDATOR.validate(ROOT))

    def test_invalid_version_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text("v2.2.2\n", encoding="utf-8")
            (root / "CHANGELOG.md").write_text("## [2.2.2] - 2026-08-24\n", encoding="utf-8")
            (root / "README.md").write_text("Current release: v2.2.2\n", encoding="utf-8")
            errors = VALIDATOR.validate(root)
            self.assertIn("VERSION must contain only MAJOR.MINOR.PATCH SemVer", errors)

    def test_stale_current_release_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text("2.2.2\n", encoding="utf-8")
            (root / "CHANGELOG.md").write_text("## [2.2.2] - 2026-08-24\n", encoding="utf-8")
            (root / "README.md").write_text("Current release: v2.2.1\n", encoding="utf-8")
            errors = VALIDATOR.validate(root)
            self.assertIn("README.md current release does not match VERSION", errors)


if __name__ == "__main__":
    unittest.main()
