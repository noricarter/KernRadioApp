"""Original-image caching and catalog backfill using isolated storage/fake HTTP."""
import base64
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, Mock
from urllib.error import URLError

from radio.artwork import CatalogArtwork
from radio.ingestion import MusicIngestion
from radio.management import StationManagement
from radio.web import create_app
from services.artwork import ArtworkError, ArtworkService, _ArtworkRedirects
from services.catalog import MusicCatalogService
from services.database import DatabaseService
from services.discord_text import DiscordTextParser

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXioAAAAASUVORK5CYII=')
URL = 'https://cdn2.suno.ai/example.png'
ID = '12345678-1234-1234-1234-123456789abc'


class ArtworkTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.artwork = ArtworkService(self.root / 'artwork')
        self.response = Mock()
        self.response.geturl.return_value = URL
        self.response.read.return_value = PNG
        self.artwork._opener = MagicMock()
        self.artwork._opener.open.return_value.__enter__.return_value = self.response
        self.catalog = MusicCatalogService(DatabaseService(self.root / 'catalog.sqlite3'))
        self.metadata = Mock()
        self.metadata.fetch_song.return_value = {'suno_song_id': ID, 'artwork_url': URL, 'title': 'Remote title'}
        self.composition = CatalogArtwork(self.catalog, self.metadata, self.artwork)

    def test_original_bytes_cached_and_reused_without_network(self):
        filename = self.artwork.fetch(URL)
        self.assertEqual(self.artwork.path(filename).read_bytes(), PNG)
        self.assertTrue(filename.endswith('.png'))
        self.response.read.assert_called_once_with(10_000_001)
        self.assertEqual(self.artwork.fetch(URL), filename)
        self.artwork._opener.open.assert_called_once()
        self.assertEqual(ArtworkService(self.artwork.directory).path(filename).read_bytes(), PNG)

    def test_bad_urls_paths_and_redirects_are_rejected(self):
        for url in ('http://cdn2.suno.ai/test.png', 'https://localhost/test.png',
                    'https://cdn2.suno.ai.evil/test.png', 'https://cdn2.suno.ai:bad/a',
                    'https://user:secret@cdn2.suno.ai/a'):
            with self.assertRaises(ArtworkError):
                self.artwork.fetch(url)
        for filename in ('../../secret', '/tmp/image.png', 'file.svg', None):
            self.assertIsNone(self.artwork.path(filename))
        with self.assertRaises(ArtworkError):
            _ArtworkRedirects().redirect_request(None, None, 302, '', {}, 'https://localhost/private')
        self.artwork._opener.open.assert_not_called()

    def test_invalid_images_oversize_and_network_failure_do_not_cache(self):
        for data in (b'<html>Not an image</html>', b'x' * 10_000_001):
            self.response.read.return_value = data
            with self.assertRaises(ArtworkError):
                self.artwork.fetch(URL)
        self.artwork._opener.open.side_effect = URLError('offline')
        with self.assertRaises(ArtworkError):
            self.artwork.fetch(URL)
        self.assertFalse(self.artwork.directory.exists())

    def test_backfill_only_updates_artwork_preserves_edits_and_skips_cached_songs(self):
        artist = self.catalog.create_artist(preferred_display_alias='Chosen identity 🌲')
        self.catalog.get_or_create_song(ID, 'original-url', title='Edited title', lyrics='Edited lyrics',
            style='Edited style', suno_username='Original creator', artist_id=artist['id'])
        before = self.catalog.get_song(ID)
        result = self.composition.backfill()
        self.assertEqual(result, {'saved': 1, 'existing': 0, 'unavailable': 0, 'failed': []})
        after = self.catalog.get_song(ID)
        self.assertEqual({key: after[key] for key in before if not key.startswith('artwork_')},
                         {key: value for key, value in before.items() if not key.startswith('artwork_')})
        self.assertEqual(after['artwork_url'], URL)
        self.assertEqual(self.artwork.path(after['artwork_file']).read_bytes(), PNG)
        self.assertEqual(self.composition.backfill()['existing'], 1)
        self.metadata.fetch_song.assert_called_once_with(ID)
        self.artwork._opener.open.assert_called_once()

    def test_known_artwork_url_needs_no_metadata_lookup(self):
        self.catalog.get_or_create_song(ID, 'url', artwork_url=URL)
        self.assertEqual(self.composition.backfill()['saved'], 1)
        self.metadata.fetch_song.assert_not_called()

    def test_unavailable_or_failed_artwork_is_reported_without_fabrication(self):
        self.catalog.get_or_create_song(ID, 'url')
        self.metadata.fetch_song.return_value = {'suno_song_id': ID, 'artwork_url': None}
        self.assertEqual(self.composition.backfill()['unavailable'], 1)
        self.assertIsNone(self.catalog.get_song(ID)['artwork_file'])
        self.metadata.fetch_song.return_value = {'suno_song_id': ID, 'artwork_url': URL}
        self.artwork._opener.open.side_effect = URLError('offline')
        result = self.composition.backfill()
        self.assertEqual(len(result['failed']), 1)
        self.assertEqual(result['saved'], 0)
        self.assertIsNone(self.catalog.get_song(ID)['artwork_file'])

    def test_import_caches_new_song_artwork_once_and_image_failure_keeps_song(self):
        self.metadata.fetch_song.return_value |= {'lyrics': None, 'style': 'Ambient', 'suno_username': 'Creator'}
        ingestion = MusicIngestion(DiscordTextParser(), self.metadata, self.catalog, artwork=self.composition)
        result = ingestion.import_text('https://suno.com/song/' + ID)
        self.assertEqual(result['counts']['added'], 1)
        self.assertIsNotNone(self.catalog.get_song(ID)['artwork_file'])
        ingestion.import_text('https://suno.com/song/' + ID)
        self.metadata.fetch_song.assert_called_once()
        self.artwork._opener.open.assert_called_once()
        other = '22222222-2222-2222-2222-222222222222'
        self.metadata.fetch_song.return_value |= {'suno_song_id': other, 'artwork_url': URL + '?different'}
        self.artwork._opener.open.side_effect = URLError('offline')
        result = ingestion.import_text('https://suno.com/song/' + other)
        self.assertEqual(result['counts']['added'], 1)
        self.assertTrue(result['entries'][0]['warnings'])
        self.assertIsNone(self.catalog.get_song(other)['artwork_file'])

    def test_old_catalog_migration_preserves_existing_records_and_artwork_survives_restart(self):
        database = DatabaseService(self.root / 'old.sqlite3')
        database.initialize(('''CREATE TABLE songs(id INTEGER PRIMARY KEY, suno_song_id TEXT UNIQUE,
            original_suno_url TEXT, title TEXT, lyrics TEXT, style TEXT, suno_username TEXT, artist_id INTEGER)''',))
        database.execute("INSERT INTO songs VALUES (1, ?, 'original', 'Old title 🌙', 'Lyrics', 'Style', 'Creator', NULL)", (ID,))
        catalog = MusicCatalogService(database)
        self.assertEqual(catalog.get_song(ID)['title'], 'Old title 🌙')
        self.assertIsNone(catalog.get_song(ID)['artwork_file'])
        catalog.update_song_metadata(ID, artwork_url=URL, artwork_file=self.artwork.fetch(URL))
        restarted = MusicCatalogService(DatabaseService(database.path))
        self.assertEqual(restarted.get_song(ID), catalog.get_song(ID))

    def test_local_http_artwork_route_serves_original_and_rejects_missing_files(self):
        self.catalog.get_or_create_song(ID, 'url', artwork_url=URL)
        self.composition.cache_song(ID)
        information = Mock()
        information.metrics.song_statistics.return_value = {}
        manager = StationManagement(Mock(), self.catalog, information, Mock(), artwork=self.artwork)
        client = create_app(Mock(), management=manager).test_client()
        detail = client.get('/api/catalog/' + ID).json
        self.assertEqual(detail['artwork_url'], '/api/catalog/' + ID + '/artwork')
        with client.get(detail['artwork_url']) as response:
            self.assertEqual(response.data, PNG)
            self.assertEqual(response.mimetype, 'image/png')
        self.assertEqual(client.get('/api/catalog/unknown/artwork').status_code, 404)
