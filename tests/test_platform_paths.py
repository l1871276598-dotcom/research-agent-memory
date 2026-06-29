import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPOSITORY_ROOT / "src"

if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

import platform_paths


class PlatformPathsTests(unittest.TestCase):
    def test_platform_name_detects_windows(self):
        with patch.object(platform_paths.os, "name", "nt"):
            self.assertEqual(platform_paths.platform_name(), "windows")

    def test_platform_name_detects_macos(self):
        with (
            patch.object(platform_paths.os, "name", "posix"),
            patch.object(platform_paths.sys, "platform", "darwin"),
        ):
            self.assertEqual(platform_paths.platform_name(), "macos")

    def test_platform_name_defaults_to_linux(self):
        with (
            patch.object(platform_paths.os, "name", "posix"),
            patch.object(platform_paths.sys, "platform", "linux"),
        ):
            self.assertEqual(platform_paths.platform_name(), "linux")

    def test_windows_default_state_dir_uses_local_app_data(self):
        local_app_data = str(Path("test-local-app-data").resolve())

        with (
            patch.object(platform_paths, "platform_name", return_value="windows"),
            patch.dict(os.environ, {"LOCALAPPDATA": local_app_data}),
        ):
            self.assertEqual(
                platform_paths.default_state_dir(),
                Path(local_app_data) / "ResearchAgent",
            )

    def test_macos_default_state_dir_uses_application_support(self):
        with patch.object(
            platform_paths,
            "platform_name",
            return_value="macos",
        ):
            self.assertEqual(
                platform_paths.default_state_dir(),
                Path.home()
                / "Library"
                / "Application Support"
                / "ResearchAgent",
            )

    def test_linux_default_state_dir_uses_xdg_state_home(self):
        xdg_state_home = str(Path("test-xdg-state").resolve())

        with (
            patch.object(platform_paths, "platform_name", return_value="linux"),
            patch.dict(os.environ, {"XDG_STATE_HOME": xdg_state_home}),
        ):
            self.assertEqual(
                platform_paths.default_state_dir(),
                Path(xdg_state_home) / "research-agent",
            )

    def test_windows_sync_roots_are_unique(self):
        one_drive = str(Path("test-onedrive").resolve())

        with (
            patch.object(platform_paths, "platform_name", return_value="windows"),
            patch.dict(
                os.environ,
                {
                    "OneDrive": one_drive,
                    "OneDriveConsumer": one_drive,
                    "OneDriveCommercial": one_drive,
                },
            ),
        ):
            self.assertEqual(
                platform_paths.known_sync_roots(),
                [Path(one_drive)],
            )

    def test_linux_has_no_known_sync_roots(self):
        with patch.object(
            platform_paths,
            "platform_name",
            return_value="linux",
        ):
            self.assertEqual(platform_paths.known_sync_roots(), [])


if __name__ == "__main__":
    unittest.main()
