from datetime import datetime, timedelta, timezone


def classify(record, now=None, stale_after_days=30, archive_after_days=90):
    now = now or datetime.now(timezone.utc)
    created = datetime.fromisoformat(record["created_at"])
    activity = record.get("last_activity_at")
    anchor = datetime.fromisoformat(activity) if activity else created
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    if record.get("pinned"):
        return "active"
    if anchor <= now - timedelta(days=archive_after_days):
        return "archived"
    if anchor <= now - timedelta(days=stale_after_days):
        return "stale"
    return "active"
