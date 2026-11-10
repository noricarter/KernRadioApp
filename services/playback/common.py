"""Shared playback timestamp and query conventions."""
from datetime import datetime, timezone


def utc_timestamp(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError('Use an ISO 8601 timestamp with a timezone.') from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Playback timestamps must include a timezone.')
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def now_utc():
    return datetime.now(timezone.utc)


def date_range(start, end, *, column='e.started_at'):
    start = utc_timestamp(start) if start is not None else None
    end = utc_timestamp(end) if end is not None else None
    if start is not None and end is not None and start >= end:
        raise ValueError('The range end must be after its start.')
    clauses, parameters = [], []
    if start is not None:
        clauses.append(f'{column} >= ?')
        parameters.append(start)
    if end is not None:
        clauses.append(f'{column} < ?')
        parameters.append(end)
    return ''.join(' AND ' + clause for clause in clauses), parameters


def page(limit, offset):
    if type(limit) is not int or limit < 1 or type(offset) is not int or offset < 0:
        raise ValueError('Use a positive integer limit and a nonnegative integer offset.')


# Canonical UTC text always has six fractional digits. Integer arithmetic avoids
# Julian-day floating point errors without persisting redundant duration fields.
def _microseconds(column):
    return f"(CAST(strftime('%s', substr({column}, 1, 19) || 'Z') AS INTEGER) * 1000000 + CAST(substr({column}, 21, 6) AS INTEGER))"


DURATION = f"CASE WHEN e.ended_at IS NOT NULL THEN {_microseconds('e.ended_at')} - {_microseconds('e.started_at')} ELSE 0 END"
AGGREGATES = f'''
    COUNT(e.id) AS started_plays,
    COUNT(CASE WHEN e.outcome = 'completed' THEN 1 END) AS completed_plays,
    COUNT(CASE WHEN e.outcome = 'interrupted' THEN 1 END) AS interrupted_plays,
    COUNT(CASE WHEN e.id IS NOT NULL AND e.outcome IS NULL THEN 1 END) AS unfinished_plays,
    MAX(e.started_at) AS last_played_at,
    COALESCE(SUM({DURATION}), 0) / 1000000.0 AS airtime_seconds
'''
