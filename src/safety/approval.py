import contextvars
import re
from dataclasses import dataclass


_SESSION = contextvars.ContextVar("laos_approval_session", default="default")


@dataclass(frozen=True)
class CommandDecision:
    action: str
    reason: str
    pattern: str = ""


_NEVER = (
    (re.compile(r"\brm\s+-[^\n]*r[^\n]*f[^\n]*\s+/(?:\s|$)"), "filesystem root deletion"),
    (re.compile(r"\bmkfs(?:\.[a-z0-9]+)?\b"), "filesystem formatting"),
    (re.compile(r"\bdd\b[^\n]*\bof=/dev/(?:sd|nvme|disk)"), "raw disk overwrite"),
    (re.compile(r"\b(?:shutdown|reboot|halt|poweroff)\b"), "system power control"),
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:"), "process fork bomb"),
)

_APPROVAL = (
    (re.compile(r"(?:^|[;&|]\s*)sudo\b"), "privileged command"),
    (re.compile(r"\bgit\s+push\b[^\n]*(?:--force|-f)\b"), "forced remote update"),
    (re.compile(r"\b(?:chmod|chown)\b[^\n]*\s-[^\n]*R"), "recursive permission change"),
    (re.compile(r"(?:>|>>|\btee\b)[^\n]*(?:\.env\b|/etc/|/\.ssh/)"), "sensitive file write"),
    (re.compile(r"\brm\b[^\n]*\s-[^\n]*r"), "recursive deletion"),
)


class ApprovalGate:
    def __init__(self):
        self.session_allow = {}
        self.once_allow = set()

    def bind_session(self, session_id):
        return _SESSION.set(str(session_id or "default"))

    def reset_session(self, token):
        _SESSION.reset(token)

    @staticmethod
    def classify(command):
        text = str(command).strip()
        if not text:
            return CommandDecision("deny", "empty command")
        normalized = text.casefold()
        for pattern, reason in _NEVER:
            if pattern.search(normalized):
                return CommandDecision("deny", reason, pattern.pattern)
        for pattern, reason in _APPROVAL:
            if pattern.search(normalized):
                return CommandDecision("review", reason, pattern.pattern)
        return CommandDecision("allow", "no protected pattern")

    def decide(self, command):
        decision = self.classify(command)
        if decision.action != "review":
            return decision
        session = _SESSION.get()
        key = (session, str(command))
        if key in self.once_allow:
            self.once_allow.remove(key)
            return CommandDecision("allow", "approved once", decision.pattern)
        if str(command) in self.session_allow.get(session, set()):
            return CommandDecision("allow", "approved for session", decision.pattern)
        return decision

    def approve(self, command, scope="once"):
        if self.classify(command).action != "review":
            raise ValueError("command does not require approval")
        session = _SESSION.get()
        if scope == "once":
            self.once_allow.add((session, str(command)))
        elif scope == "session":
            self.session_allow.setdefault(session, set()).add(str(command))
        else:
            raise ValueError("approval scope must be once or session")
