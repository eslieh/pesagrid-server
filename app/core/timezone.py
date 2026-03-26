from datetime import datetime, timezone, timedelta

# Nairobi is UTC+3. Kenya does not observe Daylight Saving Time.
Nairobi = timezone(timedelta(hours=3))

def now_nairobi() -> datetime:
    """
    Get the current time in Nairobi (UTC+3).
    Returns a timezone-aware datetime object.
    """
    return datetime.now(Nairobi)

def make_aware(dt: datetime) -> datetime:
    """
    Ensure a datetime is timezone-aware. If naive, assume it's UTC (from DB)
    and convert it to Nairobi time.
    """
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).astimezone(Nairobi)
    return dt
