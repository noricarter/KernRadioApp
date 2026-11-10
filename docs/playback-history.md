# Playback history and metrics

This package provides data infrastructure only. The separate
[Suno playback service](suno-playback.md) now supplies actual playback lifecycle
notifications. The history/metrics package contains no player, scheduler,
rotation algorithm, programming rule, agent tool, or frontend integration.

## Composition

```python
from pathlib import Path
from services.database import DatabaseService
from services.catalog import MusicCatalogService
from services.playback import PlaybackHistoryService, PlaybackMetricsService

# All three services must use the same database file.
database = DatabaseService(Path('data/catalog.sqlite3'))
catalog = MusicCatalogService(database)
history = PlaybackHistoryService(database, catalog)
metrics = PlaybackMetricsService(database, catalog)
```

The existing database service manages connections and transactions. The existing
catalog validates song/artist identities. The separate `services.playback`
package owns event persistence and calculated metrics; generic database and
catalog behavior are unchanged. Constructors initialize the playback schema
idempotently. No application startup, ingestion, frontend, or agent changes
are necessary to use these public interfaces directly.

## Event API

All methods return detached dictionaries, using existing service conventions.

| Method | Result/behavior |
| --- | --- |
| `start_playback(suno_song_id, *, started_at=None)` | Append a pending event and return its ID, internal song ID, Suno ID, start, empty end, and empty outcome. Defaults to current UTC time. |
| `finish_playback(event_id, *, ended_at=None)` | Set the pending event's end and outcome `completed`. Defaults to current UTC time. |
| `interrupt_playback(event_id, *, ended_at=None)` | Set the pending event's end and outcome `interrupted`. Defaults to current UTC time. |
| `record_playback(suno_song_id, *, started_at, ended_at=None, outcome='completed')` | Append a historical completed/interrupted event. An unavailable end stays unknown. |
| `get_event(event_id)` | Event dictionary, or `None` if absent. |
| `list_events(*, suno_song_id=None, artist_id=None, start=None, end=None, outcome=None, limit=100, offset=0, order='oldest')` | Chronological history by start and then event ID; `order='newest'` returns recent starts first. Filters can be combined. Without an outcome filter, pending events are included. |

Timestamp arguments accept timezone-aware `datetime` values or ISO 8601 strings
with a timezone. They are normalized to UTC and stored as fixed-width text such
as `2026-11-01T10:00:00.000000Z`. Naive timestamps are rejected rather than
assuming local time. End-before-start and invalid outcomes are rejected.

Repeated plays always append distinct events, including identical start times.
Finalization updates only the event started for that playback. A repeated
notification with the same outcome and explicit end is idempotent; omitting
an end for an already-finalized event returns it unchanged. A different outcome
or explicit end is rejected, so recorded history cannot silently be rewritten.
A historical terminal event recorded without an end remains without one; it
contributes no assumed duration.

Pending events have a NULL outcome, not a third terminal outcome. No background
process infers that they completed or interrupts them after a timeout. Future
playback code is responsible for reporting what happened.

Missing songs/artists or events required by mutation raise the existing
`RecordNotFound`; invalid input raises `ValueError`; persistence failures use
`DatabaseError` / `DatabaseConstraintError` from the database service.

## Metrics API

| Method | Result |
| --- | --- |
| `song_statistics(suno_song_id, *, start=None, end=None, as_of=None)` | Completed/interrupted/pending/start counts, latest start, elapsed seconds since that start, and recorded airtime. |
| `artist_statistics(artist_id, *, start=None, end=None)` | The same counts, latest start, and recorded airtime across songs associated with the artist. |
| `song_play_counts(*, start=None, end=None, order='most_played', limit=100, offset=0)` | Per-song counts and airtime, including catalog songs with zero matching plays. Orders: `most_played`, `least_played`, `least_recent`, `catalog`. |
| `songs_never_played(*, limit=100, offset=0)` | Songs with no recorded starts at all. |
| `artist_airtime_distribution(*, start=None, end=None)` | Per-artist counts, airtime, and `airtime_share` as a fraction from 0 to 1. Unassociated songs have an `artist_id=None` bucket. |
| `representation(*, metadata='style', start=None, end=None)` | Library song counts, playback counts, and airtime per exact style string or submitted genre. |

An artist's chronological range history uses
`history.list_events(artist_id=artist_id, start=..., end=...)`.

Calculated aggregate fields:

- `started_plays`: all recorded events, including interruptions and pending
  events. This is the total number of playback attempts/starts.
- `completed_plays`: events with outcome `completed`. Frequency rankings and
  completed-play counts use this field consistently.
- `interrupted_plays`: events with outcome `interrupted`.
- `unfinished_plays`: pending events with no outcome yet.
- `last_played_at`: latest start of any event, including interrupted/pending.
- `airtime_seconds`: sum of known end-minus-start durations, including completed
  and interrupted plays. Unknown ends contribute zero; elapsed pending time is
  never estimated. Durations use integer microsecond arithmetic before conversion
  to seconds; no duration or summary fields are persisted.
- `seconds_since_last_play`: song-specific elapsed seconds from the latest start
  to `as_of` (defaults to current UTC time). `None` if never played. `as_of` only
  supplies the elapsed-time reference, not an additional history filter; a
  reference before the latest start produces a negative interval.

All date ranges are **inclusive start, exclusive end**, based on playback start
in UTC: `[start, end)`. Either boundary may be omitted; empty/reversed ranges
are rejected. An event starting before the range is excluded even if it ends
inside it. A selected event's full known duration is counted, even if it ends
after the range. These APIs count events by start, not clipped station occupancy.

Zero-play songs sort first in `least_recent`, followed by the earliest latest
start. Frequency ranks use completed plays, with internal song ID breaking ties.
A song with only interrupted starts has zero completed plays but is not
classified as never played.

Artist aggregates use the catalog's **current** song-to-artist relationships;
no artist name snapshots, normalization, or merging are introduced. Artist
statistics return zeros for an existing artist without songs or events. Airtime
distribution includes artists with catalog songs, even at zero airtime, and any
unassociated-song bucket; it omits artists without songs. With no known airtime,
all shares are zero.

Style representation treats each stored style string as one exact value; it
does not tokenize comma-separated styles or infer a taxonomy. Genre representation
uses distinct `(song, submitted genre)` pairs from Discord submissions so repeated
submissions do not multiply plays. A song with several distinct submitted genres
appears once in each; these buckets overlap and must not be summed as exclusive
categories. Missing metadata is retained as a NULL bucket. Representation changes
neither song metadata nor Discord attribution.

## Schema

Only one new table is created:

```sql
CREATE TABLE playback_events (
    id INTEGER PRIMARY KEY,
    song_id INTEGER NOT NULL REFERENCES songs(id),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    outcome TEXT CHECK(outcome IN ('completed', 'interrupted')),
    CHECK(ended_at IS NULL OR ended_at >= started_at),
    CHECK(outcome IS NOT NULL OR ended_at IS NULL)
);
```

Indexes cover `(song_id, started_at, id)` and `(started_at, id)`. Song deletion
cannot orphan historical events because foreign keys are enabled by the existing
database service. No existing catalog tables or records are rewritten, and no
calculated-statistics tables or columns are added.

## Future playback integration

The eventual playback service should notify the history service at these points:

```python
# Called by the future player when audio actually begins:
event = history.start_playback('existing-suno-song-id', started_at=actual_start)

# Called on normal completion:
history.finish_playback(event['id'], ended_at=actual_end)

# Alternatively, called when that playback is interrupted:
history.interrupt_playback(event['id'], ended_at=actual_stop)
```

Keep the returned event ID with that playback attempt. Report actual times,
not expected song duration or scheduled times. These are notification examples,
not a playback engine or simulated radio loop.

The separate `SunoPlaybackService` calls these interfaces on actual browser
playing, ended, stopped, and error events. The [live programming
orchestrator](radio-programming.md) now reuses that service; transitions and
graceful Stop follow actual playback completion without changing these metrics.

## Verification

```sh
uv run python -m unittest tests.test_playback -v
uv run python -m unittest discover -q
```

The 22 new tests use real, isolated temporary SQLite files, without mocked
persistence, networking, audio, or user data. They cover multiple/concurrent
plays of the same song, chronological order, completion/interruption/pending
states, idempotent notifications, UTC conversion, invalid timestamps, precise
airtime across midnight, unknown ends, artist aggregation and alias preservation,
range boundaries, zero-play rankings, airtime shares, exact style/genre buckets,
foreign-key/outcome constraints, pagination, and persistence after reopening.

Results: all **22 playback tests** passed, and the full repository suite passed
with **99 tests**. No live playback engine exists or was exercised.
