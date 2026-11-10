"""Real SQLite tests, isolated in a fresh temporary directory per test."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from services.database import DatabaseConstraintError, DatabaseError, DatabaseService
from services.catalog import AliasConflict, MusicCatalogService, RecordNotFound
from services.catalog.schema import SCHEMA


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'nested/database.sqlite3'
        self.database = DatabaseService(self.path)
        self.database.initialize(('CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY, value TEXT UNIQUE)',))

    def test_schema_reinitialization_and_restart(self):
        result = self.database.execute('INSERT INTO records(value) VALUES (?)', ('original',))
        reopened = DatabaseService(self.path)
        reopened.initialize(('CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY, value TEXT UNIQUE)',))
        self.assertEqual(reopened.fetch_one('SELECT * FROM records WHERE id = ?', (result.lastrowid,)),
                         {'id': result.lastrowid, 'value': 'original'})
        self.assertEqual(reopened.fetch_all('SELECT * FROM records'), [{'id': 1, 'value': 'original'}])
        self.assertIsNone(reopened.fetch_one('SELECT * FROM records WHERE id = -1'))

    def test_transaction_commit_and_rollback(self):
        with self.database.transaction() as transaction:
            transaction.execute('INSERT INTO records(value) VALUES (?)', ('one',))
            transaction.execute('INSERT INTO records(value) VALUES (?)', ('two',))
        with self.assertRaisesRegex(ValueError, 'cancel'):
            with self.database.transaction() as transaction:
                transaction.execute('INSERT INTO records(value) VALUES (?)', ('three',))
                raise ValueError('cancel')
        self.assertEqual(len(self.database.fetch_all('SELECT * FROM records')), 2)

    def test_failed_schema_initialization_is_atomic(self):
        with self.assertRaises(DatabaseError):
            self.database.initialize(('CREATE TABLE temporary_table(id INTEGER)', 'NOT VALID SQL'))
        self.assertIsNone(self.database.fetch_one("SELECT name FROM sqlite_master WHERE name = 'temporary_table'"))

    def test_constraints_roll_back_the_entire_transaction(self):
        with self.assertRaises(DatabaseConstraintError):
            with self.database.transaction() as transaction:
                transaction.execute('INSERT INTO records(value) VALUES (?)', ('duplicate',))
                transaction.execute('INSERT INTO records(value) VALUES (?)', ('duplicate',))
        self.assertEqual(self.database.fetch_all('SELECT * FROM records'), [])

    def test_foreign_keys_enabled_on_every_connection(self):
        self.database.initialize(('CREATE TABLE child(parent_id INTEGER REFERENCES records(id))',))
        for _ in range(2):
            with self.assertRaises(DatabaseConstraintError):
                self.database.execute('INSERT INTO child(parent_id) VALUES (999)')

    def test_named_parameters_and_injection_are_literal(self):
        text = "'); DROP TABLE records; --"
        self.database.execute('INSERT INTO records(value) VALUES (:value)', {'value': text})
        self.assertEqual(self.database.fetch_one('SELECT value FROM records')['value'], text)

    def test_invalid_database_location_raises_service_error(self):
        database = DatabaseService(Path(self.directory.name))
        with self.assertRaises(DatabaseError):
            database.fetch_one('SELECT 1')


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'catalog.sqlite3'
        self.database = DatabaseService(self.path)
        self.catalog = MusicCatalogService(self.database)

    def song(self, song_id='suno-1', **metadata):
        return self.catalog.get_or_create_song(song_id, 'https://suno.com/song/' + song_id, **metadata)[0]

    def test_existing_catalog_migrates_post_dates_without_changing_old_records(self):
        database = DatabaseService(Path(self.directory.name) / 'old.sqlite3')
        old_schema = [statement.replace('        discord_timestamp TEXT,\n', '').replace('        posted_at TEXT,\n', '') for statement in SCHEMA]
        database.initialize(old_schema)
        database.execute('INSERT INTO discord_submissions(suno_song_id, discord_handle, original_message) VALUES (?, ?, ?)',
                         ('song', 'Jїпж [TCBL]', 'untouched original'))
        migrated = MusicCatalogService(database)
        old = migrated.list_submissions()[0]
        self.assertIsNone(old['posted_at'])
        self.assertIsNone(old['discord_timestamp'])
        self.assertEqual(old['original_message'], 'untouched original')
        new = migrated.store_submission('song', 'Author', 'text', discord_timestamp='11/5/26, 1:51 PM', posted_at='2026-11-05T13:51:00-07:00')
        reopened = MusicCatalogService(DatabaseService(database.path))
        self.assertEqual(reopened.list_submissions(), [old, new])
        self.assertNotIn('ingested_at', {row['name'] for row in database.fetch_all('PRAGMA table_info(discord_submissions)')})

    def test_automatic_initialization_and_reinitialization(self):
        names = {row['name'] for row in self.database.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(names, {'songs', 'artists', 'artist_aliases', 'discord_submissions',
                                 'ingestion_batches', 'ingestion_batch_items', 'youtube_tracks', 'youtube_submissions'})
        MusicCatalogService(self.database)
        self.assertEqual(self.catalog.list_songs(), [])

    def test_song_creation_and_retrieval(self):
        song, created = self.catalog.get_or_create_song('suno-1', 'https://suno.com/song/suno-1')
        self.assertTrue(created)
        self.assertIsInstance(song['id'], int)
        self.assertIsNone(song['artist_id'])
        self.assertEqual(self.catalog.get_song('suno-1'), song)
        self.assertIsNone(self.catalog.get_song('absent'))

    def test_suno_id_is_the_only_deduplication_key(self):
        first = self.song(title='Shared title')
        second = self.song('suno-2', title='Shared title')
        duplicate, created = self.catalog.get_or_create_song('suno-1', 'https://different.example/', title='replacement')
        self.assertFalse(created)
        self.assertEqual(duplicate, first)
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(len(self.catalog.list_songs()), 2)
        with self.assertRaises(DatabaseConstraintError):
            self.database.execute('INSERT INTO songs(suno_song_id, original_suno_url) VALUES (?, ?)', ('suno-1', 'url'))

    def test_concurrent_duplicate_inserts(self):
        def insert(_):
            catalog = MusicCatalogService(DatabaseService(self.path))
            return catalog.get_or_create_song('same-id', 'https://suno.com/song/same-id')
        with ThreadPoolExecutor(max_workers=6) as workers:
            results = list(workers.map(insert, range(12)))
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(len({record['id'] for record, _ in results}), 1)
        self.assertEqual(len(self.catalog.list_songs()), 1)

    def test_unicode_and_unusual_names_survive_restart_without_normalization(self):
        title = '  𝕁ØɄℝℕ𝔼𝕐 🦦 e\u0301 / é 日本語 \u200d  '
        artist = self.catalog.create_artist(suno_artist_name=title, discord_handle='用户#1234',
                                           aliases=[title], preferred_display_alias='🎵 雨')
        song = self.song(title=title, lyrics='第一行\nΣτίχοι\n🌳', style='夢 / ambient',
                         suno_username='𝒞𝓇𝑒𝒶𝓉𝑜𝓇', artist_id=artist['id'])
        restored = MusicCatalogService(DatabaseService(self.path))
        self.assertEqual(restored.get_song('suno-1'), song)
        self.assertEqual(restored.get_artist(artist['id']), artist)
        self.assertEqual(restored.resolve_artist_alias(title)['id'], artist['id'])
        self.assertIsNone(restored.resolve_artist_alias(title.strip()))
        # Canonically equivalent Unicode strings are kept distinct.
        other = restored.create_artist(aliases=['e\u0301'])
        third = restored.create_artist(aliases=['é'])
        self.assertNotEqual(other['id'], third['id'])

    def test_metadata_updates_are_partial_and_can_clear_values(self):
        original = self.song(title='Original', lyrics='Keep', suno_username='creator')
        updated = self.catalog.update_song_metadata('suno-1', title='Retrieved', style='jazz')
        self.assertEqual(updated['title'], 'Retrieved')
        self.assertEqual(updated['lyrics'], 'Keep')
        for key in ('id', 'suno_song_id', 'original_suno_url', 'suno_username'):
            self.assertEqual(updated[key], original[key])
        self.assertIsNone(self.catalog.update_song_metadata('suno-1', lyrics=None)['lyrics'])
        self.assertEqual(self.catalog.update_song_metadata('suno-1'), self.catalog.get_song('suno-1'))
        with self.assertRaises(RecordNotFound):
            self.catalog.update_song_metadata('missing', title='Unknown')
        for invalid in ({'id': 23}, {'title': ['invalid']},
                        {'title = NULL; DROP TABLE songs; --': 'x'}):
            with self.assertRaises(ValueError):
                self.catalog.update_song_metadata('suno-1', **invalid)
        with self.assertRaises(TypeError):
            self.catalog.update_song_metadata('suno-1', suno_song_id='other')

    def test_artist_aliases_and_mutable_names(self):
        artist = self.catalog.create_artist(suno_artist_name='Old name', discord_handle='old-handle',
                                           aliases=['Old name', '別名'], preferred_display_alias='Display')
        song = self.song(artist_id=artist['id'])
        self.catalog.add_artist_alias(artist['id'], 'Another alias')
        self.catalog.add_artist_alias(artist['id'], 'Another alias')
        updated = self.catalog.update_artist(artist['id'], suno_artist_name='New name',
                                             discord_handle='new-handle', preferred_display_alias='New display')
        for alias in ('Old name', '別名', 'Display', 'Another alias', 'New display'):
            self.assertEqual(self.catalog.resolve_artist_alias(alias)['id'], artist['id'])
        self.assertEqual(self.catalog.get_song('suno-1')['artist_id'], song['artist_id'])
        self.assertEqual(updated['suno_artist_name'], 'New name')
        self.assertIsNone(self.catalog.update_artist(artist['id'], preferred_display_alias=None)['preferred_display_alias'])
        self.assertIsNone(self.catalog.get_artist(999))
        self.assertIsNone(self.catalog.resolve_artist_alias('not-known'))

    def test_alias_conflicts_do_not_merge_or_partially_create_artists(self):
        one = self.catalog.create_artist(aliases=['Taken'])
        with self.assertRaises(AliasConflict):
            self.catalog.create_artist(aliases=['new-alias', 'Taken'])
        self.assertEqual(len(self.catalog.list_artists()), 1)
        self.assertIsNone(self.catalog.resolve_artist_alias('new-alias'))
        two = self.catalog.create_artist(suno_artist_name='Second')
        with self.assertRaises(AliasConflict):
            self.catalog.update_artist(two['id'], suno_artist_name='Changed', preferred_display_alias='Taken')
        self.assertEqual(self.catalog.get_artist(two['id'])['suno_artist_name'], 'Second')
        with self.assertRaises(AliasConflict):
            self.catalog.add_artist_alias(two['id'], 'Taken')
        self.assertEqual(self.catalog.resolve_artist_alias('Taken')['id'], one['id'])

    def test_artist_names_are_not_unique_identity_keys(self):
        one = self.catalog.create_artist(suno_artist_name='Shared', discord_handle='Shared')
        two = self.catalog.create_artist(suno_artist_name='Shared', discord_handle='Shared')
        self.assertNotEqual(one['id'], two['id'])
        self.assertIsNone(self.catalog.resolve_artist_alias('Shared'))

    def test_artist_association_and_constraints(self):
        self.song()
        artist = self.catalog.create_artist(aliases=['artist'])
        self.assertEqual(self.catalog.associate_song('suno-1', artist['id'])['artist_id'], artist['id'])
        with self.assertRaises(RecordNotFound):
            self.catalog.associate_song('suno-1', 999)
        with self.assertRaises(DatabaseConstraintError):
            self.song('invalid-artist', artist_id=999)
        with self.assertRaises(RecordNotFound):
            self.catalog.associate_song('missing', artist['id'])
        self.assertEqual(self.catalog.list_songs(artist_id=artist['id'])[0]['suno_song_id'], 'suno-1')
        self.assertIsNone(self.catalog.associate_song('suno-1', None)['artist_id'])
        self.assertEqual(self.catalog.list_songs(artist_id=artist['id']), [])

    def test_discord_attribution_is_distinct_and_preserved_for_unretrieved_songs(self):
        song = self.song(suno_username='Suno Creator')
        batch = self.catalog.create_batch(source_text='Copied batch')
        message = '  🌳 submitted “e\u0301”\nhttps://suno.com/song/suno-1\nCW: loud sounds  '
        submission = self.catalog.store_submission('suno-1', 'Discord Submitter', message,
            submitted_title='Submitted title', genre='🌙', description='A description',
            content_warning='loud sounds', batch_id=batch['id'])
        self.catalog.store_submission('suno-1', 'Another person', 'Same song, separate submission')
        unknown = self.catalog.store_submission('not-yet-retrieved', 'Third person', 'Original text')
        reopened = MusicCatalogService(DatabaseService(self.path))
        self.assertEqual(reopened.list_submissions(batch_id=batch['id']), [submission])
        self.assertEqual(submission['original_message'], message)
        self.assertEqual(len(reopened.list_submissions(suno_song_id='suno-1')), 2)
        self.assertEqual(reopened.list_submissions(suno_song_id='not-yet-retrieved'), [unknown])
        self.assertEqual(reopened.get_song('suno-1'), song)
        self.assertIsNone(reopened.get_song('not-yet-retrieved'))
        with self.assertRaises(DatabaseConstraintError):
            self.catalog.store_submission('suno-1', 'handle', 'text', batch_id=999)

    def test_batch_outcomes_failed_ids_and_restart(self):
        self.song()
        batch = self.catalog.create_batch(source_text='raw input 🌿')
        self.catalog.record_batch_item(batch['id'], outcome='added', suno_song_id='suno-1')
        self.catalog.record_batch_item(batch['id'], outcome='existing', suno_song_id='suno-1')
        self.catalog.record_batch_item(batch['id'], outcome='failed', suno_song_id='unavailable', error='Could not retrieve')
        self.catalog.record_batch_item(batch['id'], outcome='failed', source_text='bad input', error='No song ID')
        completed = self.catalog.finish_batch(batch['id'])
        self.assertIsNotNone(completed['completed_at'])
        self.assertEqual(completed['counts'], {'added': 1, 'existing': 1, 'failed': 2})
        self.assertEqual(self.catalog.finish_batch(batch['id']), completed)
        reopened = MusicCatalogService(DatabaseService(self.path))
        self.assertEqual(reopened.get_batch(batch['id']), completed)
        self.assertEqual(reopened.list_batches()[0]['source_text'], 'raw input 🌿')
        with self.assertRaises(ValueError):
            reopened.record_batch_item(batch['id'], outcome='failed', error='Too late')
        self.assertIsNone(reopened.get_batch(999))

    def test_invalid_batch_outcomes(self):
        batch = self.catalog.create_batch()
        for invalid in ({'outcome': 'unknown'}, {'outcome': 'failed'}, {'outcome': 'failed', 'error': '  '}):
            with self.assertRaises(ValueError):
                self.catalog.record_batch_item(batch['id'], **invalid)
        for missing in (None, 'not-in-catalog'):
            with self.assertRaises(RecordNotFound):
                self.catalog.record_batch_item(batch['id'], outcome='added', suno_song_id=missing)
        with self.assertRaises(RecordNotFound):
            self.catalog.record_batch_item(999, outcome='failed', error='No batch')
        with self.assertRaises(RecordNotFound):
            self.catalog.finish_batch(999)
        self.assertEqual(self.catalog.get_batch(batch['id'])['items'], [])

    def test_required_text_and_alias_validation(self):
        for value in ('', '  ', None, 123):
            with self.assertRaises(ValueError):
                self.catalog.get_or_create_song(value, 'url')
            with self.assertRaises(ValueError):
                self.catalog.get_or_create_song('id', value)
            with self.assertRaises(ValueError):
                self.catalog.create_artist(aliases=[value])
        with self.assertRaises(ValueError):
            self.catalog.create_artist(aliases='one-string')
        with self.assertRaises(RecordNotFound):
            self.catalog.add_artist_alias(999, 'name')
        self.assertEqual(self.catalog.list_artists(), [])
        self.assertEqual(self.catalog.list_songs(), [])

    def test_stable_pagination_and_invalid_limits(self):
        first = self.song()
        second = self.song('suno-2')
        self.assertEqual(self.catalog.list_songs(limit=1), [first])
        self.assertEqual(self.catalog.list_songs(limit=1, offset=1), [second])
        self.assertEqual(self.catalog.list_songs(offset=2), [])
        for operation in (self.catalog.list_songs, self.catalog.list_artists,
                          self.catalog.list_submissions, self.catalog.list_batches):
            for options in ({'limit': 0}, {'offset': -1}, {'limit': True}, {'offset': 1.5}):
                with self.assertRaises(ValueError):
                    operation(**options)


if __name__ == '__main__':
    unittest.main()
