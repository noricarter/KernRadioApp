"""Read-only calculated metrics over catalog records and playback history."""
from datetime import datetime

from services.catalog import MusicCatalogService, RecordNotFound
from services.database import DatabaseService
from .common import AGGREGATES, date_range, now_utc, page, utc_timestamp
from .schema import SCHEMA


class PlaybackMetricsService:
    def __init__(self, database: DatabaseService, catalog: MusicCatalogService):
        self._database, self._catalog = database, catalog
        database.initialize(SCHEMA)

    def song_statistics(self, suno_song_id, *, start=None, end=None, as_of=None) -> dict:
        if self._catalog.get_song(suno_song_id) is None:
            raise RecordNotFound(f'Song {suno_song_id} does not exist.')
        where, parameters = date_range(start, end)
        result = self._database.fetch_one(f'''SELECT s.id AS song_id, s.suno_song_id, {AGGREGATES}
            FROM songs s LEFT JOIN playback_events e ON e.song_id = s.id {where}
            WHERE s.suno_song_id = ? GROUP BY s.id''', (*parameters, suno_song_id))
        reference = datetime.fromisoformat(utc_timestamp(as_of if as_of is not None else now_utc()))
        last = result['last_played_at']
        result['seconds_since_last_play'] = (reference - datetime.fromisoformat(last)).total_seconds() if last else None
        return result

    def artist_statistics(self, artist_id, *, start=None, end=None) -> dict:
        if self._catalog.get_artist(artist_id) is None:
            raise RecordNotFound(f'Artist {artist_id} does not exist.')
        where, parameters = date_range(start, end)
        return self._database.fetch_one(f'''SELECT a.id AS artist_id, {AGGREGATES}
            FROM artists a LEFT JOIN songs s ON s.artist_id = a.id
            LEFT JOIN playback_events e ON e.song_id = s.id {where}
            WHERE a.id = ? GROUP BY a.id''', (*parameters, artist_id))

    def song_play_counts(self, *, start=None, end=None, order='most_played', limit=100, offset=0) -> list[dict]:
        """Catalog songs including zero plays; order is selection, never a score."""
        orders = {
            'most_played': 'completed_plays DESC, s.id',
            'least_played': 'completed_plays ASC, s.id',
            'least_recent': 'last_played_at IS NOT NULL, last_played_at, s.id',
            'catalog': 's.id',
        }
        if order not in orders:
            raise ValueError('Unknown playback count ordering.')
        page(limit, offset)
        where, parameters = date_range(start, end)
        return self._database.fetch_all(f'''SELECT s.id AS song_id, s.suno_song_id,
            s.title, s.artist_id, {AGGREGATES}
            FROM songs s LEFT JOIN playback_events e ON e.song_id = s.id {where}
            GROUP BY s.id ORDER BY {orders[order]} LIMIT ? OFFSET ?''', (*parameters, limit, offset))

    def songs_never_played(self, *, limit=100, offset=0) -> list[dict]:
        page(limit, offset)
        return self._database.fetch_all('''SELECT s.* FROM songs s
            WHERE NOT EXISTS (SELECT 1 FROM playback_events e WHERE e.song_id = s.id)
            ORDER BY s.id LIMIT ? OFFSET ?''', (limit, offset))

    def artist_airtime_distribution(self, *, start=None, end=None) -> list[dict]:
        """Includes an artist_id=None bucket for unassociated catalog songs."""
        where, parameters = date_range(start, end)
        rows = self._database.fetch_all(f'''SELECT s.artist_id, a.preferred_display_alias, {AGGREGATES}
            FROM songs s LEFT JOIN artists a ON a.id = s.artist_id
            LEFT JOIN playback_events e ON e.song_id = s.id {where}
            GROUP BY s.artist_id ORDER BY s.artist_id''', parameters)
        total = sum(row['airtime_seconds'] for row in rows)
        for row in rows:
            row['airtime_share'] = row['airtime_seconds'] / total if total else 0.0
        return rows

    def representation(self, *, metadata='style', start=None, end=None) -> list[dict]:
        """Exact stored style strings or distinct submitted genres; no taxonomy."""
        where, parameters = date_range(start, end)
        if metadata == 'style':
            labels = 'SELECT id AS song_id, style AS label FROM songs'
        elif metadata == 'genre':
            # Multiple submissions of one genre must not multiply a song's plays.
            labels = '''SELECT DISTINCT s.id AS song_id, d.genre AS label FROM songs s
                JOIN discord_submissions d ON d.suno_song_id = s.suno_song_id
                WHERE d.genre IS NOT NULL AND d.genre != ''
                UNION ALL SELECT s.id, NULL FROM songs s WHERE NOT EXISTS (
                    SELECT 1 FROM discord_submissions d WHERE d.suno_song_id = s.suno_song_id
                    AND d.genre IS NOT NULL AND d.genre != '')'''
        else:
            raise ValueError('Metadata must be style or genre.')
        return self._database.fetch_all(f'''WITH labels AS ({labels})
            SELECT l.label, COUNT(DISTINCT l.song_id) AS song_count, {AGGREGATES}
            FROM labels l LEFT JOIN playback_events e ON e.song_id = l.song_id {where}
            GROUP BY l.label ORDER BY l.label''', parameters)
