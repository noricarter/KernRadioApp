"""Initial catalog schema. Generic database infrastructure knows nothing of it."""
SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS artists (
        id INTEGER PRIMARY KEY,
        suno_artist_name TEXT,
        discord_handle TEXT,
        preferred_display_alias TEXT
    )''',
    '''CREATE TABLE IF NOT EXISTS artist_aliases (
        alias TEXT PRIMARY KEY NOT NULL CHECK(length(trim(alias)) > 0),
        artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE
    )''',
    'CREATE INDEX IF NOT EXISTS artist_aliases_artist ON artist_aliases(artist_id)',
    '''CREATE TABLE IF NOT EXISTS songs (
        id INTEGER PRIMARY KEY,
        suno_song_id TEXT NOT NULL UNIQUE CHECK(length(trim(suno_song_id)) > 0),
        original_suno_url TEXT NOT NULL CHECK(length(trim(original_suno_url)) > 0),
        title TEXT,
        lyrics TEXT,
        style TEXT,
        suno_username TEXT,
        artwork_url TEXT,
        artwork_file TEXT,
        artist_id INTEGER REFERENCES artists(id)
    )''',
    'CREATE INDEX IF NOT EXISTS songs_artist ON songs(artist_id)',
    'CREATE INDEX IF NOT EXISTS songs_original_url ON songs(original_suno_url)',
    '''CREATE TABLE IF NOT EXISTS ingestion_batches (
        id INTEGER PRIMARY KEY,
        source_text TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        completed_at TEXT
    )''',
    '''CREATE TABLE IF NOT EXISTS discord_submissions (
        id INTEGER PRIMARY KEY,
        suno_song_id TEXT NOT NULL CHECK(length(trim(suno_song_id)) > 0),
        discord_handle TEXT NOT NULL,
        original_message TEXT NOT NULL,
        submitted_title TEXT,
        genre TEXT,
        description TEXT,
        content_warning TEXT,
        discord_timestamp TEXT,
        posted_at TEXT,
        batch_id INTEGER REFERENCES ingestion_batches(id),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )''',
    'CREATE INDEX IF NOT EXISTS submissions_song ON discord_submissions(suno_song_id)',
    'CREATE INDEX IF NOT EXISTS submissions_batch ON discord_submissions(batch_id)',
    '''CREATE TABLE IF NOT EXISTS youtube_tracks (
        id INTEGER PRIMARY KEY,
        video_id TEXT NOT NULL UNIQUE,
        original_url TEXT NOT NULL,
        title TEXT, genre TEXT, description TEXT, content_warning TEXT,
        artist_id INTEGER REFERENCES artists(id)
    )''',
    '''CREATE TABLE IF NOT EXISTS youtube_submissions (
        id INTEGER PRIMARY KEY,
        video_id TEXT NOT NULL REFERENCES youtube_tracks(video_id),
        discord_handle TEXT NOT NULL,
        original_message TEXT NOT NULL,
        submitted_title TEXT, genre TEXT, description TEXT, content_warning TEXT,
        discord_timestamp TEXT, posted_at TEXT,
        batch_id INTEGER REFERENCES ingestion_batches(id)
    )''',
    '''CREATE TABLE IF NOT EXISTS ingestion_batch_items (
        id INTEGER PRIMARY KEY,
        batch_id INTEGER NOT NULL REFERENCES ingestion_batches(id),
        suno_song_id TEXT,
        youtube_video_id TEXT REFERENCES youtube_tracks(video_id),
        outcome TEXT NOT NULL CHECK(outcome IN ('added', 'existing', 'failed')),
        source_text TEXT,
        error TEXT,
        CHECK(outcome = 'failed' OR suno_song_id IS NOT NULL OR youtube_video_id IS NOT NULL),
        CHECK(suno_song_id IS NULL OR youtube_video_id IS NULL),
        CHECK(outcome != 'failed' OR (error IS NOT NULL AND length(trim(error)) > 0))
    )''',
    'CREATE INDEX IF NOT EXISTS batch_items_batch ON ingestion_batch_items(batch_id)',
)


def initialize_schema(database):
    """Add nullable post-date/artwork columns without rewriting existing records."""
    with database.transaction() as session:
        for statement in SCHEMA:
            session.execute(statement)
        columns = {column['name'] for column in session.fetch_all('PRAGMA table_info(discord_submissions)')}
        for name in ('discord_timestamp', 'posted_at'):
            if name not in columns:
                session.execute(f'ALTER TABLE discord_submissions ADD COLUMN {name} TEXT')
        song_columns = {column['name'] for column in session.fetch_all('PRAGMA table_info(songs)')}
        for name in ('artwork_url', 'artwork_file'):
            if name not in song_columns:
                session.execute(f'ALTER TABLE songs ADD COLUMN {name} TEXT')
        item_columns = {column['name'] for column in session.fetch_all('PRAGMA table_info(ingestion_batch_items)')}
        if 'youtube_video_id' not in item_columns:
            # Replace only the old Suno-only success constraint. IDs/history stay
            # intact, and the migration commits atomically with the new tables.
            definition = next(statement for statement in SCHEMA if statement.startswith('CREATE TABLE IF NOT EXISTS ingestion_batch_items'))
            session.execute(definition.replace('IF NOT EXISTS ingestion_batch_items', 'ingestion_batch_items_new', 1))
            session.execute('''INSERT INTO ingestion_batch_items_new
                (id,batch_id,suno_song_id,outcome,source_text,error)
                SELECT id,batch_id,suno_song_id,outcome,source_text,error FROM ingestion_batch_items''')
            session.execute('DROP TABLE ingestion_batch_items')
            session.execute('ALTER TABLE ingestion_batch_items_new RENAME TO ingestion_batch_items')
            session.execute('CREATE INDEX batch_items_batch ON ingestion_batch_items(batch_id)')
