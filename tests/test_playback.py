"""Playback infrastructure verified against isolated real SQLite databases."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from services.catalog import MusicCatalogService, RecordNotFound
from services.database import DatabaseConstraintError, DatabaseService
from services.playback import PlaybackHistoryService, PlaybackMetricsService


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'catalog.sqlite3'
        self.database = DatabaseService(self.path)
        self.catalog = MusicCatalogService(self.database)
        self.artist = self.catalog.create_artist(aliases=['α'], preferred_display_alias='𝒜 🌲')
        self.other_artist = self.catalog.create_artist(preferred_display_alias='Other')
        self.empty_artist = self.catalog.create_artist(preferred_display_alias='No songs')
        for song_id, artist_id, style in (
                ('a', self.artist['id'], 'ambient, folk'),
                ('b', self.artist['id'], 'ambient, folk'),
                ('c', self.other_artist['id'], 'rock'),
                ('d', None, None), ('never', None, None)):
            self.catalog.get_or_create_song(song_id, 'https://suno.com/song/' + song_id,
                                           title='Été 🌳 ' + song_id, artist_id=artist_id, style=style)
        self.history = PlaybackHistoryService(self.database, self.catalog)
        self.metrics = PlaybackMetricsService(self.database, self.catalog)

    def record(self, song='a', start='2026-11-01T10:00:00Z', seconds=120, outcome='completed'):
        began = datetime.fromisoformat(start)
        return self.history.record_playback(song, started_at=began,
            ended_at=began + timedelta(seconds=seconds), outcome=outcome)

    def populate(self):
        self.record()
        self.record(start='2026-11-01T11:00:00Z', seconds=30, outcome='interrupted')
        self.record('b', start='2026-11-01T11:10:00Z')
        self.record('c', start='2026-11-01T12:00:00Z', seconds=60)
        self.record('d', start='2026-11-01T13:00:00Z', seconds=10)

    def test_multiple_plays_preserve_history_and_sort_by_start_then_id(self):
        later = self.record(start='2026-11-01T12:00:00Z')
        earlier = self.record(start='2026-11-01T10:00:00Z')
        tied = self.record(start='2026-11-01T10:00:00Z')
        events = self.history.list_events(suno_song_id='a')
        self.assertEqual([event['id'] for event in events], [earlier['id'], tied['id'], later['id']])
        self.assertEqual(len({event['id'] for event in events}), 3)
        self.assertEqual(self.history.get_event(later['id']), later)
        self.assertIsNone(self.history.get_event(999))

    def test_future_notifications_start_finish_interrupt(self):
        started = self.history.start_playback('a', started_at='2026-11-01T10:00:00Z')
        self.assertIsNone(started['outcome'])
        self.assertIsNone(started['ended_at'])
        done = self.history.finish_playback(started['id'], ended_at='2026-11-01T10:01:00Z')
        self.assertEqual(done['outcome'], 'completed')
        self.assertEqual(done['id'], started['id'])
        other = self.history.start_playback('a', started_at='2026-11-01T11:00:00Z')
        interrupted = self.history.interrupt_playback(other['id'], ended_at='2026-11-01T11:00:10Z')
        self.assertEqual(interrupted['outcome'], 'interrupted')
        self.assertEqual(len(self.history.list_events()), 2)

    def test_finalize_is_idempotent_but_cannot_overwrite_known_history(self):
        event = self.history.start_playback('a', started_at='2026-11-01T10:00:00Z')
        done = self.history.finish_playback(event['id'], ended_at='2026-11-01T10:01:00Z')
        self.assertEqual(self.history.finish_playback(event['id']), done)
        self.assertEqual(self.history.finish_playback(event['id'], ended_at=done['ended_at']), done)
        with self.assertRaises(ValueError):
            self.history.interrupt_playback(event['id'], ended_at=done['ended_at'])
        with self.assertRaises(ValueError):
            self.history.finish_playback(event['id'], ended_at='2026-11-01T10:02:00Z')
        self.assertEqual(self.history.get_event(event['id']), done)

    def test_default_notification_times_are_utc(self):
        event = self.history.start_playback('a')
        done = self.history.finish_playback(event['id'])
        self.assertTrue(event['started_at'].endswith('Z'))
        self.assertTrue(done['ended_at'].endswith('Z'))
        self.assertGreaterEqual(done['ended_at'], event['started_at'])

    def test_timezone_offsets_are_normalized_before_comparison(self):
        event = self.history.record_playback('a', started_at='2026-11-01T03:00:00-07:00',
                                             ended_at='2026-11-01T12:00:01+02:00')
        self.assertEqual(event['started_at'], '2026-11-01T10:00:00.000000Z')
        self.assertEqual(event['ended_at'], '2026-11-01T10:00:01.000000Z')
        self.assertEqual(self.metrics.song_statistics('a')['airtime_seconds'], 1)

    def test_missing_end_is_not_assumed_and_pending_does_not_count_as_completed(self):
        self.history.record_playback('a', started_at='2026-11-01T10:00:00Z', outcome='completed')
        self.history.record_playback('a', started_at='2026-11-01T11:00:00Z', outcome='interrupted')
        self.history.start_playback('a', started_at='2026-11-01T12:00:00Z')
        stats = self.metrics.song_statistics('a')
        self.assertEqual(stats['completed_plays'], 1)
        self.assertEqual(stats['interrupted_plays'], 1)
        self.assertEqual(stats['unfinished_plays'], 1)
        self.assertEqual(stats['started_plays'], 3)
        self.assertEqual(stats['airtime_seconds'], 0)
        self.assertEqual(stats['last_played_at'], '2026-11-01T12:00:00.000000Z')
        self.assertNotIn('a', [song['suno_song_id'] for song in self.metrics.songs_never_played()])

    def test_counts_last_play_and_elapsed_time(self):
        self.populate()
        stats = self.metrics.song_statistics('a', as_of='2026-11-01T12:00:00Z')
        self.assertEqual(stats['completed_plays'], 1)
        self.assertEqual(stats['interrupted_plays'], 1)
        self.assertEqual(stats['started_plays'], 2)
        self.assertEqual(stats['last_played_at'], '2026-11-01T11:00:00.000000Z')
        self.assertEqual(stats['seconds_since_last_play'], 3600)
        self.assertEqual(stats['airtime_seconds'], 150)

    def test_microsecond_airtime_across_midnight(self):
        self.record(start='2026-10-31T23:59:59.500000Z', seconds=1.25)
        self.record(start='2026-11-01T01:00:00.000001Z', seconds=.000003, outcome='interrupted')
        self.assertAlmostEqual(self.metrics.song_statistics('a')['airtime_seconds'], 1.250003, places=6)

    def test_artist_totals_history_and_renaming_preserve_identity(self):
        self.populate()
        self.catalog.update_artist(self.artist['id'], preferred_display_alias='Changed')
        stats = self.metrics.artist_statistics(self.artist['id'])
        self.assertEqual(stats['completed_plays'], 2)
        self.assertEqual(stats['interrupted_plays'], 1)
        self.assertEqual(stats['airtime_seconds'], 270)
        self.assertEqual(stats['last_played_at'], '2026-11-01T11:10:00.000000Z')
        events = self.history.list_events(artist_id=self.artist['id'],
            start='2026-11-01T11:00:00Z', end='2026-11-01T12:00:00Z')
        self.assertEqual([event['suno_song_id'] for event in events], ['a', 'b'])
        self.assertEqual(self.catalog.resolve_artist_alias('α')['id'], self.artist['id'])

    def test_empty_song_and_artist_have_zero_statistics(self):
        stats = self.metrics.song_statistics('a')
        self.assertEqual(stats['started_plays'], 0)
        self.assertEqual(stats['completed_plays'], 0)
        self.assertEqual(stats['airtime_seconds'], 0)
        self.assertIsNone(stats['last_played_at'])
        self.assertIsNone(stats['seconds_since_last_play'])
        artist = self.metrics.artist_statistics(self.empty_artist['id'])
        self.assertEqual(artist['started_plays'], 0)
        self.assertEqual(artist['airtime_seconds'], 0)
        self.assertIsNone(artist['last_played_at'])

    def test_date_range_counts_starts_in_half_open_interval(self):
        self.populate()
        stats = self.metrics.song_statistics('a', start='2026-11-01T10:00:00Z', end='2026-11-01T11:00:00Z')
        self.assertEqual(stats['completed_plays'], 1)
        self.assertEqual(stats['interrupted_plays'], 0)
        self.assertEqual(stats['airtime_seconds'], 120)
        self.assertEqual(len(self.history.list_events(start='2026-11-01T10:00:00Z', end='2026-11-01T11:00:00Z')), 1)
        artist = self.metrics.artist_statistics(self.artist['id'], start='2026-11-01T11:00:00Z', end='2026-11-01T12:00:00Z')
        self.assertEqual(artist['completed_plays'], 1)
        self.assertEqual(artist['airtime_seconds'], 150)
        # Full duration is attributed to the selected start, not clipped at end.
        self.assertEqual(self.metrics.song_statistics('a', end='2026-11-01T10:00:30Z')['airtime_seconds'], 120)

    def test_never_played_and_count_orderings_include_zeroes(self):
        self.populate()
        self.record('a', start='2026-11-02T10:00:00Z')
        self.assertEqual([song['suno_song_id'] for song in self.metrics.songs_never_played()], ['never'])
        self.assertEqual(self.metrics.song_play_counts(order='most_played')[0]['suno_song_id'], 'a')
        self.assertEqual(self.metrics.song_play_counts(order='least_played')[0]['suno_song_id'], 'never')
        self.assertEqual([row['suno_song_id'] for row in self.metrics.song_play_counts(order='least_recent')],
                         ['never', 'b', 'c', 'd', 'a'])
        counts = {row['suno_song_id']: row['completed_plays'] for row in self.metrics.song_play_counts(
            start='2026-11-02T00:00:00Z', end='2026-11-03T00:00:00Z')}
        self.assertEqual(counts, {'a': 1, 'b': 0, 'c': 0, 'd': 0, 'never': 0})

    def test_artist_distribution_includes_unassociated_airtime(self):
        self.populate()
        distribution = self.metrics.artist_airtime_distribution()
        rows = {row['artist_id']: row for row in distribution}
        self.assertEqual(rows[self.artist['id']]['airtime_seconds'], 270)
        self.assertEqual(rows[self.other_artist['id']]['airtime_seconds'], 60)
        self.assertEqual(rows[None]['airtime_seconds'], 10)
        self.assertAlmostEqual(sum(row['airtime_share'] for row in distribution), 1)
        self.assertAlmostEqual(rows[self.artist['id']]['airtime_share'], 270 / 340)
        restricted = {row['artist_id']: row for row in self.metrics.artist_airtime_distribution(end='2026-11-01T11:00:00Z')}
        self.assertEqual(restricted[self.artist['id']]['airtime_seconds'], 120)
        self.assertEqual(restricted[None]['airtime_seconds'], 0)

    def test_styles_and_genres_do_not_multiply_repeated_submissions(self):
        self.populate()
        for genre in ('EDM+', 'EDM+', 'Rock'):
            self.catalog.store_submission('a', 'User', 'original', genre=genre)
        styles = {row['label']: row for row in self.metrics.representation()}
        self.assertEqual(styles['ambient, folk']['song_count'], 2)
        self.assertEqual(styles['ambient, folk']['completed_plays'], 2)
        self.assertEqual(styles['ambient, folk']['airtime_seconds'], 270)
        self.assertEqual(styles[None]['song_count'], 2)
        genres = {row['label']: row for row in self.metrics.representation(metadata='genre')}
        self.assertEqual(genres['EDM+']['song_count'], 1)
        self.assertEqual(genres['EDM+']['completed_plays'], 1)
        self.assertEqual(genres['EDM+']['airtime_seconds'], 150)
        self.assertEqual(genres['Rock']['completed_plays'], 1)
        self.assertEqual(genres[None]['song_count'], 4)
        self.assertEqual(self.catalog.get_song('a')['style'], 'ambient, folk')

    def test_no_zero_denominator_for_empty_airtime(self):
        self.assertTrue(all(row['airtime_share'] == 0 for row in self.metrics.artist_airtime_distribution()))

    def test_persistence_and_schema_initialization_preserve_catalog(self):
        self.populate()
        expected = self.history.list_events()
        database = DatabaseService(self.path)
        catalog = MusicCatalogService(database)
        history = PlaybackHistoryService(database, catalog)
        metrics = PlaybackMetricsService(database, catalog)
        self.assertEqual(history.list_events(), expected)
        self.assertEqual(metrics.artist_statistics(self.artist['id'])['airtime_seconds'], 270)
        self.assertEqual(catalog.get_song('a')['title'], 'Été 🌳 a')
        tables = {row['name'] for row in database.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertEqual(tables, {'songs', 'artists', 'artist_aliases', 'discord_submissions',
            'ingestion_batches', 'ingestion_batch_items', 'youtube_tracks', 'youtube_submissions', 'playback_events'})

    def test_foreign_key_and_outcome_constraints_are_enforced(self):
        for parameters in ((999, 'completed'), (self.catalog.get_song('a')['id'], 'invalid')):
            with self.assertRaises(DatabaseConstraintError):
                self.database.execute('INSERT INTO playback_events(song_id, started_at, outcome) VALUES (?, ?, ?)',
                    (parameters[0], '2026-11-01T00:00:00.000000Z', parameters[1]))
        self.assertEqual(self.history.list_events(), [])

    def test_invalid_inputs_leave_history_unchanged(self):
        for value in ('not a date', '2026-11-01T10:00:00', datetime(2026, 11, 1), 123, None):
            with self.assertRaises(ValueError):
                self.history.record_playback('a', started_at=value)
        with self.assertRaises(ValueError):
            self.record(seconds=-1)
        with self.assertRaises(ValueError):
            self.record(outcome='skipped')
        with self.assertRaises(RecordNotFound):
            self.record('missing')
        with self.assertRaises(RecordNotFound):
            self.history.finish_playback(999)
        self.assertEqual(self.history.list_events(), [])

    def test_invalid_finalize_preserves_pending_event(self):
        event = self.history.start_playback('a', started_at='2026-11-01T10:00:00Z')
        with self.assertRaises(ValueError):
            self.history.finish_playback(event['id'], ended_at='2026-11-01T09:59:59Z')
        self.assertIsNone(self.history.get_event(event['id'])['outcome'])

    def test_missing_metric_records_and_invalid_query_arguments(self):
        for call in (lambda: self.metrics.song_statistics('absent'), lambda: self.metrics.artist_statistics(999),
                     lambda: self.history.list_events(artist_id=999)):
            with self.assertRaises(RecordNotFound):
                call()
        with self.assertRaises(ValueError):
            self.metrics.song_play_counts(order='arbitrary SQL')
        with self.assertRaises(ValueError):
            self.metrics.representation(metadata='unknown')
        with self.assertRaises(ValueError):
            self.history.list_events(outcome='invalid')
        for operation in (self.history.list_events, self.metrics.song_play_counts,
                          self.metrics.artist_airtime_distribution, self.metrics.representation):
            with self.assertRaises(ValueError):
                operation(start='2026-11-02T00:00:00Z', end='2026-11-01T00:00:00Z')
        with self.assertRaises(ValueError):
            self.metrics.song_play_counts(limit=0)

    def test_chronological_pagination_and_outcome_filter(self):
        self.populate()
        all_events = self.history.list_events()
        self.assertEqual(self.history.list_events(limit=2, offset=1), all_events[1:3])
        self.assertEqual(len(self.history.list_events(outcome='completed')), 4)
        self.assertEqual(len(self.history.list_events(outcome='interrupted')), 1)

    def test_concurrent_appends_keep_distinct_events(self):
        with ThreadPoolExecutor(max_workers=4) as workers:
            events = list(workers.map(lambda _: self.record(), range(8)))
        self.assertEqual(len({event['id'] for event in events}), 8)
        self.assertEqual(self.metrics.song_statistics('a')['completed_plays'], 8)
        self.assertEqual(self.metrics.song_statistics('a')['airtime_seconds'], 960)


if __name__ == '__main__':
    unittest.main()
