from datetime import datetime, timedelta, timezone


VALID_STATES = {"active", "stale", "archived"}


def _time(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value:
        result = datetime.fromisoformat(value)
    else:
        raise ValueError("invalid timestamp")
    return result if result.tzinfo is not None else result.replace(tzinfo=timezone.utc)


def target_state(record, now=None, stale_days=30, archive_days=90):
    if stale_days <= 0 or archive_days <= stale_days:
        raise ValueError("invalid lifecycle thresholds")
    now = _time(now or datetime.now(timezone.utc))
    created = _time(record["created_at"])
    anchor = _time(record["last_activity_at"]) if record.get("last_activity_at") else created
    if record.get("pinned"):
        return "active"
    if anchor <= now - timedelta(days=archive_days):
        return "archived"
    if anchor <= now - timedelta(days=stale_days):
        return "stale"
    return "active"


def build_plan(records, now=None, stale_days=30, archive_days=90):
    now = _time(now or datetime.now(timezone.utc))
    changes = []
    for record in records:
        current = record.get("state", "active")
        if current not in VALID_STATES:
            raise ValueError("invalid procedure state")
        target = target_state(record, now, stale_days, archive_days)
        if target != current:
            changes.append({"name": record["name"], "from": current, "to": target})
    return {"dry_run": True, "checked": len(records), "changes": changes}
