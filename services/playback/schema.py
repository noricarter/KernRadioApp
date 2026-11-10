"""Playback-owned schema; catalog records remain in their existing tables."""
SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS playback_events (
        id INTEGER PRIMARY KEY,
        song_id INTEGER NOT NULL REFERENCES songs(id),
        started_at TEXT NOT NULL,
        ended_at TEXT,
        outcome TEXT CHECK(outcome IN ('completed', 'interrupted')),
        CHECK(ended_at IS NULL OR ended_at >= started_at),
        CHECK(outcome IS NOT NULL OR ended_at IS NULL)
    )''',
    'CREATE INDEX IF NOT EXISTS playback_song_start ON playback_events(song_id, started_at, id)',
    'CREATE INDEX IF NOT EXISTS playback_start ON playback_events(started_at, id)',
)
