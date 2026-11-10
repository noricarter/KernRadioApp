"""Exact catalog coverage resolves old groups without rewriting import history."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from radio.catalog_review import import_entry_state, pending_import_review
from radio.ingestion import MusicIngestion
from radio.station_tools import StationTools
from services.catalog import MusicCatalogService
from services.database import DatabaseService
from services.discord_text import DiscordTextParser

A = '12345678-1234-1234-1234-123456789abc'
B = '22222222-2222-2222-2222-222222222222'
GROUP = f'Author A — 11/1/26, 1:00 PM\nTitle: Same title\nGenre: Folk\nhttps://suno.com/song/{A}\nSuno\nSame title\nAuthor B — 11/1/26, 1:01 PM\nTitle: Same title\nGenre: Rock\nhttps://suno.com/song/{B}\nSuno\nSame title'


class ImportResolutionTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.catalog = MusicCatalogService(DatabaseService(self.root / 'catalog.sqlite3'))
        self.batch = self.catalog.create_batch(source_text=GROUP)
        self.item = self.catalog.record_batch_item(self.batch['id'], outcome='failed', source_text=GROUP,
            error='Multiple fields without recognized message boundaries.')
        self.catalog.finish_batch(self.batch['id'])

    def add(self, song_id, url=None):
        self.catalog.get_or_create_song(song_id, url or 'https://suno.com/song/' + song_id, title='Same title')

    def test_importing_group_clears_review_and_preserves_original_history_across_restart(self):
        self.assertEqual(pending_import_review(self.catalog)['total'], 1)
        metadata = Mock()
        metadata.fetch_song.side_effect = lambda song_id: {'suno_song_id': song_id, 'title': 'Same title'}
        result = MusicIngestion(DiscordTextParser(), metadata, self.catalog).import_text(GROUP)
        self.assertEqual(result['counts'], {'added': 2, 'existing': 0, 'failed': 0})
        self.assertEqual(pending_import_review(self.catalog)['total'], 0)
        old = self.catalog.get_batch(self.batch['id'])
        self.assertEqual(old['counts']['failed'], 1)
        self.assertEqual(old['items'][0], self.item)
        restored = MusicCatalogService(DatabaseService(self.root / 'catalog.sqlite3'))
        self.assertEqual(pending_import_review(restored)['total'], 0)
        self.assertEqual(restored.get_batch(self.batch['id'])['counts']['failed'], 1)

    def test_manual_separate_imports_require_every_id_not_just_same_title(self):
        self.add(A)
        state = import_entry_state(self.item, self.catalog)
        self.assertEqual(state['catalog_songs'], 1)
        self.assertFalse(state['resolved'])
        self.assertEqual(pending_import_review(self.catalog)['total'], 1)
        self.add(B)
        self.assertEqual(pending_import_review(self.catalog)['total'], 0)

    def test_non_suno_post_does_not_disappear_when_other_songs_are_present(self):
        self.add(A)
        self.add(B)
        source = 'YouTube Author — 11/1/26, 12:00 PM\nSong Title: Still unsupported\nhttps://youtu.be/example\n' + GROUP
        state = import_entry_state({'source_text': source}, self.catalog)
        self.assertEqual(state['catalog_songs'], 2)
        self.assertEqual(state['unrecognized_posts'], 1)
        self.assertFalse(state['resolved'])

    def test_short_links_only_match_an_exact_known_original_url(self):
        source = 'https://suno.com/s/known'
        self.add(A, url='https://suno.com/s/another')
        self.assertFalse(import_entry_state({'source_text': source}, self.catalog)['resolved'])
        self.add(B, url=source)
        self.assertTrue(import_entry_state({'source_text': source}, self.catalog)['resolved'])

    def test_agent_current_review_and_historical_failures_remain_distinct(self):
        tools = StationTools(Mock(catalog=self.catalog), Mock(), Mock())
        self.assertEqual(tools.dispatch('station_ingestion', {'action': 'unresolved'})['total'], 1)
        self.add(A)
        self.add(B)
        self.assertEqual(tools.dispatch('station_ingestion', {'action': 'unresolved'})['total'], 0)
        self.assertEqual(self.catalog.get_batch(self.batch['id'])['counts']['failed'], 1)

    def test_review_paginates_after_filtering_resolved_groups(self):
        batch = self.catalog.create_batch()
        for _ in range(25):
            self.catalog.record_batch_item(batch['id'], outcome='failed', source_text=GROUP, error='Needs review')
        self.catalog.finish_batch(batch['id'])
        first = pending_import_review(self.catalog, limit=20)
        second = pending_import_review(self.catalog, limit=20, offset=20)
        self.assertEqual(first['total'], 25)
        self.assertEqual(first['batch']['counts']['failed'], 25)
        self.assertEqual(first['batch']['unresolved_count'], 25)
        self.assertEqual(len(first['items']), 20)
        self.assertEqual(len(second['items']), 5)
        self.assertEqual(first['unresolved_total'], 26)

    def test_explicit_resolved_batch_and_invalid_paging(self):
        self.add(A)
        self.add(B)
        page = pending_import_review(self.catalog, batch_id=self.batch['id'])
        self.assertEqual(page['batch']['id'], self.batch['id'])
        self.assertEqual(page['total'], 0)
        for arguments in ({'limit': 21}, {'offset': -1}, {'batch_id': True}):
            with self.assertRaises(ValueError):
                pending_import_review(self.catalog, **arguments)
