import importlib.util as _importlib_util
import sys as _sys
from pathlib import Path as _Path


_LEGACY_NAME = "_laos_legacy_memory"


def _load_legacy():
    module = _sys.modules.get(_LEGACY_NAME)
    if module is not None:
        return module
    path = _Path(__file__).resolve().parent.parent / "memory.py"
    spec = _importlib_util.spec_from_file_location(_LEGACY_NAME, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load legacy memory module")
    module = _importlib_util.module_from_spec(spec)
    _sys.modules[_LEGACY_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        _sys.modules.pop(_LEGACY_NAME, None)
        raise
    return module


_legacy = _load_legacy()
for _name, _value in vars(_legacy).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

__all__ = sorted(name for name in vars(_legacy) if not name.startswith("__"))
