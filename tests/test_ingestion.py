"""Application orchestration with real temporary SQLite and fake Suno retrieval."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from radio.ingestion import MusicIngestion
from radio.web import create_app
from services.catalog import MusicCatalogService
from services.database import DatabaseService
from services.discord_text import DiscordTextParser
from services.suno import SunoMetadataError

A = '12345678-1234-1234-1234-123456789abc'
B = '22222222-2222-2222-2222-222222222222'


class IngestionTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.catalog = MusicCatalogService(DatabaseService(Path(self.directory.name) / 'catalog.sqlite3'))
        self.metadata = Mock()
        self.metadata.fetch_song.side_effect = lambda value: {
            'suno_song_id': value, 'title': 'Retrieved 風',
            'lyrics': None, 'style': 'ambient', 'suno_username': 'Suno creator',
        }
        self.ingestion = MusicIngestion(DiscordTextParser(), self.metadata, self.catalog)

    def test_unique_ids_duplicates_and_reimport_skip_retrieval(self):
        text = f'Title: Submitted title\nGenre: rock\nhttps://suno.com/song/{A}\nhttps://suno.com/song/{B}\n\nhttps://suno.com/song/{A}'
        result = self.ingestion.import_text(text)
        self.assertEqual(result['counts'], {'added': 2, 'existing': 0, 'failed': 0})
        self.assertEqual(self.metadata.fetch_song.call_count, 2)
        self.assertEqual(len(self.catalog.list_submissions()), 3)
        song = self.catalog.get_song(A)
        self.assertEqual(song['title'], 'Retrieved 風')
        self.assertIsNone(song['artist_id'])
        self.assertEqual(self.catalog.list_artists(), [])
        submission = self.catalog.list_submissions(suno_song_id=A)[0]
        self.assertEqual(submission['submitted_title'], 'Submitted title')
        self.assertEqual(submission['discord_handle'], '')
        self.assertEqual(result['entries'][0]['missing_metadata'], ['lyrics'])
        self.assertTrue(result['entries'][0]['warnings'])
        repeated = self.ingestion.import_text(text)
        self.assertEqual(repeated['counts'], {'added': 0, 'existing': 2, 'failed': 0})
        self.assertEqual(self.metadata.fetch_song.call_count, 2)

    def test_one_lookup_failure_does_not_discard_other_songs_or_submission(self):
        fetch = self.metadata.fetch_song.side_effect
        self.metadata.fetch_song.side_effect = lambda value: (_ for _ in ()).throw(SunoMetadataError('Unavailable')) if B in value else fetch(value)
        result = self.ingestion.import_text(f'https://suno.com/song/{A}\nhttps://suno.com/song/{B}')
        self.assertEqual(result['counts'], {'added': 1, 'existing': 0, 'failed': 1})
        self.assertIsNone(self.catalog.get_song(B))
        self.assertEqual(len(self.catalog.list_submissions(suno_song_id=B)), 1)
        batch = self.catalog.get_batch(result['batch_id'])
        self.assertIsNotNone(batch['completed_at'])
        self.assertEqual(batch['items'][1]['error'], 'Unavailable')

    def test_unrecognized_text_and_unsupported_link_are_saved(self):
        text = 'Original unknown text\n\nhttps://suno.com/playlist/other'
        result = self.ingestion.import_text(text)
        self.assertEqual(result['counts']['failed'], 2)
        self.assertEqual(self.catalog.get_batch(result['batch_id'])['source_text'], text)
        self.metadata.fetch_song.assert_not_called()

    def test_mismatched_metadata_is_not_inserted(self):
        self.metadata.fetch_song.side_effect = None
        self.metadata.fetch_song.return_value = {'suno_song_id': B}
        result = self.ingestion.import_text('https://suno.com/song/' + A)
        self.assertEqual(result['counts']['failed'], 1)
        self.assertEqual(self.catalog.list_songs(), [])

    def test_blank_oversized_and_busy_inputs(self):
        for value in (None, '', 'a' * 100001):
            with self.assertRaises(ValueError):
                self.ingestion.import_text(value)
        self.ingestion._lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError, 'already running'):
                self.ingestion.import_text('hello')
        finally:
            self.ingestion._lock.release()

    def test_http_import_boundary_and_results(self):
        client = create_app(Mock(), self.ingestion).test_client()
        page = client.get('/').text
        self.assertIn('Copied Discord messages', page)
        token = re.search('name="app-token" content="([^"]+)"', page)[1]
        headers = {'X-App-Token': token}
        self.assertEqual(client.post('/api/import', json={'text': 'x'}).status_code, 403)
        self.assertEqual(client.post('/api/import', headers=headers, json=[]).status_code, 400)
        result = client.post('/api/import', headers=headers, json={'text': 'https://suno.com/song/' + A})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['counts']['added'], 1)
        self.assertEqual(len(self.catalog.list_songs()), 1)

    def test_resolved_share_urls_skip_all_network_on_reimport(self):
        self.metadata.resolve_song_id.return_value = A
        text = 'https://suno.com/s/sharecode'
        first = self.ingestion.import_text(text)
        self.assertEqual(first['counts']['added'], 1)
        self.assertEqual(self.catalog.get_song(A)['original_suno_url'], text)
        self.assertEqual(self.catalog.get_song_by_url(text)['suno_song_id'], A)
        self.metadata.resolve_song_id.reset_mock()
        self.metadata.fetch_song.reset_mock()
        second = self.ingestion.import_text(text)
        self.assertEqual(second['counts']['existing'], 1)
        self.metadata.resolve_song_id.assert_not_called()
        self.metadata.fetch_song.assert_not_called()

    def test_failed_share_resolution_is_reported_once_without_fetch(self):
        self.metadata.resolve_song_id.side_effect = SunoMetadataError('Share link unavailable')
        result = self.ingestion.import_text('https://suno.com/s/share\n\nhttps://suno.com/s/share')
        self.assertEqual(result['counts']['failed'], 1)
        self.metadata.fetch_song.assert_not_called()
        self.metadata.resolve_song_id.assert_called_once()

    def test_default_discord_artist_and_post_date_are_persisted(self):
        reference = datetime(2026, 11, 8, 0, 1, tzinfo=ZoneInfo('America/Phoenix'))
        clock = Mock(return_value=reference)
        ingestion = MusicIngestion(DiscordTextParser(), self.metadata, self.catalog, clock=clock)
        text = f'Jїпж [TCBL], Role icon, The Forged — Yesterday at 7:51 PM\nhttps://suno.com/song/{A}'
        result = ingestion.import_text(text)
        clock.assert_called_once()
        self.assertEqual(result['counts']['added'], 1)
        artist = self.catalog.get_artist(self.catalog.get_song(A)['artist_id'])
        self.assertEqual(artist['preferred_display_alias'], 'Jїпж')
        self.assertEqual(artist['discord_handle'], 'Jїпж [TCBL]')
        self.assertIn('Jїпж [TCBL]', artist['aliases'])
        post = self.catalog.list_submissions()[0]
        self.assertEqual(post['posted_at'], '2026-11-07T19:51:00-07:00')
        self.assertEqual(post['discord_timestamp'], 'Yesterday at 7:51 PM')
        self.assertEqual(post['original_message'], text)
        self.assertNotIn('ingested_at', post)
        # Tag changes resolve through the clean alias and keep the stable ID.
        ingestion.import_text(text.replace('[TCBL]', '[OTHER]'))
        self.assertEqual(len(self.catalog.list_artists()), 1)
        self.assertEqual(self.catalog.get_song(A)['artist_id'], artist['id'])

    def test_reimport_fills_missing_artist_but_preserves_explicit_artist_choice(self):
        self.ingestion.import_text('https://suno.com/song/' + A)
        self.assertIsNone(self.catalog.get_song(A)['artist_id'])
        self.ingestion.import_text(f'Snotegel [ᛕᛟᛏᚺ],  — 11/5/26, 1:51 PM\nhttps://suno.com/song/{A}')
        artist_id = self.catalog.get_song(A)['artist_id']
        self.assertEqual(self.catalog.get_artist(artist_id)['preferred_display_alias'], 'Snotegel')
        self.catalog.update_artist(artist_id, preferred_display_alias='Chosen on-air name')
        self.ingestion.import_text(f'Other person — 1:00 PM\nhttps://suno.com/song/{A}')
        self.assertEqual(self.catalog.get_song(A)['artist_id'], artist_id)
        self.assertEqual(self.catalog.get_artist(artist_id)['preferred_display_alias'], 'Chosen on-air name')
        self.assertEqual(len(self.catalog.list_artists()), 1)
