"""Command runners selectively adapted from Hermes execution environments."""

from dataclasses import asdict, dataclass
import shlex
import subprocess

from safety.approval import ApprovalGate


@dataclass
class CommandResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    backend: str

    def to_dict(self):
        return asdict(self)


def _argv(command):
    if isinstance(command, str):
        return shlex.split(command)
    if isinstance(command, list) and all(isinstance(item, str) for item in command):
        return list(command)
    raise ValueError("command must be a string or list of strings")


class LocalBackend:
    name = "local"

    def __init__(self, approval=None):
        self.approval = approval or ApprovalGate()

    def _approval_result(self, argv):
        decision = self.approval.decide(shlex.join(argv))
        if decision.action == "allow":
            return None
        return {
            "backend": self.name,
            "approval": decision.action,
            "reason": decision.reason,
            "command": argv,
        }

    def _execute(self, argv, *, cwd=None, timeout=300, env=None):
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(
            argv,
            completed.returncode,
            completed.stdout,
            completed.stderr,
            self.name,
        ).to_dict()

    def run(self, command, *, cwd=None, timeout=300, env=None):
        argv = _argv(command)
        blocked = self._approval_result(argv)
        return blocked or self._execute(argv, cwd=cwd, timeout=timeout, env=env)


class DockerBackend(LocalBackend):
    name = "docker"

    def __init__(self, container, approval=None):
        super().__init__(approval)
        if not isinstance(container, str) or not container.strip():
            raise ValueError("container is required")
        self.container = container

    def run(self, command, *, cwd=None, timeout=300, env=None):
        inner = _argv(command)
        blocked = self._approval_result(inner)
        if blocked:
            return blocked
        return self._execute(
            ["docker", "exec", self.container, *inner],
            cwd=cwd,
            timeout=timeout,
            env=env,
        )


class SSHBackend(LocalBackend):
    name = "ssh"

    def __init__(self, host, *, user=None, port=None, approval=None):
        super().__init__(approval)
        if not isinstance(host, str) or not host.strip():
            raise ValueError("host is required")
        self.target = f"{user}@{host}" if user else host
        self.port = port

    def run(self, command, *, cwd=None, timeout=300, env=None):
        inner = _argv(command)
        blocked = self._approval_result(inner)
        if blocked:
            return blocked
        argv = ["ssh"]
        if self.port is not None:
            argv += ["-p", str(self.port)]
        argv += [self.target, shlex.join(inner)]
        return self._execute(argv, cwd=cwd, timeout=timeout, env=env)


class SingularityBackend(LocalBackend):
    name = "singularity"

    def __init__(self, image, approval=None):
        super().__init__(approval)
        if not isinstance(image, str) or not image.strip():
            raise ValueError("image is required")
        self.image = image

    def run(self, command, *, cwd=None, timeout=300, env=None):
        inner = _argv(command)
        blocked = self._approval_result(inner)
        if blocked:
            return blocked
        return self._execute(
            ["singularity", "exec", self.image, *inner],
            cwd=cwd,
            timeout=timeout,
            env=env,
        )


class CallableBackend:
    """Adapter for optional cloud runners supplied by extensions."""

    def __init__(self, name, handler):
        if not isinstance(name, str) or not name.strip() or not callable(handler):
            raise ValueError("invalid callable execution backend")
        self.name = name.strip()
        self.handler = handler

    def run(self, command, **kwargs):
        return self.handler(_argv(command), **kwargs)


class BackendRegistry:
    def __init__(self):
        self.backends = {}

    def register(self, backend, *, allow_override=False):
        name = getattr(backend, "name", None)
        if not isinstance(name, str) or not callable(getattr(backend, "run", None)):
            raise ValueError("invalid execution backend")
        if name in self.backends and not allow_override:
            raise ValueError("execution backend already registered")
        self.backends[name] = backend
        return backend

    def run(self, name, command, **kwargs):
        try:
            backend = self.backends[name]
        except KeyError as exc:
            raise ValueError("unknown execution backend") from exc
        return backend.run(command, **kwargs)
