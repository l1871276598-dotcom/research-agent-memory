import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from extensions.loader import PluginLoader


def write_plugin(root, name, source, manifest=None):
    directory = Path(root) / name
    directory.mkdir()
    (directory / "plugin.json").write_text(
        json.dumps(
            manifest
            or {
                "name": name,
                "provides_tools": ["example_tool"],
                "provides_hooks": ["on_session_end"],
            }
        ),
        encoding="utf-8",
    )
    (directory / "__init__.py").write_text(source, encoding="utf-8")


class ExtensionTests(unittest.TestCase):
    def test_plugins_are_opt_in_and_register_tools_and_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            write_plugin(
                directory,
                "example",
                "def register(ctx):\n"
                "    ctx.register_tool('example_tool', lambda value=1: value)\n"
                "    ctx.register_hook('on_session_end', lambda **kw: kw.get('session_id'))\n",
            )
            disabled = PluginLoader([directory])
            self.assertEqual(disabled.load(), {})

            enabled = PluginLoader([directory], enabled={"example"})
            loaded = enabled.load()
            self.assertIn("example", loaded)
            self.assertEqual(enabled.tools["example_tool"](value=4), 4)
            self.assertEqual(enabled.invoke("on_session_end", session_id="one"), ["one"])

    def test_tool_override_requires_explicit_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            write_plugin(
                directory,
                "example",
                "def register(ctx):\n"
                "    ctx.register_tool('example_tool', lambda: 'plugin')\n",
                {
                    "name": "example",
                    "provides_tools": ["example_tool"],
                    "provides_hooks": [],
                },
            )
            denied = PluginLoader(
                [directory],
                enabled={"example"},
                tools={"example_tool": lambda: "core"},
            )
            denied.load()
            self.assertIn("override denied", denied.errors["example"])
            self.assertEqual(denied.tools["example_tool"](), "core")

            allowed = PluginLoader(
                [directory],
                enabled={"example"},
                allow_overrides={"example"},
                tools={"example_tool": lambda: "core"},
            )
            allowed.load()
            self.assertEqual(allowed.tools["example_tool"](), "plugin")

    def test_invalid_manifest_and_hook_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            write_plugin(
                directory,
                "bad",
                "def register(ctx):\n    pass\n",
                {"name": "bad", "provides_hooks": ["unknown"], "provides_tools": []},
            )
            loader = PluginLoader([directory], enabled={"bad"})
            self.assertEqual(loader.load(), {})
            self.assertIn("invalid hooks", loader.errors["bad"])


if __name__ == "__main__":
    unittest.main()
