from datetime import datetime, timezone, timedelta

def now_nairobi() -> datetime:
    """
    Get the current time in Nairobi (UTC+3).
    Returns a timezone-aware datetime object.
    """
    # Nairobi is UTC+3. Kenya does not observe Daylight Saving Time.
    return datetime.now(timezone(timedelta(hours=3)))
