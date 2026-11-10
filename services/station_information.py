"""Scoped read-only station information; no Flask, agent, or programming decisions."""
from services.catalog import RecordNotFound
from services.playback.common import now_utc, utc_timestamp


class StationInformationService:
    def __init__(self, database, catalog, history, metrics):
        self._database, self.catalog, self.history, self.metrics = database, catalog, history, metrics

    def overview(self):
        counts = self._database.fetch_one('''SELECT COUNT(*) AS songs,
            COUNT(DISTINCT artist_id) AS represented_artists,
            COUNT(CASE WHEN artist_id IS NULL THEN 1 END) AS unassociated_songs FROM songs''')
        counts['artists'] = self._database.fetch_one('SELECT COUNT(*) AS count FROM artists')['count']
        counts['youtube_tracks'] = self._database.fetch_one('SELECT COUNT(*) AS count FROM youtube_tracks')['count']
        counts['unnamed_artists'] = self._database.fetch_one("SELECT COUNT(*) AS count FROM artists a WHERE (preferred_display_alias IS NULL OR trim(preferred_display_alias) = '') AND EXISTS (SELECT 1 FROM songs s WHERE s.artist_id = a.id)")['count']
        counts['playback'] = self._database.fetch_one('''SELECT COUNT(*) AS started_plays,
            COUNT(CASE WHEN outcome = 'completed' THEN 1 END) AS completed_plays,
            COUNT(CASE WHEN outcome = 'interrupted' THEN 1 END) AS interrupted_plays FROM playback_events''')
        counts['artist_representation'] = self._database.fetch_all('''SELECT s.artist_id, a.preferred_display_alias,
            COUNT(*) AS songs FROM songs s LEFT JOIN artists a ON a.id = s.artist_id
            GROUP BY s.artist_id ORDER BY songs DESC, s.artist_id LIMIT 10''')
        counts['duration_availability'] = 'Song durations are not stored in this catalog. Playback airtime is not a song-duration measurement.'
        counts['latest_import'] = self.latest_import()
        return counts

    def latest_import(self):
        batches = self.catalog.list_batch_summaries(limit=1)
        return batches[0] if batches else None

    def playback_context(self, *, song_ids=(), limit=20):
        """Bounded playback evidence for a caller's selection/review, not a queue."""
        self.validate_page(limit, 0)
        if not isinstance(song_ids, (list, tuple)) or len(song_ids) > 20 or any(
                not isinstance(song_id, str) or not song_id for song_id in song_ids):
            raise ValueError('Supply at most 20 catalog song IDs.')
        observed_at = utc_timestamp(now_utc())
        counts = self._database.fetch_one('''SELECT COUNT(*) AS catalog_songs,
            COUNT(CASE WHEN NOT EXISTS (SELECT 1 FROM playback_events e WHERE e.song_id=s.id)
                THEN 1 END) AS never_started_songs FROM songs s''')
        counts['total_playback_events'] = self._database.fetch_one('SELECT COUNT(*) AS total FROM playback_events')['total']

        def describe(song_id):
            song = self.catalog.get_song(song_id)
            if song is None:
                raise RecordNotFound('Song does not exist.')
            artist = self.catalog.get_artist(song['artist_id']) if song['artist_id'] else None
            return {'title': song['title'], 'artist_id': song['artist_id'],
                    'artist': artist['preferred_display_alias'] if artist else None,
                    'style': (song['style'] or '')[:300] or None}

        recent = [event | describe(event['suno_song_id'])
                  for event in self.history.list_events(order='newest', limit=limit)]
        candidates = [row | describe(row['suno_song_id'])
                      for row in self.metrics.song_play_counts(order='least_recent', limit=limit)]
        requested = [self.metrics.song_statistics(song_id, as_of=observed_at) | describe(song_id)
                     for song_id in dict.fromkeys(song_ids)]
        return counts | {'observed_at': observed_at, 'recent_plays': recent,
            'recent_order': 'newest_first', 'page_limit': limit,
            'unplayed_or_least_recent_candidates': candidates, 'current_and_queued_statistics': requested,
            'scope': 'Recent plays and candidate lists are bounded samples, not the complete library/history. Counts and last-played values include the full recorded history. An unfinished/interrupted start is not a completed play, but is still evidence a song was heard. Read additional pages or specific song statistics as needed.'}

    def ingestion_batches(self, *, limit=10, offset=0):
        self.validate_page(limit, offset)
        return self.catalog.list_batch_summaries(limit=limit, offset=offset)

    def ingestion_items(self, *, batch_id=None, outcome='failed', limit=10, offset=0):
        """Read persisted import evidence; default to failures in the latest batch."""
        self.validate_page(limit, offset)
        if batch_id is not None:
            self._positive_id(batch_id, 'batch ID')
        if outcome not in ('added', 'existing', 'failed', 'all'):
            raise ValueError('Choose added, existing, failed, or all outcomes.')
        batches = self.catalog.list_batch_summaries(batch_id=batch_id, limit=1)
        if not batches:
            raise RecordNotFound('Import batch does not exist.')
        batch = batches[0]
        items = self.catalog.list_batch_items(batch['id'], outcome=None if outcome == 'all' else outcome,
                                             limit=limit, offset=offset)
        for item in items:
            source = item.pop('source_text') or ''
            item.update(source_excerpt=source[:500], source_total_characters=len(source))
        total = sum(batch['counts'].values()) if outcome == 'all' else batch['counts'][outcome]
        return {'batch': batch, 'outcome': outcome, 'items': items, 'total': total, 'offset': offset, 'limit': limit}

    def ingestion_item(self, item_id, *, source_offset=0, source_limit=3000):
        self._positive_id(item_id, 'item ID')
        if type(source_offset) is not int or source_offset < 0 or type(source_limit) is not int or not 1 <= source_limit <= 4000:
            raise ValueError('Use a nonnegative source offset and 1–4,000 characters.')
        item = self.catalog.get_batch_item(item_id)
        if item is None:
            raise RecordNotFound('Import entry does not exist.')
        source = item['source_text'] or ''
        return item | {'source_text': source[source_offset:source_offset + source_limit],
                       'source_total_characters': len(source), 'source_offset': source_offset}

    @staticmethod
    def _positive_id(value, name):
        if type(value) is not int or value < 1:
            raise ValueError(f'Choose a valid {name}.')

    def songs(self, *, artist_id=None, limit=10, offset=0):
        self.validate_page(limit, offset)
        rows = self.catalog.list_songs(artist_id=artist_id, limit=limit, offset=offset)
        return [{key: song[key] for key in ('id', 'suno_song_id', 'title', 'artist_id', 'suno_username')}
                | {'style': (song['style'] or '')[:1000] or None, 'has_lyrics': bool(song['lyrics']),
                   'duration_seconds': None} for song in rows]

    def song(self, suno_song_id, *, lyrics_offset=0, lyrics_limit=3000):
        if type(lyrics_offset) is not int or lyrics_offset < 0 or type(lyrics_limit) is not int or not 1 <= lyrics_limit <= 4000:
            raise ValueError('Invalid lyrics page.')
        song = self.catalog.get_song(suno_song_id)
        if song is None:
            raise RecordNotFound('Song does not exist.')
        lyrics = song['lyrics']
        return song | {'lyrics': lyrics[lyrics_offset:lyrics_offset + lyrics_limit] if lyrics else None,
                       'lyrics_total_characters': len(lyrics or ''), 'lyrics_offset': lyrics_offset,
                       'submissions': [{key: (post[key][:1000] if isinstance(post[key], str) else post[key])
                           for key in ('discord_handle', 'submitted_title', 'genre', 'description', 'content_warning')}
                           for post in self.catalog.list_submissions(suno_song_id=suno_song_id, limit=3)],
                       'duration_seconds': None}

    def artists(self, *, limit=10, offset=0):
        self.validate_page(limit, offset)
        return self._database.fetch_all('''SELECT a.id, a.suno_artist_name, a.discord_handle,
            a.preferred_display_alias, COUNT(s.id) AS song_count FROM artists a
            LEFT JOIN songs s ON s.artist_id = a.id GROUP BY a.id ORDER BY a.id LIMIT ? OFFSET ?''', (limit, offset))

    @staticmethod
    def validate_page(limit, offset):
        if type(limit) is not int or not 1 <= limit <= 20 or type(offset) is not int or offset < 0:
            raise ValueError('Use pages of 1–20 records and a nonnegative offset.')
