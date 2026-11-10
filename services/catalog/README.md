# SQLite and music catalog services

These services use only the Python standard library. They have no Flask,
Codex, Discord client, Suno client, or radio application imports. An application
opts in by composing the two services with its chosen path (the management
application now does this for ingestion):

```python
from pathlib import Path
from services.database import DatabaseService
from services.catalog import MusicCatalogService

catalog = MusicCatalogService(DatabaseService(Path('data/catalog.sqlite3')))
# Parent directories, the database, tables, and indexes are created automatically.

artist = catalog.create_artist(
    suno_artist_name='Creator name',
    discord_handle='community-member',
    aliases=['Creator name', 'Another name'],
    preferred_display_alias='On-air name',
)
song, created = catalog.get_or_create_song(
    'authoritative-suno-song-id',
    'https://suno.com/song/authoritative-suno-song-id',
    artist_id=artist['id'],
)
catalog.update_song_metadata(song['suno_song_id'], title='Song title', style='ambient')
```

## Generic database public interface

`DatabaseService(path)` takes a **file path**, not a SQLite URI or an in-memory
connection. Each operation opens and closes its own connection; no application
shutdown hook is necessary. Every connection enables foreign keys and has a
10-second busy timeout. It can be shared across threads because connections
are never shared.

- `initialize(statements)`: apply caller-owned idempotent SQL statements in a
  single transaction. The database service does not know the catalog schema.
- `execute(sql, parameters=())`: parameterized write; returns immutable
  `WriteResult(lastrowid, rowcount)`.
- `fetch_one(sql, parameters=())`: detached dictionary, or `None`.
- `fetch_all(sql, parameters=())`: list of detached dictionaries.
- `transaction(write=True)`: context manager yielding `DatabaseSession` with
  the same execute/fetch methods. Commits on success; rolls back on exception.
  Write transactions use `BEGIN IMMEDIATE` to serialize writers. Read-only
  usage can pass `write=False` for a consistent deferred transaction/snapshot.
  This flag selects transaction mode; it is not a permission restriction.

Sessions are valid only inside their context. Do not nest operations on the
same database by opening another write transaction while one is active; use
the supplied session for the entire transaction. No raw connections or cursors
are returned. Use positional or named SQL parameters for data values.

SQLite failures surface as `DatabaseError` or its `DatabaseConstraintError`
subclass. Original exceptions are chained for diagnostics. Caller exceptions
are preserved after rollback. SQL is trusted service code, not an agent-facing
query interface.

## Catalog public interface

`MusicCatalogService(database)` initializes its schema idempotently in the
supplied generic database. Records are dictionaries using the schema column
names below. Methods that look up missing records return `None`; operations
that require an existing record raise `RecordNotFound`.

| Capability | Methods |
| --- | --- |
| Songs | `get_or_create_song(suno_song_id, original_suno_url, *, title=None, lyrics=None, style=None, suno_username=None, artist_id=None, artwork_url=None, artwork_file=None)`, `get_song(suno_song_id)`, `get_song_by_url(original_suno_url)` |
| Retrieved metadata | `update_song_metadata(suno_song_id, **metadata)`; accepts `title`, `lyrics`, `style`, `suno_username`, `artwork_url`, `artwork_file` |
| Artist association | `associate_song(suno_song_id, artist_id)`; `None` removes association |
| Artists | `create_artist(*, suno_artist_name=None, discord_handle=None, aliases=(), preferred_display_alias=None)`, `get_artist(artist_id)`, `update_artist(artist_id, **metadata)` |
| Aliases | `add_artist_alias(artist_id, alias)`, `resolve_artist_alias(alias)` |
| Browsing | `list_songs(*, artist_id=None, limit=100, offset=0)`, `list_artists(*, limit=100, offset=0)` |
| Submissions | `store_submission(suno_song_id, discord_handle, original_message, *, submitted_title=None, genre=None, description=None, content_warning=None, batch_id=None, discord_timestamp=None, posted_at=None)`, `list_submissions(*, suno_song_id=None, batch_id=None, limit=100, offset=0)` |
| Batch audit | `create_batch(*, source_text=None)`, `record_batch_item(batch_id, *, outcome, suno_song_id=None, source_text=None, error=None)`, `finish_batch(batch_id)`, `get_batch(batch_id)`, `list_batches(*, limit=100, offset=0)` |
| Scoped batch reads | `list_batch_summaries(*, batch_id=None, limit=100, offset=0)` (newest first, no source text), `list_batch_items(batch_id, *, outcome=None, limit=100, offset=0)`, `get_batch_item(item_id)` |

`get_or_create_song` returns `(song, created)`. All other creation/update methods
return the stored record. Duplicate Suno IDs return the existing song **without
overwriting its URL, metadata, or artist**. Update metadata explicitly afterward.
Titles are never unique. Deduplication is protected by a database UNIQUE
constraint and a transaction, including when separate service instances insert
concurrently.

Artist records include an `aliases` list. Internal IDs are the relationship
keys; names and handles can change and are not unique. Aliases are explicit,
exact, case-sensitive strings. Names/handles are not implicitly registered as
aliases. A preferred display alias is automatically added to that artist's
aliases. Adding the same alias to the same artist is idempotent. Assigning it
to a different artist raises `AliasConflict` and rolls back the operation;
identities are never merged automatically.

Updates preserve omitted fields and clear fields explicitly set to `None`.
Text is stored verbatim, including Unicode, combining marks, spacing, and case;
validation checks do not rewrite values. No URL or message parsing occurs.

Multiple Discord submissions of the same song are retained. Their submitted
titles and attribution never replace Suno song metadata. Submission Suno IDs
can exist before the corresponding song has been retrieved.

Batch items are caller-reported audit results (`added`, `existing`, `failed`),
not a job queue or ingestion implementation. Successful items must reference
an existing catalog song. Failed items require an error and may have an unknown
or absent song ID. Repeated input items are retained. `finish_batch` records a
completion timestamp idempotently and prevents later item additions through
the API. `get_batch` includes ordered `items` and derived outcome `counts`;
`list_batches` returns batch headers. Timestamps are UTC ISO 8601 text.

## Initial schema

Defined in `schema.py`; initialization uses transactional `CREATE ... IF NOT
EXISTS`. Existing submission tables gain nullable `discord_timestamp` and
`posted_at` columns through an idempotent, transactional migration. Existing
records are retained without guessing their historical post dates.

| Table | Columns and constraints |
| --- | --- |
| `songs` | `id` INTEGER PK; `suno_song_id` nonempty TEXT UNIQUE NOT NULL; `original_suno_url` nonempty TEXT NOT NULL; nullable `title`, `lyrics`, `style`, `suno_username`, `artwork_url`, `artwork_file` TEXT; nullable `artist_id` FK → `artists.id` |
| `artists` | `id` INTEGER PK; nullable `suno_artist_name`, `discord_handle`, `preferred_display_alias` TEXT |
| `artist_aliases` | `alias` nonempty TEXT PK; `artist_id` NOT NULL FK → `artists.id` with cascade delete |
| `discord_submissions` | `id` INTEGER PK; nonempty `suno_song_id` TEXT NOT NULL; `discord_handle`, `original_message` TEXT NOT NULL; nullable `submitted_title`, `genre`, `description`, `content_warning`, `discord_timestamp`, `posted_at` TEXT; optional `batch_id` FK → `ingestion_batches.id`; `created_at` timestamp |
| `ingestion_batches` | `id` INTEGER PK; optional `source_text` TEXT; `created_at` timestamp; nullable `completed_at` timestamp |
| `ingestion_batch_items` | `id` INTEGER PK; `batch_id` NOT NULL FK → `ingestion_batches.id`; optional `suno_song_id`, `source_text`, `error` TEXT; `outcome` TEXT constrained to added/existing/failed; successful outcomes require an ID; failed outcomes require a nonempty error |

Submission/item Suno IDs intentionally have no song FK: failed/unretrieved
song references must survive without inventing catalog songs. Artist and batch
relationships use foreign keys. Indexes cover artist associations, alias owners,
submission song/batch lookups, and batch-item lookups.

## Verification

```sh
uv run python -m unittest tests.test_catalog -v
uv run python -m unittest discover -v
```

22 new tests run against real SQLite files in isolated temporary directories;
no mocked database and no user data or Codex authentication are involved.
They cover automatic/idempotent schema initialization, persistence through
service recreation, transactions and rollback (including DDL), database-enforced
constraints, concurrent duplicate insertion, parameter binding, Unicode
preservation, partial metadata updates, artist renaming/association, alias
collisions, distinct Discord attribution, batch outcomes, and pagination.

The full suite passes: **43 tests**, including the 21 existing service tests.
No new live Codex, Discord, or Suno checks were run or are needed for this scope.
