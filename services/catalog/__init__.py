"""Public music catalog API. Depends only on the generic database service."""
from services.database import DatabaseService
from .schema import initialize_schema

__all__ = ['MusicCatalogService', 'RecordNotFound', 'AliasConflict']


class RecordNotFound(LookupError):
    pass


class AliasConflict(ValueError):
    """An exact alias already belongs to another artist; no automatic merging."""


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a nonempty string.')
    return value  # Validation only: preserve original Unicode, spaces, and case.


def _fields(values, allowed):
    if values.keys() - allowed:
        raise ValueError('Unknown fields: ' + ', '.join(sorted(values.keys() - allowed)))
    for value in values.values():
        if value is not None and not isinstance(value, str):
            raise ValueError('Metadata must contain strings or None.')


def _page(limit, offset):
    if type(limit) is not int or type(offset) is not int or limit < 1 or offset < 0:
        raise ValueError('Use a positive integer limit and a nonnegative integer offset.')


def _require(session, table, record_id):
    # Table names below are internal constants, never caller input.
    record = session.fetch_one(f'SELECT * FROM {table} WHERE id = ?', (record_id,))
    if record is None:
        raise RecordNotFound(f'{table} record {record_id} does not exist.')
    return record


class MusicCatalogService:
    def __init__(self, database: DatabaseService):
        self._database = database
        initialize_schema(database)

    def get_or_create_song(self, suno_song_id: str, original_suno_url: str, *,
                           title=None, lyrics=None, style=None, suno_username=None,
                           artist_id: int | None = None, artwork_url=None, artwork_file=None) -> tuple[dict, bool]:
        """Return (record, created). Duplicates never overwrite existing data."""
        _text(suno_song_id, 'Suno song ID')
        _text(original_suno_url, 'Original Suno URL')
        _fields(dict(title=title, lyrics=lyrics, style=style, suno_username=suno_username,
                     artwork_url=artwork_url, artwork_file=artwork_file),
                {'title', 'lyrics', 'style', 'suno_username', 'artwork_url', 'artwork_file'})
        with self._database.transaction() as session:
            result = session.execute('''INSERT INTO songs
                (suno_song_id, original_suno_url, title, lyrics, style, suno_username, artist_id, artwork_url, artwork_file)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(suno_song_id) DO NOTHING''',
                (suno_song_id, original_suno_url, title, lyrics, style, suno_username, artist_id, artwork_url, artwork_file))
            song = session.fetch_one('SELECT * FROM songs WHERE suno_song_id = ?', (suno_song_id,))
            return song, result.rowcount == 1

    def get_song(self, suno_song_id: str) -> dict | None:
        return self._database.fetch_one('SELECT * FROM songs WHERE suno_song_id = ?', (suno_song_id,))

    def get_song_by_url(self, original_suno_url: str) -> dict | None:
        """Reuse a previously resolved original share URL without another lookup."""
        return self._database.fetch_one('SELECT * FROM songs WHERE original_suno_url = ? ORDER BY id LIMIT 1', (original_suno_url,))

    def update_song_metadata(self, suno_song_id: str, **metadata) -> dict:
        """Omitted fields remain unchanged; explicit None clears a field."""
        _fields(metadata, {'title', 'lyrics', 'style', 'suno_username', 'artwork_url', 'artwork_file'})
        with self._database.transaction() as session:
            if metadata:
                assignments = ', '.join(f'{key} = ?' for key in metadata)
                session.execute(f'UPDATE songs SET {assignments} WHERE suno_song_id = ?',
                                (*metadata.values(), suno_song_id))
            record = session.fetch_one('SELECT * FROM songs WHERE suno_song_id = ?', (suno_song_id,))
            if record is None:
                raise RecordNotFound(f'Song {suno_song_id} does not exist.')
            return record

    def associate_song(self, suno_song_id: str, artist_id: int | None) -> dict:
        """Associate by stable artist ID; None clears the association."""
        with self._database.transaction() as session:
            if artist_id is not None:
                _require(session, 'artists', artist_id)
            changed = session.execute('UPDATE songs SET artist_id = ? WHERE suno_song_id = ?',
                                      (artist_id, suno_song_id))
            if changed.rowcount == 0:
                raise RecordNotFound(f'Song {suno_song_id} does not exist.')
            return session.fetch_one('SELECT * FROM songs WHERE suno_song_id = ?', (suno_song_id,))

    def list_songs(self, *, artist_id=None, limit=100, offset=0) -> list[dict]:
        _page(limit, offset)
        if artist_id is None:
            return self._database.fetch_all('SELECT * FROM songs ORDER BY id LIMIT ? OFFSET ?', (limit, offset))
        return self._database.fetch_all('SELECT * FROM songs WHERE artist_id = ? ORDER BY id LIMIT ? OFFSET ?',
                                        (artist_id, limit, offset))

    def search_songs(self, query: str, *, limit=100, offset=0) -> tuple[list[dict], int]:
        """Search imported song titles and linked artist names/aliases."""
        _page(limit, offset)
        _text(query, 'Search text')
        escaped = query.replace('!', '!!').replace('%', '!%').replace('_', '!_')
        pattern = f'%{escaped}%'
        condition = '''(s.title LIKE ? ESCAPE '!' OR a.preferred_display_alias LIKE ? ESCAPE '!'
            OR a.suno_artist_name LIKE ? ESCAPE '!' OR a.discord_handle LIKE ? ESCAPE '!'
            OR EXISTS (SELECT 1 FROM artist_aliases aa WHERE aa.artist_id = a.id AND aa.alias LIKE ? ESCAPE '!'))'''
        parameters = (pattern, pattern, pattern, pattern, pattern)
        with self._database.transaction(write=False) as session:
            total = session.fetch_one(f'''SELECT COUNT(*) AS total FROM songs s
                LEFT JOIN artists a ON a.id = s.artist_id WHERE {condition}''', parameters)['total']
            songs = session.fetch_all(f'''SELECT s.* FROM songs s LEFT JOIN artists a ON a.id = s.artist_id
                WHERE {condition} ORDER BY s.id LIMIT ? OFFSET ?''', (*parameters, limit, offset))
            return songs, total

    @staticmethod
    def _add_alias(session, artist_id, alias):
        _text(alias, 'Alias')
        existing = session.fetch_one('SELECT artist_id FROM artist_aliases WHERE alias = ?', (alias,))
        if existing and existing['artist_id'] != artist_id:
            raise AliasConflict(f'Alias {alias!r} already belongs to another artist.')
        session.execute('INSERT INTO artist_aliases(alias, artist_id) VALUES (?, ?) ON CONFLICT(alias) DO NOTHING',
                        (alias, artist_id))

    @staticmethod
    def _artist(session, artist_id):
        artist = session.fetch_one('SELECT * FROM artists WHERE id = ?', (artist_id,))
        if artist is not None:
            artist['aliases'] = [row['alias'] for row in session.fetch_all(
                'SELECT alias FROM artist_aliases WHERE artist_id = ? ORDER BY alias', (artist_id,))]
        return artist

    def create_artist(self, *, suno_artist_name=None, discord_handle=None,
                      aliases=(), preferred_display_alias=None) -> dict:
        _fields(dict(suno_artist_name=suno_artist_name, discord_handle=discord_handle,
                     preferred_display_alias=preferred_display_alias),
                {'suno_artist_name', 'discord_handle', 'preferred_display_alias'})
        if isinstance(aliases, str):
            raise ValueError('Aliases must be a collection of strings.')
        with self._database.transaction() as session:
            result = session.execute('INSERT INTO artists(suno_artist_name, discord_handle, preferred_display_alias) VALUES (?, ?, ?)',
                                     (suno_artist_name, discord_handle, preferred_display_alias))
            for alias in aliases:
                self._add_alias(session, result.lastrowid, alias)
            if preferred_display_alias is not None:
                self._add_alias(session, result.lastrowid, preferred_display_alias)
            return self._artist(session, result.lastrowid)

    def get_artist(self, artist_id: int) -> dict | None:
        with self._database.transaction(write=False) as session:
            return self._artist(session, artist_id)

    def update_artist(self, artist_id: int, *, aliases=None, **metadata) -> dict:
        _fields(metadata, {'suno_artist_name', 'discord_handle', 'preferred_display_alias'})
        with self._database.transaction() as session:
            _require(session, 'artists', artist_id)
            if aliases is not None and (not isinstance(aliases, list) or any(not isinstance(alias, str) or not alias.strip() for alias in aliases)):
                raise ValueError('Aliases must be a list of nonempty names.')
            preferred = metadata.get('preferred_display_alias')
            if preferred is not None:
                self._add_alias(session, artist_id, preferred)
            if metadata:
                assignments = ', '.join(f'{key} = ?' for key in metadata)
                session.execute(f'UPDATE artists SET {assignments} WHERE id = ?', (*metadata.values(), artist_id))
            if aliases is not None:
                session.execute('DELETE FROM artist_aliases WHERE artist_id = ?', (artist_id,))
                for alias in aliases:
                    self._add_alias(session, artist_id, alias)
                preferred = _require(session, 'artists', artist_id)['preferred_display_alias']
                if preferred is not None:
                    self._add_alias(session, artist_id, preferred)
            return self._artist(session, artist_id)

    def add_artist_alias(self, artist_id: int, alias: str) -> dict:
        with self._database.transaction() as session:
            _require(session, 'artists', artist_id)
            self._add_alias(session, artist_id, alias)
            return self._artist(session, artist_id)

    def resolve_artist_alias(self, alias: str) -> dict | None:
        """Exact match only. Names/handles are not implicitly identity keys."""
        with self._database.transaction(write=False) as session:
            match = session.fetch_one('SELECT artist_id FROM artist_aliases WHERE alias = ?', (alias,))
            return self._artist(session, match['artist_id']) if match else None

    def list_artists(self, *, limit=100, offset=0) -> list[dict]:
        _page(limit, offset)
        with self._database.transaction(write=False) as session:
            rows = session.fetch_all('SELECT id FROM artists ORDER BY id LIMIT ? OFFSET ?', (limit, offset))
            return [self._artist(session, row['id']) for row in rows]

    def store_submission(self, suno_song_id: str, discord_handle: str, original_message: str, *,
                         submitted_title=None, genre=None, description=None, content_warning=None,
                         batch_id: int | None = None, discord_timestamp=None, posted_at=None) -> dict:
        """Preserve attribution even if the song has not been retrieved yet."""
        _text(suno_song_id, 'Suno song ID')
        _fields(dict(discord_handle=discord_handle, original_message=original_message,
                     submitted_title=submitted_title, genre=genre, description=description,
                     content_warning=content_warning, discord_timestamp=discord_timestamp, posted_at=posted_at),
                {'discord_handle', 'original_message', 'submitted_title', 'genre', 'description', 'content_warning', 'discord_timestamp', 'posted_at'})
        if not isinstance(discord_handle, str) or not isinstance(original_message, str):
            raise ValueError('Discord handle and original message must be strings.')
        with self._database.transaction() as session:
            result = session.execute('''INSERT INTO discord_submissions
                (suno_song_id, discord_handle, original_message, submitted_title, genre, description, content_warning, batch_id, discord_timestamp, posted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (suno_song_id, discord_handle, original_message, submitted_title, genre, description, content_warning, batch_id, discord_timestamp, posted_at))
            return _require(session, 'discord_submissions', result.lastrowid)

    def list_submissions(self, *, suno_song_id=None, batch_id=None, limit=100, offset=0) -> list[dict]:
        _page(limit, offset)
        filters, parameters = [], []
        for column, value in (('suno_song_id', suno_song_id), ('batch_id', batch_id)):
            if value is not None:
                filters.append(f'{column} = ?')
                parameters.append(value)
        where = ' WHERE ' + ' AND '.join(filters) if filters else ''
        return self._database.fetch_all(f'SELECT * FROM discord_submissions{where} ORDER BY id LIMIT ? OFFSET ?',
                                        (*parameters, limit, offset))

    def create_batch(self, *, source_text: str | None = None) -> dict:
        _fields({'source_text': source_text}, {'source_text'})
        with self._database.transaction() as session:
            result = session.execute('INSERT INTO ingestion_batches(source_text) VALUES (?)', (source_text,))
            return _require(session, 'ingestion_batches', result.lastrowid)

    def record_batch_item(self, batch_id: int, *, outcome: str, suno_song_id=None,
                          source_text=None, error=None, youtube_video_id=None) -> dict:
        """Record one input outcome; repeats are retained, not deduplicated."""
        if outcome not in ('added', 'existing', 'failed'):
            raise ValueError('Outcome must be added, existing, or failed.')
        _fields(dict(suno_song_id=suno_song_id, source_text=source_text, error=error),
                {'suno_song_id', 'source_text', 'error'})
        if suno_song_id is not None:
            _text(suno_song_id, 'Suno song ID')
        if youtube_video_id is not None:
            _text(youtube_video_id, 'YouTube video ID')
        if suno_song_id is not None and youtube_video_id is not None:
            raise ValueError('Choose either a Suno song or a YouTube video.')
        if outcome == 'failed':
            _text(error, 'Failure reason')
        with self._database.transaction() as session:
            batch = _require(session, 'ingestion_batches', batch_id)
            if batch['completed_at'] is not None:
                raise ValueError('This batch is already complete.')
            if outcome != 'failed':
                record = (session.fetch_one('SELECT id FROM youtube_tracks WHERE video_id = ?', (youtube_video_id,))
                    if youtube_video_id is not None else session.fetch_one('SELECT id FROM songs WHERE suno_song_id = ?', (suno_song_id,)))
                if record is None:
                    raise RecordNotFound('A successful batch item must reference a catalog song or YouTube video.')
            result = session.execute('''INSERT INTO ingestion_batch_items
                (batch_id, suno_song_id, youtube_video_id, outcome, source_text, error) VALUES (?, ?, ?, ?, ?, ?)''',
                (batch_id, suno_song_id, youtube_video_id, outcome, source_text, error))
            return _require(session, 'ingestion_batch_items', result.lastrowid)

    def finish_batch(self, batch_id: int) -> dict:
        with self._database.transaction() as session:
            _require(session, 'ingestion_batches', batch_id)
            session.execute('''UPDATE ingestion_batches
                SET completed_at = COALESCE(completed_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) WHERE id = ?''', (batch_id,))
        return self.get_batch(batch_id)

    def get_batch(self, batch_id: int) -> dict | None:
        with self._database.transaction(write=False) as session:
            batch = session.fetch_one('SELECT * FROM ingestion_batches WHERE id = ?', (batch_id,))
            if batch is not None:
                batch['items'] = session.fetch_all('SELECT * FROM ingestion_batch_items WHERE batch_id = ? ORDER BY id', (batch_id,))
                batch['counts'] = {outcome: sum(item['outcome'] == outcome for item in batch['items'])
                                   for outcome in ('added', 'existing', 'failed')}
            return batch

    def list_batches(self, *, limit=100, offset=0) -> list[dict]:
        _page(limit, offset)
        return self._database.fetch_all('SELECT * FROM ingestion_batches ORDER BY id LIMIT ? OFFSET ?', (limit, offset))

    def list_batch_summaries(self, *, batch_id=None, limit=100, offset=0) -> list[dict]:
        """Newest first, with outcome counts but without copied message text."""
        _page(limit, offset)
        where = 'WHERE b.id = ?' if batch_id is not None else ''
        parameters = (batch_id,) if batch_id is not None else ()
        rows = self._database.fetch_all(f'''SELECT b.id, b.created_at, b.completed_at,
            COUNT(CASE WHEN i.outcome = 'added' THEN 1 END) AS added,
            COUNT(CASE WHEN i.outcome = 'existing' THEN 1 END) AS existing,
            COUNT(CASE WHEN i.outcome = 'failed' THEN 1 END) AS failed
            FROM ingestion_batches b LEFT JOIN ingestion_batch_items i ON i.batch_id = b.id
            {where} GROUP BY b.id ORDER BY b.id DESC LIMIT ? OFFSET ?''', (*parameters, limit, offset))
        for row in rows:
            row['counts'] = {outcome: row.pop(outcome) for outcome in ('added', 'existing', 'failed')}
        return rows

    def list_batch_items(self, batch_id: int, *, outcome=None, limit=100, offset=0) -> list[dict]:
        """Page through saved outcomes, including failed entries with no song ID."""
        _page(limit, offset)
        if outcome is not None and outcome not in ('added', 'existing', 'failed'):
            raise ValueError('Outcome must be added, existing, or failed.')
        where = 'batch_id = ?' + (' AND outcome = ?' if outcome is not None else '')
        parameters = (batch_id, outcome) if outcome is not None else (batch_id,)
        return self._database.fetch_all(f'SELECT * FROM ingestion_batch_items WHERE {where} ORDER BY id LIMIT ? OFFSET ?',
                                        (*parameters, limit, offset))

    def get_batch_item(self, item_id: int) -> dict | None:
        return self._database.fetch_one('SELECT * FROM ingestion_batch_items WHERE id = ?', (item_id,))

    def get_or_create_youtube_track(self, video_id, original_url, *, title=None, genre=None,
                                    description=None, content_warning=None, artist_id=None):
        _text(video_id, 'YouTube video ID')
        _text(original_url, 'Original URL')
        _fields(dict(title=title, genre=genre, description=description, content_warning=content_warning),
                {'title', 'genre', 'description', 'content_warning'})
        with self._database.transaction() as session:
            result = session.execute('''INSERT INTO youtube_tracks
                (video_id,original_url,title,genre,description,content_warning,artist_id)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(video_id) DO NOTHING''',
                (video_id,original_url,title,genre,description,content_warning,artist_id))
            return session.fetch_one('SELECT * FROM youtube_tracks WHERE video_id = ?', (video_id,)), result.rowcount == 1

    def get_youtube_track(self, video_id):
        return self._database.fetch_one('SELECT * FROM youtube_tracks WHERE video_id = ?', (video_id,))

    def list_youtube_tracks(self, *, limit=20, offset=0):
        _page(limit, offset)
        return self._database.fetch_all('SELECT * FROM youtube_tracks ORDER BY id LIMIT ? OFFSET ?', (limit,offset))

    def store_youtube_submission(self, video_id, discord_handle, original_message, *, submitted_title=None,
                                  genre=None, description=None, content_warning=None, discord_timestamp=None,
                                  posted_at=None, batch_id=None):
        values = dict(submitted_title=submitted_title,genre=genre,description=description,content_warning=content_warning,
                      discord_timestamp=discord_timestamp,posted_at=posted_at)
        _fields(values, set(values))
        _text(original_message, 'Original message')
        if not isinstance(discord_handle, str):
            raise ValueError('Discord attribution must be text.')
        with self._database.transaction() as session:
            result = session.execute('''INSERT INTO youtube_submissions
                (video_id,discord_handle,original_message,submitted_title,genre,description,content_warning,discord_timestamp,posted_at,batch_id)
                VALUES (?,?,?,?,?,?,?,?,?,?)''', (video_id,discord_handle,original_message,*values.values(),batch_id))
            return _require(session, 'youtube_submissions', result.lastrowid)

    def list_youtube_submissions(self, video_id, *, limit=20, offset=0):
        _page(limit, offset)
        return self._database.fetch_all('SELECT * FROM youtube_submissions WHERE video_id = ? ORDER BY id LIMIT ? OFFSET ?', (video_id,limit,offset))

    def update_submission_author(self, submission_id, discord_handle):
        _text(discord_handle, 'Discord author')
        with self._database.transaction() as session:
            _require(session, 'discord_submissions', submission_id)
            session.execute('UPDATE discord_submissions SET discord_handle = ? WHERE id = ?', (discord_handle,submission_id))
