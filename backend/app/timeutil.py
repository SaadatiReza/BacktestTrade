from datetime import datetime, timezone


def to_naive_utc(v: datetime) -> datetime:
    """Candle timestamps from the data provider and the DB are stored naive
    (implicitly UTC). Client-supplied timestamps often carry a timezone
    (e.g. JS `.toISOString()`), so normalize at every API boundary --
    otherwise comparing an aware datetime against a naive DB datetime raises
    TypeError deep inside the data provider.
    """
    if v.tzinfo is not None:
        v = v.astimezone(timezone.utc).replace(tzinfo=None)
    return v
