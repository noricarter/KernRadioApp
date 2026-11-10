"""Management operations and thin HTTP boundaries with real isolated SQLite."""
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from radio.controller import ApplicationController
from radio.management import StationManagement
from radio.web import create_app
from services.catalog import AliasConflict, MusicCatalogService
from services.configuration import ConfigurationService
from services.database import DatabaseService
from services.playback import PlaybackHistoryService, PlaybackMetricsService
from services.policies import OperatingPolicyService, PolicyConflict
from services.station_information import StationInformationService


class ManagementTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = DatabaseService(self.root / 'catalog.sqlite3')
        self.catalog = MusicCatalogService(self.database)
        self.history = PlaybackHistoryService(self.database, self.catalog)
        self.metrics = PlaybackMetricsService(self.database, self.catalog)
        self.info = StationInformationService(self.database, self.catalog, self.history, self.metrics)
        self.config = ConfigurationService(self.root / 'settings.json')
        self.policies = OperatingPolicyService(self.database)
        self.manager = StationManagement(self.config, self.catalog, self.info, self.policies)
        codex = Mock(connected=True)
        codex.account.return_value = {'authenticated': True}
        codex.login_status.return_value = {'pending': False}
        self.controller = ApplicationController(self.config, codex, policies=self.policies)
        self.addCleanup(self.controller.close)
        self.client = create_app(self.controller, management=self.manager).test_client()
        page = self.client.get('/').text
        self.headers = {'X-App-Token': re.search('name="app-token" content="([^"]+)"', page)[1]}

    def song(self, associated=True):
        artist = self.catalog.create_artist(preferred_display_alias='Artist 🌲', aliases=['Old alias']) if associated else None
        song, _ = self.catalog.get_or_create_song('test-song', 'url', title='Test song', style='Test style',
                                                 artist_id=artist['id'] if artist else None)
        return song, artist

    def proposal(self):
        return self.policies.create_proposal('ui-test', 'Test policy', 'Only an isolated test instruction.',
            rationale='Test fixture', basis='Explicit isolated test setup')

    def ready(self):
        self.song()
        self.manager.review_catalog()
        policy = self.proposal()
        self.policies.approve(policy['id'], 1)

    def test_empty_state_and_authentication_are_grounded(self):
        state = self.manager.snapshot(authenticated=False)
        self.assertFalse(state['onboarding']['complete'])
        self.assertEqual(state['onboarding']['current'], 'connection')
        self.assertEqual(state['catalog'], {'songs': 0, 'artists': 0})
        self.assertIsNone(state['now_playing'])
        self.config.update(setup_completed=True, onboarding_completed=True)
        self.assertFalse(self.manager.snapshot(authenticated=False)['onboarding']['complete'])
        self.assertEqual(self.manager.snapshot(authenticated=True)['onboarding']['current'], 'music')

    def test_onboarding_cannot_be_completed_by_clicking_without_data(self):
        for path in ('/api/catalog-review', '/api/onboarding/complete'):
            self.assertEqual(self.client.post(path, headers=self.headers, json={}).status_code, 400)
        self.assertFalse(self.config.get('onboarding_completed', False))

    def test_unresolved_artist_blocks_review_then_can_be_fixed_directly(self):
        self.song(associated=False)
        with self.assertRaises(ValueError):
            self.manager.review_catalog()
        saved = self.manager.save_song('test-song', {'artist_id': None,
            'artist': {'preferred_display_alias': 'Resolved artist', 'aliases': ['Raw author']}})
        self.assertEqual(saved['artist']['preferred_display_alias'], 'Resolved artist')
        self.manager.review_catalog()
        self.assertEqual(self.manager.snapshot(authenticated=True)['onboarding']['current'], 'policies')

    def test_preferred_name_is_required_for_associated_artist_review(self):
        song, artist = self.song()
        self.catalog.update_artist(artist['id'], preferred_display_alias=None)
        with self.assertRaises(ValueError):
            self.manager.review_catalog()
        self.assertEqual(self.manager.snapshot(authenticated=True)['onboarding']['unresolved_artists'], 1)

    def test_complete_onboarding_requires_approval_and_survives_restart(self):
        self.song()
        self.manager.review_catalog()
        policy = self.proposal()
        with self.assertRaises(ValueError):
            self.manager.finish_onboarding(authenticated=True)
        self.policies.approve(policy['id'], 1)
        self.manager.finish_onboarding(authenticated=True)
        manager = StationManagement(ConfigurationService(self.config.path), self.catalog, self.info,
                                    OperatingPolicyService(DatabaseService(self.database.path)))
        self.assertTrue(manager.snapshot(authenticated=True)['onboarding']['complete'])
        self.assertEqual(manager.snapshot(authenticated=True)['station_status'], 'Idle')

    def test_http_metadata_edit_and_alias_removal_persist(self):
        song, artist = self.song()
        response = self.client.post('/api/catalog/test-song', headers=self.headers,
            json={'metadata': {'title': 'Renamed 風', 'style': 'Ambient', 'lyrics': 'New lyrics'},
                  'artist_id': artist['id'],
                  'artist': {'preferred_display_alias': 'Preferred name', 'aliases': ['New alias']}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/catalog/test-song').json['song']['title'], 'Renamed 風')
        restored = MusicCatalogService(DatabaseService(self.database.path))
        self.assertEqual(restored.get_artist(artist['id'])['aliases'], ['New alias', 'Preferred name'])
        self.assertEqual(restored.get_song('test-song')['lyrics'], 'New lyrics')

    def test_alias_conflict_does_not_partially_rename_artist(self):
        _, artist = self.song()
        self.catalog.create_artist(preferred_display_alias='Taken')
        with self.assertRaises(AliasConflict):
            self.catalog.update_artist(artist['id'], preferred_display_alias='Changed', aliases=['Taken'])
        self.assertEqual(self.catalog.get_artist(artist['id'])['preferred_display_alias'], 'Artist 🌲')
        self.assertIn('Old alias', self.catalog.get_artist(artist['id'])['aliases'])

    def test_correcting_song_to_existing_artist_preserves_both_identities(self):
        _, original = self.song()
        existing = self.catalog.create_artist(preferred_display_alias='Seraphym', aliases=['_seraphym'])
        conflict = self.client.post('/api/catalog/test-song', headers=self.headers,
            json={'artist': {'preferred_display_alias': 'Seraphym'}})
        self.assertEqual(conflict.status_code, 400)
        response = self.client.post('/api/catalog/test-song', headers=self.headers,
            json={'artist_id': existing['id']})
        self.assertEqual(response.status_code, 200)
        restored = MusicCatalogService(DatabaseService(self.database.path))
        self.assertEqual(restored.get_song('test-song')['artist_id'], existing['id'])
        self.assertEqual(restored.get_artist(existing['id']), existing)
        self.assertEqual(restored.get_artist(original['id']), original)

    def test_calculated_statistics_cannot_be_saved_as_metadata(self):
        self.song()
        response = self.client.post('/api/catalog/test-song', headers=self.headers,
            json={'metadata': {'completed_plays': 50}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.metrics.song_statistics('test-song')['completed_plays'], 0)

    def test_policy_approval_button_uses_human_boundary_and_csrf(self):
        policy = self.proposal()
        payload = {'action': 'approve', 'policy_id': policy['id'], 'revision': 1}
        self.assertEqual(self.client.post('/api/policies/action', json=payload).status_code, 403)
        self.assertFalse(self.policies.get_policy(policy['id'])['active'])
        result = self.client.post('/api/policies/action', headers=self.headers, json=payload)
        self.assertEqual(result.status_code, 200)
        self.assertTrue(self.policies.get_policy(policy['id'])['active'])
        self.controller.codex.send_message.assert_not_called()
        self.assertEqual(self.controller.status()['messages'], [])

    def test_decline_keeps_prior_approved_policy_active_and_persists(self):
        policy = self.proposal()
        self.policies.approve(policy['id'], 1)
        self.policies.revise(policy['id'], 1, title='New draft', instruction='Draft replacement',
                             rationale='Test revision', basis='Isolated fixture request')
        result = self.client.post('/api/policies/action', headers=self.headers,
            json={'action': 'decline', 'policy_id': policy['id'], 'revision': 2})
        self.assertEqual(result.status_code, 200)
        restored = OperatingPolicyService(DatabaseService(self.database.path))
        self.assertTrue(restored.get_policy(policy['id'])['latest_revision']['declined'])
        self.assertEqual(restored.active_policies()[0]['revision'], 1)
        with self.assertRaises(PolicyConflict):
            restored.approve(policy['id'], 2)

    def test_idle_does_not_use_old_unfinished_history_as_live_playback(self):
        self.song()
        self.history.start_playback('test-song')
        state = self.manager.snapshot(authenticated=True)
        self.assertEqual(state['station_status'], 'Idle')
        self.assertIsNone(state['now_playing'])

    def test_now_playing_uses_actual_injected_playback_status(self):
        self.song()
        playback = Mock()
        playback.status.return_value = {'state': 'buffering', 'suno_song_id': 'test-song'}
        manager = StationManagement(self.config, self.catalog, self.info, self.policies, playback=playback)
        state = manager.snapshot(authenticated=True)
        self.assertEqual(state['station_status'], 'Buffering')
        self.assertEqual(state['now_playing']['song']['title'], 'Test song')
        self.assertIsNone(state['now_playing']['duration_seconds'])

    def test_shell_uses_approved_asset_and_disabled_programming_controls(self):
        page = self.client.get('/').text
        self.assertIn('Kern Radio', page)
        self.assertIn('id="start-programming" disabled', page)
        self.assertIn('id="stop-programming" disabled', page)
        self.assertNotIn('settings-icon', page)
        with self.client.get('/assets/mascot.png') as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, 'image/png')

    def test_invalid_edits_and_unknown_songs_have_clear_feedback(self):
        self.assertEqual(self.client.get('/api/catalog/missing').status_code, 404)
        self.song()
        for data in ({'artist_id': 999}, {'artist_id': True}, {'artist': {'aliases': ['']}},
                     {'artist': {'preferred_display_alias': ''}}):
            response = self.client.post('/api/catalog/test-song', headers=self.headers, json=data)
            self.assertEqual(response.status_code, 400)

    def test_old_policy_schema_gains_decline_flag_without_rewriting_approval(self):
        old = DatabaseService(self.root / 'old-policies.sqlite3')
        old.initialize((
            'CREATE TABLE operating_policies(id INTEGER PRIMARY KEY, subject TEXT UNIQUE, kind TEXT, enabled INTEGER, created_at TEXT, modified_at TEXT)',
            'CREATE TABLE operating_policy_revisions(policy_id INTEGER, revision INTEGER, title TEXT, instruction TEXT, rationale TEXT, basis TEXT, approval_status TEXT, created_at TEXT, PRIMARY KEY(policy_id,revision))',
        ))
        old.execute("INSERT INTO operating_policies VALUES (1,'old','default',1,'old-time','old-time')")
        old.execute("INSERT INTO operating_policy_revisions VALUES (1,1,'Original','Original instruction','Reason','Basis','approved','old-time')")
        migrated = OperatingPolicyService(old)
        self.assertEqual(migrated.get_policy(1)['latest_revision']['declined'], 0)
        self.assertEqual(migrated.active_policies()[0]['instruction'], 'Original instruction')
