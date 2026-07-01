import json

from safety.approval import ApprovalGate
from safety.loop import ToolLoopDetector


class ToolExecutor:
    def __init__(
        self,
        registry,
        *,
        allowed=None,
        approval=None,
        guardrail=None,
        extensions=None,
    ):
        self.registry = registry
        self.allowed = set(allowed) if allowed is not None else None
        self.approval = approval or ApprovalGate()
        self.guardrail = guardrail or ToolLoopDetector()
        self.extensions = extensions

    def execute(self, name, arguments=None):
        values = dict(arguments or {})
        if self.allowed is not None and name not in self.allowed:
            return {"success": False, "error": "tool is not allowed", "tool": name}
        spec = self.registry.get(name)

        if self.extensions is not None:
            results = self.extensions.invoke("pre_tool_call", name=name, arguments=values)
            for result in results:
                if isinstance(result, dict) and result.get("action") == "deny":
                    return {
                        "success": False,
                        "error": result.get("reason", "blocked by extension"),
                        "tool": name,
                    }
                if isinstance(result, dict) and result.get("action") == "rewrite":
                    replacement = result.get("arguments")
                    if isinstance(replacement, dict):
                        values = replacement

        command = values.get("command") if name in {"terminal", "shell", "execute_code"} else None
        if command is not None:
            decision = self.approval.decide(command)
            if decision.action != "allow":
                return {
                    "success": False,
                    "approval": decision.action,
                    "reason": decision.reason,
                    "tool": name,
                }

        try:
            result = self.registry.call(name, values)
            failed = False
            payload = {"success": True, "result": result, "tool": name}
        except Exception as exc:
            failed = True
            payload = {"success": False, "error": str(exc), "tool": name}

        loop = self.guardrail.record(name, values, failed=failed)
        payload["guardrail"] = loop
        if self.extensions is not None:
            self.extensions.invoke(
                "post_tool_call",
                name=name,
                arguments=values,
                result=payload,
            )
        json.dumps(payload, ensure_ascii=False, default=str)
        return payload
