"""Record playback facts without playback, scheduling, or rotation behavior."""
from services.catalog import MusicCatalogService, RecordNotFound
from services.database import DatabaseService
from .common import date_range, now_utc, page, utc_timestamp
from .schema import SCHEMA


class PlaybackHistoryService:
    def __init__(self, database: DatabaseService, catalog: MusicCatalogService):
        self._database, self._catalog = database, catalog
        database.initialize(SCHEMA)

    def _song(self, suno_song_id):
        song = self._catalog.get_song(suno_song_id)
        if song is None:
            raise RecordNotFound(f'Song {suno_song_id} does not exist.')
        return song

    def get_event(self, event_id: int) -> dict | None:
        return self._database.fetch_one('''SELECT e.*, s.suno_song_id FROM playback_events e
            JOIN songs s ON s.id = e.song_id WHERE e.id = ?''', (event_id,))

    def record_playback(self, suno_song_id, *, started_at, ended_at=None, outcome='completed') -> dict:
        """Append a historical terminal event. An unknown end remains None."""
        if outcome not in ('completed', 'interrupted'):
            raise ValueError('Outcome must be completed or interrupted.')
        return self._insert(suno_song_id, started_at, ended_at, outcome)

    def start_playback(self, suno_song_id, *, started_at=None) -> dict:
        """Append a pending event; its ID is used for a future finish/interrupt."""
        return self._insert(suno_song_id, started_at if started_at is not None else now_utc(), None, None)

    def _insert(self, suno_song_id, started_at, ended_at, outcome):
        song = self._song(suno_song_id)
        start = utc_timestamp(started_at)
        end = utc_timestamp(ended_at) if ended_at is not None else None
        if end is not None and end < start:
            raise ValueError('Playback cannot end before it starts.')
        with self._database.transaction() as session:
            result = session.execute('''INSERT INTO playback_events(song_id, started_at, ended_at, outcome)
                VALUES (?, ?, ?, ?)''', (song['id'], start, end, outcome))
            event_id = result.lastrowid
        return self.get_event(event_id)

    def finish_playback(self, event_id, *, ended_at=None) -> dict:
        return self._finalize(event_id, ended_at, 'completed')

    def interrupt_playback(self, event_id, *, ended_at=None) -> dict:
        return self._finalize(event_id, ended_at, 'interrupted')

    def _finalize(self, event_id, ended_at, outcome):
        with self._database.transaction() as session:
            event = session.fetch_one('SELECT * FROM playback_events WHERE id = ?', (event_id,))
            if event is None:
                raise RecordNotFound(f'Playback event {event_id} does not exist.')
            end = utc_timestamp(ended_at if ended_at is not None else now_utc())
            if event['outcome'] is not None:
                if event['outcome'] != outcome or (ended_at is not None and event['ended_at'] != end):
                    raise ValueError('This playback event is already finalized; its history cannot be overwritten.')
            else:
                if end < event['started_at']:
                    raise ValueError('Playback cannot end before it starts.')
                session.execute('UPDATE playback_events SET ended_at = ?, outcome = ? WHERE id = ?', (end, outcome, event_id))
        return self.get_event(event_id)

    def list_events(self, *, suno_song_id=None, artist_id=None, start=None, end=None,
                    outcome=None, limit=100, offset=0, order='oldest') -> list[dict]:
        """Starts in [start, end); default chronological, optional newest first."""
        page(limit, offset)
        if order not in ('oldest', 'newest'):
            raise ValueError('History order must be oldest or newest.')
        direction = 'DESC' if order == 'newest' else 'ASC'
        where, parameters = date_range(start, end)
        if suno_song_id is not None:
            song = self._song(suno_song_id)
            where += ' AND e.song_id = ?'
            parameters.append(song['id'])
        if artist_id is not None:
            if self._catalog.get_artist(artist_id) is None:
                raise RecordNotFound(f'Artist {artist_id} does not exist.')
            where += ' AND s.artist_id = ?'
            parameters.append(artist_id)
        if outcome is not None:
            if outcome not in ('completed', 'interrupted'):
                raise ValueError('Outcome must be completed or interrupted.')
            where += ' AND e.outcome = ?'
            parameters.append(outcome)
        return self._database.fetch_all(f'''SELECT e.*, s.suno_song_id FROM playback_events e
            JOIN songs s ON s.id = e.song_id WHERE 1=1 {where}
            ORDER BY e.started_at {direction}, e.id {direction} LIMIT ? OFFSET ?''', (*parameters, limit, offset))
