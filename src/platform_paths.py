import os
import sys
from pathlib import Path


def platform_name():
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def default_state_dir():
    platform = platform_name()

    if platform == "windows":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "ResearchAgent"
        return Path.home() / "AppData" / "Local" / "ResearchAgent"

    if platform == "macos":
        return Path.home() / "Library" / "Application Support" / "ResearchAgent"

    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home) / "research-agent"
    return Path.home() / ".local" / "state" / "research-agent"


def known_sync_roots():
    platform = platform_name()
    roots = []

    if platform == "macos":
        roots.append(
            Path.home()
            / "Library"
            / "Mobile Documents"
            / "com~apple~CloudDocs"
        )

    if platform == "windows":
        for variable in (
            "OneDrive",
            "OneDriveConsumer",
            "OneDriveCommercial",
        ):
            value = os.environ.get(variable)
            if value:
                roots.append(Path(value))

    unique = []
    seen = set()

    for root in roots:
        key = str(root.expanduser()).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(root.expanduser())

    return unique
