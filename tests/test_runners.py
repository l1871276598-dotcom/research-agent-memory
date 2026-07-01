import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from runners.backends import (
    BackendRegistry,
    CallableBackend,
    DockerBackend,
    LocalBackend,
    SSHBackend,
    SingularityBackend,
)


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
    def test_wrappers_build_explicit_commands(self, run):
        DockerBackend("worker").run(["python", "-V"])
        self.assertEqual(run.call_args.args[0], ["docker", "exec", "worker", "python", "-V"])

        SSHBackend("host", user="user", port=22).run(["pwd"])
        self.assertEqual(run.call_args.args[0], ["ssh", "-p", "22", "user@host", "pwd"])

        SingularityBackend("worker.sif").run(["python", "-V"])
        self.assertEqual(
            run.call_args.args[0],
            ["singularity", "exec", "worker.sif", "python", "-V"],
        )

    @mock.patch("runners.backends.subprocess.run", return_value=Completed())
    def test_sensitive_inner_commands_are_not_started(self, run):
        backends = [
            LocalBackend(),
            DockerBackend("worker"),
            SSHBackend("host"),
            SingularityBackend("worker.sif"),
        ]
        for backend in backends:
            with self.subTest(backend=backend.name):
                result = backend.run(["sudo", "apt", "update"])
                self.assertEqual(result["approval"], "review")
                self.assertEqual(result["command"], ["sudo", "apt", "update"])
        run.assert_not_called()

    def test_registry_routes_named_and_optional_cloud_backends(self):
        registry = BackendRegistry()
        backend = mock.Mock(name="backend")
        backend.name = "custom"
        backend.run.return_value = {"ok": True}
        registry.register(backend)
        self.assertEqual(registry.run("custom", ["x"]), {"ok": True})
        with self.assertRaises(ValueError):
            registry.register(backend)

        cloud = CallableBackend(
            "cloud",
            lambda command, **kwargs: {"command": command, "region": kwargs["region"]},
        )
        registry.register(cloud)
        self.assertEqual(
            registry.run("cloud", "python -V", region="us-west"),
            {"command": ["python", "-V"], "region": "us-west"},
        )


if __name__ == "__main__":
    unittest.main()
