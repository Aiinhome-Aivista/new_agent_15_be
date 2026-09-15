from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

def get_ist_now():
    """
    Returns current naive datetime in IST (UTC + 5:30) for database default values.
    """
    return datetime.now(IST).replace(tzinfo=None)

def format_ist_iso(dt):
    """
    Formats a datetime object into an ISO 8601 string with +05:30 IST timezone offset.
    """
    if not dt:
        return None
    return dt.strftime('%Y-%m-%dT%H:%M:%S+05:30')
