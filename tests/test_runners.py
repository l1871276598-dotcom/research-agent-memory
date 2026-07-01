import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from runners.backends import BackendRegistry, DockerBackend, LocalBackend, SSHBackend


class Completed:
    returncode = 0
    stdout = "ok"
    stderr = ""


class RunnerTests(unittest.TestCase):
    @mock.patch("runners.backends.subprocess.run", return_value=Completed())
    def test_local_runner_uses_argv_without_shell(self, run):
        result = LocalBackend().run(["echo", "hello"])
        self.assertEqual(result["stdout"], "ok")
        self.assertEqual(result["backend"], "local")
        self.assertEqual(run.call_args.args[0], ["echo", "hello"])
        self.assertNotIn("shell", run.call_args.kwargs)

    @mock.patch("runners.backends.subprocess.run", return_value=Completed())
    def test_docker_and_ssh_build_explicit_commands(self, run):
        DockerBackend("worker").run(["python", "-V"])
        self.assertEqual(run.call_args.args[0], ["docker", "exec", "worker", "python", "-V"])

        SSHBackend("host", user="user", port=22).run(["pwd"])
        self.assertEqual(run.call_args.args[0], ["ssh", "-p", "22", "user@host", "pwd"])

    @mock.patch("runners.backends.subprocess.run", return_value=Completed())
    def test_sensitive_command_is_not_started_without_approval(self, run):
        result = LocalBackend().run(["sudo", "apt", "update"])
        self.assertEqual(result["approval"], "review")
        run.assert_not_called()

    def test_registry_routes_named_backends(self):
        registry = BackendRegistry()
        backend = mock.Mock(name="backend")
        backend.name = "custom"
        backend.run.return_value = {"ok": True}
        registry.register(backend)
        self.assertEqual(registry.run("custom", ["x"]), {"ok": True})
        with self.assertRaises(ValueError):
            registry.register(backend)


if __name__ == "__main__":
    unittest.main()
