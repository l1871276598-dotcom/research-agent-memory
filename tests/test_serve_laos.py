import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from memory import init_store

SPEC = importlib.util.spec_from_file_location(
    "serve_laos",
    ROOT / "tools" / "serve_laos.py",
)
SERVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVE)


class ServeLaosTests(unittest.TestCase):
    def test_stdio_has_no_http_server_options(self):
        args = SERVE._parser().parse_args(
            ["--data-root", "/data", "--state-dir", "/state"]
        )
        self.assertEqual(args.transport, "stdio")
        self.assertIsNone(SERVE._server_options(args))

    def test_streamable_http_is_loopback_stateless_json(self):
        args = SERVE._parser().parse_args(
            [
                "--data-root",
                "/data",
                "--state-dir",
                "/state",
                "--transport",
                "streamable-http",
                "--port",
                "8766",
                "--mcp-path",
                "/custom/",
            ]
        )
        self.assertEqual(args.mcp_path, "/custom")
        self.assertEqual(
            SERVE._server_options(args),
            {
                "host": "127.0.0.1",
                "port": 8766,
                "streamable_http_path": "/custom",
                "json_response": True,
                "stateless_http": True,
            },
        )

    def test_checkpoint_is_recoverable_from_a_fresh_facade(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            init_store(data)
            args = SERVE._parser().parse_args(
                [
                    "--data-root",
                    data,
                    "--state-dir",
                    state,
                    "--enable-checkpoint-capture",
                    "--checkpoint-workspace",
                    "personal",
                    "--checkpoint-project",
                    "laos",
                ]
            )
            first = SERVE.build_facade(args)
            checkpoint = first.capture_checkpoint(
                session_alias="private-assistant-continuity",
                user_message="Save this checkpoint so a new assistant session can continue.",
                assistant_response="The next step is to restore this checkpoint in a fresh session.",
                checkpoint_id="private-assistant-continuity-1",
            )

            second = SERVE.build_facade(args)
            matches = second.session_search(
                "checkpoint",
                workspace="personal",
                project="laos",
                limit=10,
            )
            restored = second.session_get(checkpoint["session_id"])

            self.assertTrue(matches)
            self.assertEqual(
                {match["session_id"] for match in matches},
                {checkpoint["session_id"]},
            )
            self.assertEqual(restored["workspace"], "personal")
            self.assertEqual(restored["project"], "laos")
            self.assertEqual(
                [message["content"] for message in restored["messages"]],
                [
                    "Save this checkpoint so a new assistant session can continue.",
                    "The next step is to restore this checkpoint in a fresh session.",
                ],
            )
            self.assertEqual(
                second.session_search(
                    "checkpoint",
                    workspace="work",
                    project="laos",
                    limit=10,
                ),
                [],
            )

    def test_public_bind_is_not_available(self):
        with self.assertRaises(SystemExit):
            SERVE._parser().parse_args(
                [
                    "--data-root",
                    "/data",
                    "--state-dir",
                    "/state",
                    "--transport",
                    "streamable-http",
                    "--host",
                    "0.0.0.0",
                ]
            )

    def test_invalid_port_is_rejected(self):
        args = SERVE._parser().parse_args(
            [
                "--data-root",
                "/data",
                "--state-dir",
                "/state",
                "--transport",
                "streamable-http",
                "--port",
                "70000",
            ]
        )
        with self.assertRaisesRegex(ValueError, "between 1 and 65535"):
            SERVE._server_options(args)


if __name__ == "__main__":
    unittest.main()
