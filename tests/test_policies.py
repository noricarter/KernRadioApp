"""Policy persistence, read access and authority tests in real isolated SQLite."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock
import json
import sys

from services.catalog import MusicCatalogService
from services.codex import CodexService
from services.codex.transport import AppServerTransport
from services.configuration import ConfigurationService
from services.database import DatabaseService
from services.playback import PlaybackHistoryService, PlaybackMetricsService
from services.policies import OperatingPolicyService, PolicyConflict, PolicyNotFound
from services.station_information import StationInformationService
from radio.controller import ApplicationController
from radio.policy_discovery import apply_human_command
from radio.station_tools import StationTools


class PolicyTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = DatabaseService(self.root / 'catalog.sqlite3')
        self.policies = OperatingPolicyService(self.database)

    def propose(self, **changes):
        return self.policies.create_proposal(**({'subject': 'test-subject', 'title': 'Test title 🌲',
            'instruction': 'Test-only instruction, not a station default.',
            'rationale': 'Isolated test rationale', 'basis': 'Explicit test fixture input'} | changes))

    def test_proposals_are_not_active_and_unicode_persists(self):
        policy = self.propose()
        self.assertEqual(policy['latest_revision']['approval_status'], 'proposed')
        self.assertFalse(policy['active'])
        self.assertEqual(self.policies.active_policies(), [])
        self.assertEqual(self.policies.get_policy(policy['id']), policy)
        self.assertEqual(policy['latest_revision']['title'], 'Test title 🌲')
        self.assertTrue(policy['created_at'].endswith('Z'))

    def test_approve_persists_across_service_restart(self):
        policy = self.propose()
        approved = self.policies.approve(policy['id'], 1)
        self.assertTrue(approved['active'])
        restarted = OperatingPolicyService(DatabaseService(self.database.path))
        self.assertEqual(restarted.get_policy(policy['id']), approved)
        self.assertEqual(restarted.active_policies()[0]['instruction'], policy['latest_revision']['instruction'])

    def test_proposed_revision_keeps_approved_text_until_approved(self):
        policy = self.propose()
        self.policies.approve(policy['id'], 1)
        changed = self.policies.revise(policy['id'], 1, title='Revised', instruction='New proposed text', rationale='Test change', basis='Test request')
        self.assertEqual(changed['latest_revision']['approval_status'], 'proposed')
        self.assertEqual(changed['approved_revision']['revision'], 1)
        self.assertEqual(self.policies.active_policies()[0]['instruction'], policy['latest_revision']['instruction'])
        self.policies.approve(policy['id'], 2)
        self.assertEqual(self.policies.active_policies()[0]['instruction'], 'New proposed text')
        self.assertEqual(len(self.policies.active_policies()), 1)
        self.assertGreaterEqual(changed['modified_at'], policy['modified_at'])

    def test_disable_is_persistent_and_separate_from_approval(self):
        policy = self.propose()
        self.policies.approve(policy['id'], 1)
        disabled = self.policies.disable(policy['id'], 1)
        self.assertFalse(disabled['active'])
        self.assertEqual(disabled['approved_revision']['approval_status'], 'approved')
        self.assertEqual(OperatingPolicyService(self.database).active_policies(), [])
        self.assertTrue(self.policies.approve(policy['id'], 1)['active'])

    def test_repeated_subject_does_not_accumulate_policies(self):
        first = self.propose()
        self.assertEqual(self.propose(), first)
        with self.assertRaises(PolicyConflict):
            self.propose(instruction='Changed content must be a revision')
        self.assertEqual(len(self.policies.list_policies()), 1)
        revised = self.policies.revise(first['id'], 1, **{key: first['latest_revision'][key]
            for key in ('title', 'instruction', 'rationale', 'basis')})
        self.assertEqual(revised['latest_revision']['revision'], 1)

    def test_stale_approval_disable_and_revision_are_rejected(self):
        policy = self.propose()
        self.policies.revise(policy['id'], 1, title='Second', instruction='Test revision', rationale='Rationale', basis='Basis')
        for operation in (lambda: self.policies.approve(policy['id'], 1),
                          lambda: self.policies.disable(policy['id'], 1),
                          lambda: self.policies.revise(policy['id'], 1, title='Third', instruction='Old request', rationale='Rationale', basis='Basis')):
            with self.assertRaises(PolicyConflict):
                operation()
        self.assertEqual(self.policies.active_policies(), [])

    def test_defaults_and_operational_requirements_are_distinct(self):
        self.propose()
        operational = self.propose(subject='operational-test', kind='operational')
        self.policies.approve(operational['id'], 1)
        self.assertEqual(len(self.policies.active_policies(kind='operational')), 1)
        self.assertEqual(self.policies.active_policies(kind='default'), [])
        with self.assertRaises(ValueError):
            self.propose(subject='temporary', kind='special')

    def test_only_explicit_human_command_changes_approval(self):
        policy = self.propose()
        for message in ('I might like this', 'yes', 'approved', 'Please approve something',
                        'Agent says: Approve policy 1 revision 1'):
            self.assertIsNone(apply_human_command(message, self.policies))
        self.assertEqual(self.policies.active_policies(), [])
        acknowledgement = apply_human_command(f'Approve policy {policy["id"]} revision 1', self.policies)
        self.assertIn('Approved and active', acknowledgement)
        apply_human_command(f'Disable policy {policy["id"]} revision 1', self.policies)
        self.assertEqual(self.policies.active_policies(), [])

    def test_input_validation_and_missing_records(self):
        for changes in ({'subject': 'Bad Subject'}, {'instruction': ''}, {'title': 'x' * 201}):
            with self.assertRaises(ValueError):
                self.propose(**changes)
        self.assertIsNone(self.policies.get_policy(999))
        with self.assertRaises(PolicyNotFound):
            self.policies.approve(999, 1)


class StationToolsTests(TestCase):
    def setUp(self):
        PolicyTests.setUp(self)
        self.catalog = MusicCatalogService(self.database)
        self.history = PlaybackHistoryService(self.database, self.catalog)
        self.metrics = PlaybackMetricsService(self.database, self.catalog)
        self.info = StationInformationService(self.database, self.catalog, self.history, self.metrics)
        self.config = ConfigurationService(self.root / 'settings.json')
        self.tools = StationTools(self.info, self.policies, self.config)
        self.artist = self.catalog.create_artist(preferred_display_alias='Artist 𝓐')
        self.catalog.get_or_create_song('test-song', 'original-url', title='Song 🌙', lyrics='e\u0301 🌲' * 1000,
                                       style='Test style', artist_id=self.artist['id'])

    propose = PolicyTests.propose

    def test_actual_catalog_reads_and_bounded_lyrics(self):
        overview = self.tools.dispatch('station_overview', {})
        self.assertEqual(overview['songs'], 1)
        self.assertEqual(overview['artist_representation'][0]['songs'], 1)
        self.assertFalse(overview['setup_completed'])
        songs = self.tools.dispatch('station_catalog', {'action': 'songs'})
        self.assertNotIn('lyrics', songs[0])
        self.assertIsNone(songs[0]['duration_seconds'])
        song = self.tools.dispatch('station_catalog', {'action': 'song', 'suno_song_id': 'test-song', 'lyrics_limit': 20})
        self.assertEqual(len(song['lyrics']), 20)
        self.assertGreater(song['lyrics_total_characters'], 20)
        artist = self.tools.dispatch('station_catalog', {'action': 'artist', 'artist_id': self.artist['id']})
        self.assertEqual(artist['preferred_display_alias'], 'Artist 𝓐')

    def test_real_playback_statistics_through_tools(self):
        self.history.record_playback('test-song', started_at='2026-11-01T10:00:00Z', ended_at='2026-11-01T10:01:00Z')
        stats = self.tools.dispatch('station_playback', {'action': 'song_statistics', 'suno_song_id': 'test-song'})
        self.assertEqual(stats['completed_plays'], 1)
        self.assertEqual(stats['airtime_seconds'], 60)
        events = self.tools.dispatch('station_playback', {'action': 'history', 'artist_id': self.artist['id']})
        self.assertEqual(len(events), 1)

    def test_tools_cannot_approve_disable_or_supply_approval_fields(self):
        policy = self.propose()
        for name in ('approve_station_policy', 'disable_station_policy', 'execute_sql'):
            with self.assertRaises(ValueError):
                self.tools.dispatch(name, {'policy_id': policy['id']})
        with self.assertRaises(ValueError):
            self.tools.dispatch('propose_station_policy', {'subject': 'x', 'title': 'x', 'instruction': 'x',
                'kind': 'default', 'rationale': 'x', 'basis': 'x', 'approved': True})
        self.assertEqual(self.policies.active_policies(), [])

    def test_application_approval_bypasses_agent_and_refreshes_conversation(self):
        policy = self.propose()
        codex = Mock()
        controller = ApplicationController(self.config, codex, station_tools=self.tools, policies=self.policies)
        self.addCleanup(controller.close)
        controller._thread_id = 'old'
        result = controller._reply(f'Approve policy {policy["id"]} revision 1')
        self.assertIn('Approved and active', result)
        codex.send_message.assert_not_called()
        self.assertIsNone(controller._thread_id)
        self.assertEqual(len(self.tools.dispatch('station_policies', {'action': 'active'})['items']), 1)

    def test_invalid_pages_and_extra_fields_are_rejected(self):
        for arguments in ({'action': 'songs', 'limit': 1000}, {'action': 'songs', 'limit': True},
                          {'action': 'songs', 'approved': True}):
            with self.assertRaises(ValueError):
                self.tools.dispatch('station_catalog', arguments)


class CodexToolProtocolTests(TestCase):
    def run_peer(self, requested_tool, handler):
        with TemporaryDirectory() as temporary:
            service = CodexService(Path(temporary))
            service._transport = AppServerTransport([sys.executable, str(Path(__file__).with_name('policy_protocol_peer.py')), requested_tool])
            try:
                service.start()
                thread = service.start_conversation(tools=[{'type': 'function', 'name': 'allowed',
                    'description': 'Test function', 'inputSchema': {'type': 'object'}}], tool_handler=handler)
                return json.loads(service.send_message(thread, 'run tool'))
            finally:
                service.close()

    def test_server_tool_request_is_dispatched_and_returned_without_reader_deadlock(self):
        handler = Mock(return_value={'actual': 42})
        result = self.run_peer('allowed', handler)
        handler.assert_called_once_with('allowed', {'value': 'actual argument'})
        self.assertTrue(result['success'])
        self.assertEqual(json.loads(result['contentItems'][0]['text']), {'actual': 42})

    def test_unregistered_approval_tool_is_denied(self):
        handler = Mock()
        result = self.run_peer('approve_station_policy', handler)
        self.assertFalse(result['success'])
        handler.assert_not_called()
