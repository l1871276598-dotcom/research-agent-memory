from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone


def _stamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def recover_processing(inbox):
    """Requeue interrupted projections.

    SessionProjector is idempotent by bridge_event_id, so replaying an event
    after a process crash cannot append the same message twice.
    """
    path = getattr(inbox, "path", None)
    if path is None:
        raise ValueError("bridge inbox is invalid")
    with closing(sqlite3.connect(path)) as connection:
        cursor = connection.execute(
            """
            UPDATE bridge_events SET status='queued',updated_at=?,
                error='recovered interrupted projection'
            WHERE status='processing'
            """,
            (_stamp(),),
        )
        connection.commit()
        return cursor.rowcount
