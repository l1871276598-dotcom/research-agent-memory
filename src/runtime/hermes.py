from __future__ import annotations

import importlib
import sys
from contextlib import contextmanager
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_VENDOR_ROOT = _REPO_ROOT / "vendor" / "hermes_agent"


def activate_vendor() -> Path:
    """Expose the unmodified Hermes source snapshot as an internal runtime."""
    if not (_VENDOR_ROOT / "run_agent.py").is_file():
        raise RuntimeError(
            "Hermes source snapshot is missing; run "
            "`python3 tools/sync_hermes_vendor.py sync` first."
        )
    value = str(_VENDOR_ROOT)
    if value not in sys.path:
        sys.path.insert(0, value)
    return _VENDOR_ROOT


@contextmanager
def hermes_home(path):
    """Scope vendored Hermes state to a LAOS-controlled directory."""
    activate_vendor()
    constants = importlib.import_module("hermes_constants")
    token = constants.set_hermes_home_override(Path(path))
    try:
        yield Path(path)
    finally:
        constants.reset_hermes_home_override(token)


def load_agent_class():
    """Return Hermes' upstream AIAgent without copying or rewriting its loop."""
    activate_vendor()
    return importlib.import_module("run_agent").AIAgent
