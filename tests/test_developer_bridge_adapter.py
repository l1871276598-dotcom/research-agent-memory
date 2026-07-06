import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
ADAPTER = ROOT / "tools" / "developer_bridge_adapter.py"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from memory import init_store
from sessions import SessionStore


SPEC = importlib.util.spec_from_file_location("developer_bridge_adapter", ADAPTER)
ADAPTER_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTER_MODULE)


class DeveloperBridgeAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = Path(tempfile.mkdtemp(prefix="laos-adapter-data-")).resolve()
        cls.state_dir = Path(
            tempfile.mkdtemp(prefix=".laos-adapter-state-", dir=Path.home())
        ).resolve()
        init_store(cls.data_dir)
        cls.env = {
            "LAOS_DATA_ROOT": str(cls.data_dir),
            "LAOS_STATE_DIR": str(cls.state_dir),
            "LAOS_CHECKPOINT_WORKSPACE": "personal",
            "LAOS_CHECKPOINT_PROJECT": "adapter-test",
            "LAOS_CHECKPOINT_ACCOUNT_ID": "developer-bridge-test",
            "LAOS_CHECKPOINT_PROFILE": "personal",
            "LAOS_CHECKPOINT_CONFIDENTIALITY": "personal",
        }

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)
        shutil.rmtree(cls.state_dir, ignore_errors=True)

    def invoke(self, request, *, env=None, raw=None, args=(), cwd=None):
        payload = raw if raw is not None else json.dumps(request).encode("utf-8")
        startup = os.environ.copy()
        for name in self.env:
            startup.pop(name, None)
        startup.update(self.env if env is None else env)
        return subprocess.run(
            [sys.executable, str(ADAPTER), *args],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=startup,
            cwd=cwd,
            check=False,
        )

    def assert_failure(self, completed, code):
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, b"")
        self.assertEqual(
            completed.stdout,
            f'{{"ok":false,"error":{{"code":"{code}"}}}}\n'.encode("utf-8"),
        )

    def capture(self, **overrides):
        arguments = {
            "session_alias": "developer-bridge-session",
            "user_message": "Preserve this exact user text.",
            "assistant_response": "Preserve this exact assistant text.",
            "checkpoint_id": "developer-bridge-checkpoint",
        }
        arguments.update(overrides)
        return self.invoke({"operation": "capture_checkpoint", "arguments": arguments})

    def test_capture_receipt_retry_and_fresh_process_search_get(self):
        first = self.capture()
        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stderr, b"")
        self.assertEqual(first.stdout.count(b"\n"), 1)
        response = json.loads(first.stdout)
        self.assertTrue(response["ok"])
        result = response["result"]
        receipt = result["content_receipt"]
        self.assertEqual(receipt["user_message_length"], 30)
        self.assertEqual(receipt["assistant_response_length"], 35)
        self.assertEqual(
            receipt["user_message_sha256"],
            hashlib.sha256(b"Preserve this exact user text.").hexdigest(),
        )
        self.assertEqual(
            receipt["assistant_response_sha256"],
            hashlib.sha256(b"Preserve this exact assistant text.").hexdigest(),
        )

        retry = self.capture()
        retry_result = json.loads(retry.stdout)["result"]
        self.assertEqual(retry_result["session_id"], result["session_id"])
        self.assertEqual(
            [item["status"] for item in retry_result["receipts"]],
            ["duplicate", "duplicate"],
        )

        search = self.invoke(
            {"operation": "session_search", "arguments": {"query": "exact assistant"}}
        )
        self.assertEqual(search.returncode, 0)
        matches = json.loads(search.stdout)["result"]
        self.assertEqual({item["session_id"] for item in matches}, {result["session_id"]})

        restored = self.invoke(
            {"operation": "session_get", "arguments": {"session_id": result["session_id"]}}
        )
        self.assertEqual(restored.returncode, 0)
        session = json.loads(restored.stdout)["result"]
        self.assertEqual(session["workspace"], "personal")
        self.assertEqual(session["project"], "adapter-test")
        self.assertEqual(
            [item["content"] for item in session["messages"]],
            ["Preserve this exact user text.", "Preserve this exact assistant text."],
        )

    def test_request_scope_and_checkpoint_validation(self):
        for arguments in (
            {"query": "exact", "workspace": "work"},
            {"query": "exact", "project": "other"},
        ):
            with self.subTest(arguments=arguments):
                self.assert_failure(
                    self.invoke({"operation": "session_search", "arguments": arguments}),
                    "scope_violation",
                )

        self.assert_failure(
            self.capture(assistant_response_complete=False),
            "invalid_request",
        )
        self.assert_failure(
            self.capture(database_path="/tmp/sessions.sqlite"),
            "invalid_request",
        )

    def test_rejects_unknown_path_command_and_environment_injection(self):
        requests = [
            {"operation": "session_get", "arguments": {"session_id": "x", "path": "/etc/passwd"}},
            {"operation": "session_search", "arguments": {"query": "x", "command": "id"}},
            {"operation": "capture_checkpoint", "arguments": {"env": {"HOME": "/tmp"}}},
            {"operation": "session_search", "arguments": {"query": "x"}, "extra": True},
            {"operation": "unknown", "arguments": {}},
        ]
        for request in requests:
            with self.subTest(request=request):
                self.assert_failure(self.invoke(request), "invalid_request")

    def test_rejects_malformed_oversized_and_cli_input(self):
        for raw in (b"[]", b"{} trailing", b"\xff"):
            with self.subTest(raw=raw):
                self.assert_failure(self.invoke(None, raw=raw), "invalid_request")
        self.assert_failure(self.invoke(None, raw=b" " * (512 * 1024 + 1)), "invalid_request")
        self.assert_failure(
            self.invoke(
                {"operation": "session_search", "arguments": {"query": "x"}},
                args=("--data-root", "/tmp"),
            ),
            "invalid_request",
        )

    def test_huge_json_integer_is_an_invalid_request(self):
        raw = (
            b'{"operation":"session_search","arguments":{"query":"x","limit":'
            + b"9" * 5000
            + b"}}"
        )
        self.assert_failure(self.invoke(None, raw=raw), "invalid_request")

    def test_downstream_prints_and_system_exit_are_contained(self):
        class TrackingSink(io.StringIO):
            was_closed = False

            def close(self):
                self.was_closed = True
                super().close()

        def noisy_exit():
            print("downstream stdout")
            print("downstream stderr", file=sys.stderr)
            raise SystemExit(2)

        output = io.BytesIO()
        stdout = io.TextIOWrapper(output, encoding="utf-8")
        stderr = io.StringIO()
        sink = TrackingSink()
        with mock.patch("builtins.open", return_value=sink) as opened, mock.patch.object(
            ADAPTER_MODULE, "run", noisy_exit
        ), mock.patch.object(sys, "stdout", stdout), mock.patch.object(sys, "stderr", stderr):
            status = ADAPTER_MODULE.main()
            stdout.flush()
        self.assertEqual(status, 1)
        self.assertEqual(output.getvalue(), b'{"ok":false,"error":{"code":"request_failed"}}\n')
        self.assertEqual(stderr.getvalue(), "")
        opened.assert_called_once_with(os.devnull, "w", encoding="utf-8")
        self.assertTrue(sink.was_closed)

    def test_rejects_wrong_operation_types_as_invalid_requests(self):
        for operation in ([], {}):
            with self.subTest(operation=operation):
                self.assert_failure(
                    self.invoke({"operation": operation, "arguments": {}}),
                    "invalid_request",
                )

    def test_success_is_exactly_one_compact_json_line(self):
        completed = self.invoke(
            {
                "operation": "session_search",
                "arguments": {"query": "noresulttoken987654321"},
            }
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, b'{"ok":true,"result":[]}\n')
        self.assertEqual(completed.stderr, b"")

    def test_option_values_that_look_like_flags_do_not_escape_fixed_argv(self):
        environment = {
            **self.env,
            "LAOS_CHECKPOINT_PROJECT": "--help",
            "LAOS_CHECKPOINT_ACCOUNT_ID": "--help",
            "LAOS_CHECKPOINT_PROFILE": "--help",
        }
        completed = self.invoke(
            {
                "operation": "capture_checkpoint",
                "arguments": {
                    "session_alias": "flag-like-values",
                    "user_message": "Question",
                    "assistant_response": "Answer",
                    "checkpoint_id": "flag-like-values-checkpoint",
                },
            },
            env=environment,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, b"")
        self.assertTrue(json.loads(completed.stdout)["ok"])

    def test_rejects_invalid_partial_overlap_symlink_and_temp_state_configuration(self):
        configurations = []
        configurations.append({key: value for key, value in self.env.items() if key != "LAOS_STATE_DIR"})
        configurations.append({**self.env, "LAOS_CHECKPOINT_WORKSPACE": "shared"})
        configurations.append({**self.env, "LAOS_CHECKPOINT_CONFIDENTIALITY": "secret"})
        configurations.append({**self.env, "LAOS_DATA_ROOT": str(ROOT)})
        configurations.append({**self.env, "LAOS_STATE_DIR": str(self.data_dir)})
        with tempfile.TemporaryDirectory() as temporary_state, tempfile.TemporaryDirectory(
            prefix=".laos-no-marker-", dir=Path.home()
        ) as no_marker, tempfile.TemporaryDirectory(
            prefix=".laos-sync-parent-", dir=Path.home()
        ) as sync_parent:
            configurations.append({**self.env, "LAOS_STATE_DIR": temporary_state})
            configurations.append({**self.env, "LAOS_DATA_ROOT": no_marker})
            sync_state = Path(sync_parent) / "CloudStorage" / "state"
            sync_state.mkdir(parents=True)
            configurations.append({**self.env, "LAOS_STATE_DIR": str(sync_state)})
            symlink = self.state_dir.parent / f"{self.state_dir.name}-link"
            symlink.symlink_to(self.state_dir, target_is_directory=True)
            self.addCleanup(symlink.unlink, missing_ok=True)
            configurations.append({**self.env, "LAOS_STATE_DIR": str(symlink)})
            for environment in configurations:
                with self.subTest(environment=set(environment)):
                    completed = self.invoke(
                        {"operation": "session_search", "arguments": {"query": "x"}},
                        env=environment,
                    )
                    self.assert_failure(completed, "invalid_configuration")
                    for value in environment.values():
                        self.assertNotIn(str(value).encode(), completed.stdout)

    def test_rejects_every_partial_required_configuration(self):
        for missing in (
            "LAOS_DATA_ROOT",
            "LAOS_STATE_DIR",
            "LAOS_CHECKPOINT_WORKSPACE",
        ):
            with self.subTest(missing=missing):
                environment = {key: value for key, value in self.env.items() if key != missing}
                self.assert_failure(
                    self.invoke(
                        {"operation": "session_search", "arguments": {"query": "x"}},
                        env=environment,
                    ),
                    "invalid_configuration",
                )

    def test_rejects_fixed_temp_roots_even_when_tmpdir_points_elsewhere(self):
        tested = 0
        with tempfile.TemporaryDirectory(
            prefix=".laos-tmpdir-override-", dir=Path.home()
        ) as override:
            roots = []
            for value in ("/tmp", "/var/tmp", "/usr/tmp"):
                path = Path(value)
                if path.is_dir():
                    resolved = path.resolve()
                    if resolved not in roots:
                        roots.append(resolved)
            for root in roots:
                try:
                    state = Path(tempfile.mkdtemp(prefix="laos-fixed-temp-", dir=root)).resolve()
                except OSError:
                    continue
                tested += 1
                try:
                    with self.subTest(root=root):
                        self.assert_failure(
                            self.invoke(
                                {
                                    "operation": "session_search",
                                    "arguments": {"query": "x"},
                                },
                                env={
                                    **self.env,
                                    "LAOS_STATE_DIR": str(state),
                                    "TMPDIR": str(Path(override).resolve()),
                                },
                            ),
                            "invalid_configuration",
                        )
                finally:
                    shutil.rmtree(state, ignore_errors=True)
        self.assertGreater(tested, 0)

    def test_rejects_relative_noncanonical_and_symlink_parent_directories(self):
        with tempfile.TemporaryDirectory(
            prefix=".laos-canonical-parent-", dir=Path.home()
        ) as parent:
            parent = Path(parent).resolve()
            real_parent = parent / "real"
            real_state = real_parent / "state"
            real_state.mkdir(parents=True)
            linked_parent = parent / "linked"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            cases = (
                (self.state_dir.name, self.state_dir.parent),
                (str(self.state_dir / ".." / self.state_dir.name), None),
                (str(linked_parent / "state"), None),
                (f"{self.state_dir.parent}/./{self.state_dir.name}", None),
                (f"{self.state_dir.parent}//{self.state_dir.name}", None),
            )
            for state, cwd in cases:
                with self.subTest(state=state):
                    self.assert_failure(
                        self.invoke(
                            {"operation": "session_search", "arguments": {"query": "x"}},
                            env={**self.env, "LAOS_STATE_DIR": state},
                            cwd=cwd,
                        ),
                        "invalid_configuration",
                    )

    def test_rejects_malformed_and_symlinked_data_markers(self):
        with tempfile.TemporaryDirectory() as malformed, tempfile.TemporaryDirectory() as linked:
            malformed = Path(malformed).resolve()
            linked = Path(linked).resolve()
            init_store(malformed)
            init_store(linked)
            (malformed / ".research-agent-root").write_text("{", encoding="utf-8")
            marker = linked / ".research-agent-root"
            marker.unlink()
            target = linked / "marker-target.json"
            target.write_text(
                json.dumps({"type": "research-agent-data-root", "format_version": 1}),
                encoding="utf-8",
            )
            marker.symlink_to(target)
            for data in (malformed, linked):
                with self.subTest(data=data):
                    self.assert_failure(
                        self.invoke(
                            {"operation": "session_search", "arguments": {"query": "x"}},
                            env={**self.env, "LAOS_DATA_ROOT": str(data)},
                        ),
                        "invalid_configuration",
                    )

    def test_oversized_integer_in_data_marker_is_invalid_configuration(self):
        with tempfile.TemporaryDirectory() as data:
            data = Path(data).resolve()
            init_store(data)
            marker = data / ".research-agent-root"
            marker.write_bytes(
                b'{"type":"research-agent-data-root","format_version":'
                + b"9" * 5000
                + b"}"
            )
            self.assert_failure(
                self.invoke(
                    {"operation": "session_search", "arguments": {"query": "x"}},
                    env={**self.env, "LAOS_DATA_ROOT": str(data)},
                ),
                "invalid_configuration",
            )

    def test_session_get_not_found_and_identifier_and_limit_validation(self):
        missing = self.invoke(
            {"operation": "session_get", "arguments": {"session_id": "missing-session"}}
        )
        self.assert_failure(missing, "session_not_found")
        for request in (
            {"operation": "session_get", "arguments": {"session_id": "../escape"}},
            {"operation": "session_search", "arguments": {"query": "x", "limit": True}},
            {"operation": "session_search", "arguments": {"query": "x", "limit": 51}},
        ):
            with self.subTest(request=request):
                self.assert_failure(self.invoke(request), "invalid_request")

    def test_session_get_rejects_a_stored_session_outside_fixed_scope(self):
        SessionStore(self.state_dir).create(
            session_id="foreign-scope-session",
            workspace="work",
            project="adapter-test",
        )
        completed = self.invoke(
            {
                "operation": "session_get",
                "arguments": {"session_id": "foreign-scope-session"},
            }
        )
        self.assert_failure(completed, "scope_violation")

    def test_session_get_rejects_a_stored_session_in_another_project(self):
        SessionStore(self.state_dir).create(
            session_id="foreign-project-session",
            workspace="personal",
            project="other-project",
        )
        completed = self.invoke(
            {
                "operation": "session_get",
                "arguments": {"session_id": "foreign-project-session"},
            }
        )
        self.assert_failure(completed, "scope_violation")

    def test_search_with_absent_project_excludes_other_projects_without_consuming_limit(self):
        store = SessionStore(self.state_dir)
        token = "absentprojecttoken987654321"
        store.create(session_id="no-project-target", workspace="personal", project=None)
        store.append("no-project-target", "user", token)
        for index in range(3):
            session_id = f"foreign-project-search-{index}"
            store.create(session_id=session_id, workspace="personal", project="other-project")
            store.append(session_id, "user", " ".join([token] * 10))
        environment = {
            key: value for key, value in self.env.items() if key != "LAOS_CHECKPOINT_PROJECT"
        }
        for limit in (1, 10):
            with self.subTest(limit=limit):
                completed = self.invoke(
                    {
                        "operation": "session_search",
                        "arguments": {"query": token, "limit": limit},
                    },
                    env=environment,
                )
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(completed.stderr, b"")
                self.assertEqual(
                    [item["session_id"] for item in json.loads(completed.stdout)["result"]],
                    ["no-project-target"],
                )

    def test_projectless_search_reads_scope_without_materializing_messages(self):
        class Sessions:
            calls = []

            def get(self, session_id, include_messages=True):
                self.calls.append((session_id, include_messages))
                return {"project": None}

        class Facade:
            sessions = Sessions()

            def session_search(self, query, workspace, project, limit):
                return [{"session_id": "metadata-only"}]

            def session_get(self, session_id):
                raise AssertionError("full session lookup must not be used")

        facade = Facade()
        result = ADAPTER_MODULE._dispatch(
            "session_search",
            {"query": "metadata", "limit": 1},
            {"workspace": "personal", "project": None},
            facade,
        )
        self.assertEqual(result, [{"session_id": "metadata-only"}])
        self.assertEqual(facade.sessions.calls, [("metadata-only", False)])

    def test_oversized_session_get_and_search_responses_fail_closed(self):
        store = SessionStore(self.state_dir)
        oversized = "x" * (1024 * 1024)
        store.create(
            session_id="oversized-get-session",
            workspace="personal",
            project="adapter-test",
        )
        store.append("oversized-get-session", "assistant", oversized)
        store.create(
            session_id="oversized-search-session",
            workspace="personal",
            project="adapter-test",
        )
        store.append(
            "oversized-search-session",
            "assistant",
            "oversizedsearchtoken987654321 " + oversized,
        )
        requests = (
            {
                "operation": "session_get",
                "arguments": {"session_id": "oversized-get-session"},
            },
            {
                "operation": "session_search",
                "arguments": {"query": "oversizedsearchtoken987654321"},
            },
        )
        for request in requests:
            with self.subTest(operation=request["operation"]):
                self.assert_failure(self.invoke(request), "request_failed")

    def test_non_finite_session_metadata_cannot_escape_as_json(self):
        SessionStore(self.state_dir).create(
            session_id="non-finite-metadata-session",
            workspace="personal",
            project="adapter-test",
            metadata={"nan": float("nan"), "infinity": float("inf")},
        )
        self.assert_failure(
            self.invoke(
                {
                    "operation": "session_get",
                    "arguments": {"session_id": "non-finite-metadata-session"},
                }
            ),
            "request_failed",
        )


if __name__ == "__main__":
    unittest.main()
