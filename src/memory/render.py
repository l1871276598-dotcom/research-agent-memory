BEGIN_MARKER = "BEGIN_LAOS_MEMORY_DATA"
END_MARKER = "END_LAOS_MEMORY_DATA"


def render_recall(text):
    value = str(text).replace(BEGIN_MARKER, "[memory-marker]").replace(
        END_MARKER, "[memory-marker]"
    )
    if not value:
        return ""
    return BEGIN_MARKER + "\n" + value + "\n" + END_MARKER
