"""Phase 1 vault scanner — frontmatter parser and wikilink extractor.

Contract: _system/contracts/02-note-identity-and-frontmatter.md
         _system/contracts/03-scan-index-and-conflict-rules.md

Uses manual line-by-line YAML parsing (no PyYAML dependency),
consistent with the existing memory.py parse_document_frontmatter.
"""

import json
import re


WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]+)?\]\]")

# Fenced code block delimiter: up to 3 leading spaces, then >=3 backticks or tildes.
_FENCE_LINE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


class QuarantineError(Exception):
    """Raised when a note cannot be parsed and must be quarantined."""


# Schema version supported by this parser
SUPPORTED_SCHEMA_VERSION = 1

# Fields that human-authored notes must NOT contain (LAOS DB only)
FORBIDDEN_FIELDS = {
    "verification_state",
    "verified",
    "review_passed",
    "quarantined",
    "trusted",
}

# Fields required in all human-authored notes (per contract 02)
REQUIRED_FIELDS = {
    "schema_version",
    "type",
    "lifecycle",
    "created",
    "updated",
}


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Extract frontmatter dict and body from Markdown content.

    Returns (frontmatter_dict, body_text).
    Raises QuarantineError if YAML is structurally invalid.
    """
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content

    try:
        end = lines.index("---", 1)
    except ValueError:
        raise QuarantineError("Unclosed frontmatter: missing closing ---")

    fm = {}
    for line in lines[1:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, raw = stripped.partition(":")
        key = key.strip()
        raw = raw.strip()

        if raw.startswith("[") and raw.endswith("]"):
            # Inline list: [a, b, c]
            inner = raw[1:-1]
            values = [v.strip().strip("'").strip('"') for v in inner.split(",") if v.strip()]
            fm[key] = values
        elif raw == "":
            # Multi-line list follows — scan indented lines
            items = []
            for next_line in lines[1:end]:
                nstripped = next_line.strip()
                if nstripped.startswith("- "):
                    items.append(nstripped[2:].strip().strip("'").strip('"'))
            if items:
                fm[key] = items
            else:
                fm[key] = raw
        else:
            # Try JSON parse for quoted strings, else plain
            try:
                fm[key] = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                fm[key] = raw

    body = "\n".join(lines[end + 1:])
    return fm, body


def check_forbidden_fields(fm: dict) -> list[str]:
    """Return a list of forbidden fields present in frontmatter."""
    return [f for f in FORBIDDEN_FIELDS if f in fm]


def check_schema_version(fm: dict) -> str | None:
    """Return a quarantine reason if schema_version exceeds supported version, else None."""
    sv = fm.get("schema_version")
    if sv is not None:
        try:
            sv_int = int(sv)
        except (ValueError, TypeError):
            return f"schema_version '{sv}' is not a valid integer"
        if sv_int > SUPPORTED_SCHEMA_VERSION:
            return f"schema_version {sv_int} exceeds supported version {SUPPORTED_SCHEMA_VERSION}"
    return None


def check_required_fields(fm: dict) -> list[str]:
    """Return a list of required fields that are missing from frontmatter."""
    return [f for f in REQUIRED_FIELDS if f not in fm]


def validate_frontmatter(fm: dict) -> str | None:
    """Validate frontmatter against schema rules.

    Returns a quarantine reason string if invalid, or None if valid.
    """
    # Check forbidden fields
    forbidden = check_forbidden_fields(fm)
    if forbidden:
        return f"Forbidden frontmatter fields present: {', '.join(forbidden)}"

    # Check schema version
    sv_reason = check_schema_version(fm)
    if sv_reason:
        return sv_reason

    # Check required fields
    missing = check_required_fields(fm)
    if missing:
        return f"Missing required frontmatter fields: {', '.join(missing)}"

    return None


def _strip_fenced_code(text: str) -> str:
    """Return text with fenced code blocks (``` or ~~~) removed.

    Wikilink syntax inside fenced code is illustrative, not a real note
    reference, so it must not be extracted.
    """
    kept = []
    fence = None  # active fence char (` or ~), or None when outside a block
    fence_len = 0
    for line in text.splitlines(keepends=True):
        match = _FENCE_LINE.match(line)
        if fence is not None:
            if match and match.group(1)[0] == fence and len(match.group(1)) >= fence_len:
                fence = None
                fence_len = 0
            continue
        if match:
            fence = match.group(1)[0]
            fence_len = len(match.group(1))
            continue
        kept.append(line)
    return "".join(kept)


def extract_wikilinks(text: str) -> list[str]:
    """Return deduplicated list of [[wikilink]] targets from text.

    Wikilinks inside fenced code blocks (``` / ~~~) are ignored.
    """
    return list(dict.fromkeys(WIKILINK.findall(_strip_fenced_code(text))))
