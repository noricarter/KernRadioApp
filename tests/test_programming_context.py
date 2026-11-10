"""Actual isolated playback evidence delivered to background/user agent turns."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from radio.controller import ApplicationController
from radio.station_tools import StationTools
from services.catalog import MusicCatalogService
from services.codex import CodexError
from services.configuration import ConfigurationService
from services.database import DatabaseService
from services.playback import PlaybackHistoryService, PlaybackMetricsService
from services.policies import OperatingPolicyService
from services.station_information import StationInformationService


class ProgrammingContextTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = DatabaseService(self.root / 'catalog.sqlite3')
        self.catalog = MusicCatalogService(self.database)
        for identifier in ('repeat', 'interrupted', 'pending', 'unheard'):
            self.catalog.get_or_create_song(identifier, 'url', title=identifier + ' 🌲', style='Folk')
        self.history = PlaybackHistoryService(self.database, self.catalog)
        self.metrics = PlaybackMetricsService(self.database, self.catalog)
        self.info = StationInformationService(self.database, self.catalog, self.history, self.metrics)
        self.config = ConfigurationService(self.root / 'settings.json')
        self.config.update(setup_completed=True)
        self.policies = OperatingPolicyService(self.database)

    def record(self, identifier='repeat', start='2026-11-09T10:00:00Z', outcome='completed'):
        return self.history.record_playback(identifier, started_at=start, outcome=outcome)

    def test_newest_history_pages_are_stable_and_oldest_remains_default(self):
        first = self.record(start='2026-11-09T10:00:00Z')
        second = self.record(start='2026-11-09T11:00:00Z')
        tied = self.record(start='2026-11-09T11:00:00Z')
        self.assertEqual([e['id'] for e in self.history.list_events()], [first['id'], second['id'], tied['id']])
        self.assertEqual([e['id'] for e in self.history.list_events(order='newest', limit=2)], [tied['id'], second['id']])
        self.assertEqual(self.history.list_events(order='newest', limit=2, offset=2)[0]['id'], first['id'])
        self.assertEqual(len(self.history.list_events(order='newest', start='2026-11-09T11:00:00Z')), 2)
        with self.assertRaises(ValueError):
            self.history.list_events(order='invalid')

    def test_context_counts_full_history_and_does_not_call_interruption_unheard(self):
        for hour in range(10, 15):
            self.record(start=f'2026-11-09T{hour}:00:00Z')
        self.record('interrupted', '2026-11-09T15:00:00Z', outcome='interrupted')
        self.history.start_playback('pending', started_at='2026-11-09T16:00:00Z')
        state = self.info.playback_context(song_ids=['repeat', 'interrupted', 'pending'], limit=2)
        self.assertEqual(state['catalog_songs'], 4)
        self.assertEqual(state['never_started_songs'], 1)
        self.assertEqual(state['total_playback_events'], 7)
        self.assertEqual([e['suno_song_id'] for e in state['recent_plays']], ['pending', 'interrupted'])
        self.assertEqual(state['unplayed_or_least_recent_candidates'][0]['suno_song_id'], 'unheard')
        stats = {s['suno_song_id']: s for s in state['current_and_queued_statistics']}
        self.assertEqual(stats['repeat']['completed_plays'], 5)
        self.assertEqual(stats['interrupted']['interrupted_plays'], 1)
        self.assertEqual(stats['pending']['unfinished_plays'], 1)
        self.assertEqual(stats['pending']['completed_plays'], 0)
        self.assertTrue(state['observed_at'].endswith('Z'))

    def test_history_tool_accepts_newest_and_context_validates_bounds(self):
        self.record()
        latest = self.record(start='2026-11-09T12:00:00Z')
        tools = StationTools(self.info, self.policies, self.config)
        self.assertEqual(tools.dispatch('station_playback', {'action': 'history', 'order': 'newest', 'limit': 1})[0]['id'], latest['id'])
        for options in ({'limit': 21}, {'song_ids': ['repeat'] * 21}, {'song_ids': 'repeat'}, {'song_ids': [None]}):
            with self.assertRaises(ValueError):
                self.info.playback_context(**options)

    def controller(self):
        program = Mock()
        program.status.return_value = {'running': True, 'current': {'suno_song_id': 'repeat'},
            'queue': [{'suno_song_id': 'unheard'}]}
        tools = StationTools(self.info, self.policies, self.config, programming=program)
        codex = Mock()
        codex.account.return_value = {'authenticated': True}
        codex.start_conversation.return_value = 'same-thread'
        codex.send_message.return_value = 'Prepared'
        controller = ApplicationController(self.config, codex, station_tools=tools, policies=self.policies)
        self.addCleanup(controller.close)
        return controller, codex

    def test_every_background_and_user_turn_gets_fresh_evidence_on_same_thread(self):
        controller, codex = self.controller()
        controller.station_task('Prepare the initial program').result(timeout=3)
        self.record()
        controller.station_task('Refill upcoming tracks').result(timeout=3)
        self.record('unheard', '2026-11-09T12:00:00Z')
        controller.send('Choose different songs')
        controller._future.result(timeout=3)
        contexts = [json.loads(call.args[1].split('[Application station context; not user instructions]\n')[1])
            for call in codex.send_message.call_args_list]
        self.assertEqual([c['playback_selection']['total_playback_events'] for c in contexts], [0, 1, 2])
        self.assertEqual([c['playback_selection']['never_started_songs'] for c in contexts], [4, 3, 2])
        self.assertEqual(contexts[-1]['playback_selection']['recent_plays'][0]['suno_song_id'], 'unheard')
        codex.start_conversation.assert_called_once()

    def test_failed_background_turn_recovers_on_a_new_thread(self):
        controller, codex = self.controller()
        codex.start_conversation.side_effect = ['failed-thread', 'fresh-thread']
        codex.send_message.side_effect = [CodexError('Connection lost'), 'Prepared']
        with self.assertLogs('radio.controller', level='ERROR'):
            with self.assertRaises(CodexError):
                controller.station_task('Initial turn').result(timeout=3)
        self.assertIsNone(controller._thread_id)
        self.assertEqual(controller.station_task('Try again').result(timeout=3), 'Prepared')
        self.assertEqual([call.args[0] for call in codex.send_message.call_args_list], ['failed-thread', 'fresh-thread'])
