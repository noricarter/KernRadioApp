"""Review diagnostics and actual HTTP data with isolated SQLite."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from radio.catalog_review import import_entry_review, song_field_issues
from radio.management import StationManagement
from radio.web import create_app
from services.catalog import MusicCatalogService
from services.database import DatabaseService
from services.playback import PlaybackHistoryService, PlaybackMetricsService
from services.station_information import StationInformationService


class CatalogReviewTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = DatabaseService(self.root / 'catalog.sqlite3')
        self.catalog = MusicCatalogService(self.database)
        history = PlaybackHistoryService(self.database, self.catalog)
        information = StationInformationService(self.database, self.catalog, history, PlaybackMetricsService(self.database, self.catalog))
        self.manager = StationManagement(Mock(), self.catalog, information, Mock())
        self.client = create_app(Mock(), management=self.manager).test_client()

    def failures(self):
        batch = self.catalog.create_batch()
        for index in range(19):
            self.catalog.record_batch_item(batch['id'], outcome='failed', source_text=f'Title: 𝓐 {index}\nGenre: Folk\nTitle: 風\nGenre: Rock',
                error='Multiple sets of post fields were found without recognized message boundaries.')
        self.catalog.finish_batch(batch['id'])
        return batch

    def test_all_nineteen_results_appear_separately_from_imported_songs(self):
        batch = self.failures()
        self.catalog.get_or_create_song('known', 'url', title='Imported song')
        data = self.client.get('/api/import-review').json
        self.assertEqual(data['total'], 19)
        self.assertEqual(data['batch']['id'], batch['id'])
        self.assertEqual(len(data['items']), 19)
        self.assertEqual(self.client.get('/api/catalog').json['total'], 1)
        item = self.client.get('/api/import-review/' + str(data['items'][0]['id'])).json
        self.assertTrue(any(part['questioned'] for part in item['source_parts']))
        self.assertEqual({field['field'] for field in item['questioned_fields']}, {'submitted_title', 'genre'})
        self.assertEqual(''.join(part['text'] for part in item['source_parts']), item['source_text'])

    def test_review_history_remains_accessible_after_new_successful_batch_and_restart(self):
        first = self.failures()
        later = self.catalog.create_batch()
        self.catalog.finish_batch(later['id'])
        restored = MusicCatalogService(DatabaseService(self.database.path))
        self.assertEqual(restored.get_batch(first['id'])['counts']['failed'], 19)
        self.assertEqual(self.manager.import_review()['batch']['id'], first['id'])

    def test_only_repeated_fields_are_flagged_and_original_unicode_is_exact(self):
        source = 'Author 🌲\nTitle: e\u0301\nGenre: Folk\nTitle: 風\nContent Warning: None'
        review = import_entry_review({'source_text': source, 'error': 'Unrecognized message boundaries.'})
        self.assertEqual([field['field'] for field in review['questioned_fields']], ['submitted_title'])
        self.assertEqual(''.join(part['text'] for part in review['source_parts']), source)
        self.assertTrue(all(part.get('label') == 'Title' for part in review['source_parts'] if part['questioned']))

    def test_failed_link_is_highlighted_without_inventing_metadata(self):
        source = 'Post 🌲 https://suno.com/s/example'
        review = import_entry_review({'source_text': source, 'error': 'Share link unavailable'})
        self.assertEqual([part['text'] for part in review['source_parts'] if part['questioned']], ['https://suno.com/s/example'])
        self.assertEqual(review['questioned_fields'][0]['field'], 'original_suno_url')

    def test_song_field_markers_follow_persisted_corrections(self):
        self.catalog.get_or_create_song('song', 'url')
        before = self.manager.song_detail('song')['field_issues']
        self.assertTrue({'title', 'artist_id', 'preferred_display_alias'} <= before.keys())
        self.assertNotIn('aliases', before)
        saved = self.manager.save_song('song', {'metadata': {'title': 'Corrected', 'style': 'Folk', 'lyrics': 'Words', 'suno_username': 'Creator'},
            'artist': {'preferred_display_alias': 'Jїпж', 'aliases': []}})
        self.assertEqual(saved['field_issues'], {})
        self.assertEqual(self.manager.catalog_page()['items'][0]['field_issues'], {})

    def test_empty_and_invalid_review_requests(self):
        self.assertEqual(self.client.get('/api/import-review').json['total'], 0)
        self.assertEqual(self.client.get('/api/import-review/999').status_code, 404)
        self.assertEqual(self.client.get('/api/import-review?offset=-1').status_code, 400)
