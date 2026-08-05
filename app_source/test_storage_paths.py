"""Tests for portable storage settings without modifying user configuration."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import storage_paths


class StoragePathsTests(unittest.TestCase):
    def test_default_template_has_required_layout(self) -> None:
        settings = storage_paths._default_settings()
        self.assertIn("root", settings)
        self.assertEqual(Path(settings["root"]), storage_paths.default_storage_root())
        self.assertEqual(set(settings["directories"]), {"raw_archive", "backups", "imports"})

    def test_user_setting_path_is_outside_source_tree(self) -> None:
        self.assertNotEqual(storage_paths.USER_SETTINGS_PATH, Path("config/storage.json"))

    def test_frozen_default_is_beside_executable(self) -> None:
        import platform
        if platform.system() == "Windows":
            executable = Path("C:/Portable/StockAI.exe")
        else:
            executable = Path("/Portable/StockAI.exe")
        with patch.object(storage_paths.sys, "frozen", True, create=True), patch.object(
            storage_paths.sys, "executable", str(executable)
        ):
            self.assertEqual(
                storage_paths.default_storage_root(),
                executable.parent / "StockAI_Data",
            )

    def test_missing_drive_user_setting_is_not_usable(self) -> None:
        settings = {"root": "Z:\\StockAI_Data"}
        settings_path = MagicMock()
        settings_path.exists.return_value = True
        settings_path.read_text.return_value = __import__("json").dumps(settings)
        with patch.object(storage_paths, "USER_SETTINGS_PATH", settings_path), patch(
            "pathlib.Path.exists",
            return_value=False,
        ), patch("pathlib.PurePath.anchor", new_callable=unittest.mock.PropertyMock, return_value="Z:\\"):
            self.assertFalse(storage_paths.has_user_storage_config())


if __name__ == "__main__":
    unittest.main()
