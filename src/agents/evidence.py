"""Evidence ingress agent — Bridge boundary artifact publication.

This agent is the Core-side counterpart of the Developer Bridge
`evidence.publish` task.  It converts an already-scoped, already-canonicalized
request into one immutable evidence artifact through
`review.source_refs.publish_source_artifact_v2` (schema_version 2).

It is deliberately an ingress boundary, not an authority layer:

- it never creates a memory candidate;
- it never reviews;
- it never activates;
- it cannot change active memory;
- it cannot promote a project.

The Bridge is the owner of the trusted scope (workspace/project/
confidentiality come from the Bridge profile, not from the caller).  This agent
therefore treats the `workspace` / `project` / `confidentiality` fields inside
`task.input` as Bridge-injected and only re-validates their lexical shape; it
does not re-derive scope from an untrusted caller.

All digests (source_sha256 / payload_sha256 / artifact_sha256) are recomputed
in Core. A caller-supplied digest is never trusted — Core proves the caller's
bytes by hashing what it actually sees (C-INV-15).
"""

from .base import BaseAgent
from .orchestrator import _input, _optional_text, _text
from review.source_refs import publish_source_artifact_v2

_VALID_SCHEMES = frozenset({"vault-note"})
_VALID_KINDS = frozenset({"vault_note_snapshot"})
_WORKSPACE_CHOICES = frozenset({"personal", "work"})
_CONFIDENTIALITY_LEVELS = frozenset({"public", "personal", "internal", "restricted"})


def _sha256_text(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a hex sha256 string")
    value = value.strip()
    if len(value) != 64:
        raise ValueError(f"{name} must be a 64-char hex sha256 string")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{name} must be a hex sha256 string") from exc
    return value.lower()


class EvidenceAgent(BaseAgent):
    agent_id = "evidence_agent"
    handles = ["evidence.publish"]

    def __init__(self, state_dir):
        if state_dir is None:
            raise ValueError("evidence agent requires a state directory")
        self.state_dir = state_dir

    def run(self, task, context):
        values = _input(
            self,
            task,
            {
                "schema_version",
                "kind",
                "source",
                "locator",
                "payload",
                "payload_sha256",
                "workspace",
                "project",
                "confidentiality",
            },
        )
        schema_version = values.get("schema_version")
        if schema_version != 2:
            raise ValueError("input.schema_version must be 2")
        kind = _text(values.get("kind"), "input.kind")
        if kind not in _VALID_KINDS:
            raise ValueError("input.kind must be vault_note_snapshot")

        source = values.get("source")
        if not isinstance(source, dict) or set(source) != {"scheme", "note_id", "source_sha256"}:
            raise ValueError("input.source is invalid")
        scheme = source["scheme"]
        if not isinstance(scheme, str) or scheme not in _VALID_SCHEMES:
            raise ValueError("input.source.scheme must be vault-note")
        note_id = source["note_id"]
        if not isinstance(note_id, str) or not note_id.strip():
            raise ValueError("input.source.note_id must be a non-empty string")
        if "\x00" in note_id or "/" in note_id or "\\" in note_id:
            raise ValueError("input.source.note_id must not contain path separators")

        locator = values.get("locator")
        if not isinstance(locator, dict) or set(locator) != {"relative_path"}:
            raise ValueError("input.locator is invalid")
        relative_path = locator["relative_path"]
        if not isinstance(relative_path, str) or not relative_path.strip() or relative_path.startswith("/"):
            raise ValueError("input.locator.relative_path must be a relative path")

        payload = values.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("input.payload must be an object")

        # The payload_sha256 is validated lexically but never trusted: Core
        # recomputes it from the payload it actually sees.
        _sha256_text(values.get("payload_sha256"), "input.payload_sha256")

        workspace = _text(values.get("workspace"), "workspace")
        if workspace not in _WORKSPACE_CHOICES:
            raise ValueError("workspace must be personal or work")
        confidentiality = _text(values.get("confidentiality"), "confidentiality")
        if confidentiality not in _CONFIDENTIALITY_LEVELS:
            raise ValueError("confidentiality is invalid")
        project = _optional_text(values.get("project"), "project")

        result = publish_source_artifact_v2(
            self.state_dir,
            kind=kind,
            source={
                "scheme": scheme,
                "note_id": note_id,
                "source_sha256": source["source_sha256"],
            },
            locator={"relative_path": relative_path},
            workspace=workspace,
            project=project,
            confidentiality=confidentiality,
            payload=payload,
        )

        return self.result(
            task,
            {
                "source_ref": result["source_ref"],
                "canonical_identity": result["canonical_identity"],
                "source_sha256": result["source_sha256"],
                "payload_sha256": result["payload_sha256"],
                "artifact_sha256": result["artifact_sha256"],
                "workspace": workspace,
                "project": project,
                "confidentiality": confidentiality,
            },
            candidates=[],
        )


__all__ = ["EvidenceAgent"]
