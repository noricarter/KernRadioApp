"""Focused checks for role cleanup and accepting supplied YouTube metadata."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from radio.author_cleanup import clean_role_names
from radio.catalog_review import pending_import_review
from radio.ingestion import MusicIngestion
from radio.management import StationManagement
from radio.web import create_app
from services.catalog import MusicCatalogService
from services.database import DatabaseService
from services.discord_text import DiscordTextParser
from services.playback import PlaybackHistoryService, PlaybackMetricsService
from services.station_information import StationInformationService

VIDEO = 'EhNeWvGeZQw'


class YouTubeIngestionTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = DatabaseService(self.root / 'catalog.sqlite3')
        self.catalog = MusicCatalogService(self.database)
        self.parser = DiscordTextParser()
        self.metadata = Mock()
        self.ingestion = MusicIngestion(self.parser, self.metadata, self.catalog)

    def test_role_badges_are_removed_from_all_observed_author_variants(self):
        for author in ('SeraphymRole icon, Candy Gremlin 2026', 'Seraphym, Role icon, Winner',
                       'Seraphym, role icon, Winner', 'Seraphym, ROLE ICON, Winner'):
            post = self.parser.parse(f'{author} — 11/1/26, 1:00 PM\nhttps://youtu.be/{VIDEO}').submissions[0]
            self.assertEqual(post.discord_handle, 'Seraphym')
            self.assertEqual(post.preferred_artist_name, 'Seraphym')

    def test_existing_role_names_aliases_and_attribution_are_cleaned_without_changing_identity(self):
        name = 'NósRole icon, The Forged'
        artist = self.catalog.create_artist(preferred_display_alias=name, discord_handle=name)
        self.catalog.get_or_create_song('song', 'url', artist_id=artist['id'])
        submission = self.catalog.store_submission('song', name, 'Original text includes ' + name)
        self.assertEqual(clean_role_names(self.catalog), [artist['id']])
        fixed = self.catalog.get_artist(artist['id'])
        self.assertEqual(fixed['preferred_display_alias'], 'Nós')
        self.assertEqual(fixed['discord_handle'], 'Nós')
        self.assertEqual(fixed['aliases'], ['Nós'])
        self.assertEqual(self.catalog.get_song('song')['artist_id'], artist['id'])
        restored = self.catalog.list_submissions()[0]
        self.assertEqual(restored['discord_handle'], 'Nós')
        self.assertEqual(restored['original_message'], submission['original_message'])
        self.assertEqual(clean_role_names(self.catalog), [])

    def test_actual_youtube_post_preserves_available_fields_without_suno_lookup(self):
        text = Path('tests/fixtures/crucible_grouped_posts.txt').read_text()
        post = next(post for post in self.parser.parse(text).submissions if post.source == 'youtube')
        result = self.ingestion.import_text(post.original_message)
        self.assertEqual(result['counts'], {'added': 1, 'existing': 0, 'failed': 0})
        track = self.catalog.get_youtube_track(VIDEO)
        self.assertEqual(track['title'], "Don't Make Us Face This Sober Tonight")
        self.assertIn('Industrial Pop Metal', track['genre'])
        self.assertIn('Four astronauts', track['description'])
        self.assertNotIn('YouTube\nDigital Pyre', track['description'])
        self.assertIn('Drug references', track['content_warning'])
        self.assertEqual(self.catalog.list_songs(), [])
        stored = self.catalog.list_youtube_submissions(VIDEO)[0]
        self.assertEqual(stored['original_message'], post.original_message)
        self.assertEqual(stored['discord_handle'], post.discord_handle)
        self.metadata.fetch_song.assert_not_called()
        self.metadata.resolve_song_id.assert_not_called()
        self.assertEqual(result['entries'][0]['missing_metadata'], [])
        item = self.catalog.get_batch(result['batch_id'])['items'][0]
        self.assertIsNone(item['suno_song_id'])
        self.assertEqual(item['youtube_video_id'], VIDEO)

    def test_link_only_is_accepted_and_video_id_deduplicates_url_forms(self):
        result = self.ingestion.import_text(f'https://youtu.be/{VIDEO}\n\nhttps://www.youtube.com/watch?v={VIDEO}&t=30')
        self.assertEqual(result['counts'], {'added': 1, 'existing': 0, 'failed': 0})
        track = self.catalog.get_youtube_track(VIDEO)
        self.assertIsNone(track['title'])
        self.assertIsNone(track['artist_id'])
        self.assertEqual(len(self.catalog.list_youtube_submissions(VIDEO)), 2)
        self.assertEqual(self.ingestion.import_text('https://youtu.be/' + VIDEO)['counts']['existing'], 1)
        restarted = MusicCatalogService(DatabaseService(self.database.path))
        self.assertEqual(restarted.get_youtube_track(VIDEO), track)
        self.assertEqual(len(restarted.list_youtube_submissions(VIDEO)), 3)

    def test_youtube_acceptance_clears_previous_unresolved_result_and_is_visible_via_http(self):
        source = f'AuthorRole icon, Winner — 11/1/26, 1:00 PM\nSong Title: Example\nhttps://youtu.be/{VIDEO}'
        batch = self.catalog.create_batch()
        self.catalog.record_batch_item(batch['id'], outcome='failed', source_text=source, error='No Suno song link was found')
        self.catalog.finish_batch(batch['id'])
        self.assertEqual(pending_import_review(self.catalog)['total'], 1)
        self.ingestion.import_text(source)
        self.assertEqual(pending_import_review(self.catalog)['total'], 0)
        self.assertEqual(self.catalog.get_batch(batch['id'])['counts']['failed'], 1)
        history = PlaybackHistoryService(self.database, self.catalog)
        information = StationInformationService(self.database, self.catalog, history, PlaybackMetricsService(self.database, self.catalog))
        management = StationManagement(Mock(), self.catalog, information, Mock())
        client = create_app(Mock(), management=management).test_client()
        page = client.get('/api/youtube').json
        self.assertEqual(page['total'], 1)
        detail = client.get('/api/youtube/' + VIDEO).json
        self.assertEqual(detail['track']['title'], 'Example')
        self.assertNotIn('field_issues', detail)
        management.review_catalog()  # No Suno metadata/artist requirement for YouTube.

    def test_suno_only_batch_schema_migrates_without_losing_failed_history(self):
        database = DatabaseService(self.root / 'old.sqlite3')
        database.initialize((
            'CREATE TABLE ingestion_batches(id INTEGER PRIMARY KEY,source_text TEXT,created_at TEXT,completed_at TEXT)',
            "CREATE TABLE ingestion_batch_items(id INTEGER PRIMARY KEY,batch_id INTEGER NOT NULL,suno_song_id TEXT,outcome TEXT,source_text TEXT,error TEXT,CHECK(outcome='failed' OR suno_song_id IS NOT NULL))",
        ))
        database.execute("INSERT INTO ingestion_batches VALUES(1,'Raw source','Old time','Done')")
        database.execute("INSERT INTO ingestion_batch_items VALUES(42,1,NULL,'failed','Original raw text','Needs attention')")
        catalog = MusicCatalogService(database)
        item = catalog.get_batch_item(42)
        self.assertEqual(item['source_text'], 'Original raw text')
        self.assertIsNone(item['youtube_video_id'])
        self.assertEqual(catalog.get_batch(1)['counts']['failed'], 1)
        track, created = catalog.get_or_create_youtube_track(VIDEO, 'https://youtu.be/' + VIDEO)
        batch = catalog.create_batch()
        catalog.record_batch_item(batch['id'], outcome='added', youtube_video_id=VIDEO)
        self.assertTrue(created)
        self.assertEqual(catalog.finish_batch(batch['id'])['counts']['added'], 1)
