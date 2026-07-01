from types import MappingProxyType


STATES = frozenset({"raw", "candidate", "active", "deprecated", "conflicted", "archived"})
TRANSITIONS = MappingProxyType(
    {
        "raw": frozenset({"candidate"}),
        "candidate": frozenset({"active", "archived", "conflicted"}),
        "active": frozenset({"deprecated", "conflicted", "archived"}),
    }
)


def validate_state(state):
    if not isinstance(state, str) or state not in STATES:
        raise ValueError("invalid memory state")
    return state


def can_transition(source, target):
    validate_state(source)
    validate_state(target)
    return target in TRANSITIONS.get(source, frozenset())


def validate_transition(source, target):
    if not can_transition(source, target):
        raise ValueError("invalid memory state transition")
    return True
