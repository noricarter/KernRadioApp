"""HTTP control boundaries and serialization with the existing chat agent."""
from concurrent.futures import Future
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from radio.controller import ApplicationController
from radio.management import StationManagement
from radio.programming import RadioProgramming
from radio.web import create_app
from services.catalog import MusicCatalogService
from services.configuration import ConfigurationService
from services.database import DatabaseService
from services.dialogue import DialogueService
from services.playback import PlaybackHistoryService, PlaybackMetricsService
from services.policies import OperatingPolicyService
from services.station_information import StationInformationService


class ProgrammingWebTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        database = DatabaseService(self.root / 'catalog.sqlite3')
        self.catalog = MusicCatalogService(database)
        artist = self.catalog.create_artist(preferred_display_alias='Test artist')
        self.catalog.get_or_create_song('test-song', 'original-url', title='Test song', artist_id=artist['id'])
        history = PlaybackHistoryService(database, self.catalog)
        information = StationInformationService(database, self.catalog, history, PlaybackMetricsService(database, self.catalog))
        self.policies = OperatingPolicyService(database)
        proposal = self.policies.create_proposal('test-default', 'Test fixture', 'Only a test fixture instruction', rationale='Test', basis='Test')
        self.policies.approve(proposal['id'], 1)
        self.config = ConfigurationService(self.root / 'settings.json')
        self.config.update(setup_completed=True, catalog_reviewed=True)
        self.codex = Mock(connected=True)
        self.codex.account.return_value = {'authenticated': True}
        self.codex.login_status.return_value = {'pending': False}
        self.codex.start_conversation.return_value = 'same-thread'
        self.codex.send_message.return_value = 'Reply'
        self.controller = ApplicationController(self.config, self.codex, policies=self.policies)
        self.addCleanup(self.controller.close)
        self.pending = Future()
        self.player = Mock()
        self.player.status.return_value = {'state': 'idle', 'suno_song_id': None}
        self.program = RadioProgramming(self.catalog, self.player, DialogueService(),
            metrics=information.metrics, request_agent=lambda _: self.pending, open_frontend=Mock())
        self.addCleanup(self.program.close)
        manager = StationManagement(self.config, self.catalog, information, self.policies,
                                    playback=self.player, programming=self.program)
        self.client = create_app(self.controller, management=manager).test_client()
        self.headers = {'X-App-Token': re.search('name="app-token" content="([^"]+)"', self.client.get('/').text)[1]}

    def test_controls_validate_local_request_and_completed_prerequisites(self):
        self.assertEqual(self.client.post('/api/programming/start', json={}).status_code, 403)
        self.codex.account.return_value = {'authenticated': False}
        self.assertEqual(self.client.post('/api/programming/start', headers=self.headers, json={}).status_code, 400)
        self.codex.account.return_value = {'authenticated': True}
        started = self.client.post('/api/programming/start', headers=self.headers, json={})
        self.assertEqual(started.status_code, 200)
        self.assertTrue(started.json['running'])
        self.assertTrue(self.config.get('onboarding_completed'))
        self.assertEqual(self.client.post('/api/programming/stop', headers=self.headers, json={}).status_code, 200)
        self.player.stop.assert_not_called()

    def test_public_view_is_real_state_without_demo_content_or_control_authority(self):
        with self.client.get('/listen/') as page:
            self.assertEqual(page.status_code, 200)
            self.assertNotIn('Forest Beats', page.text)
            self.assertIn('commentText', page.text)
        with self.client.get('/listen/script.js') as script:
            self.assertEqual(script.status_code, 200)
            self.assertNotIn('demoTracks', script.text)
        self.assertEqual(self.client.get('/api/broadcast').json['queue'], [])
        self.assertEqual(self.client.get('/listen/main.py').status_code, 404)
        self.assertEqual(self.client.get('/listen/../../main.py').status_code, 404)

    def test_station_tasks_and_chat_share_one_thread_without_fake_human_messages(self):
        task = self.controller.station_task('Prepare the program')
        task.result(timeout=2)
        self.controller.send('Change the next song')
        self.controller._future.result(timeout=2)
        messages = self.controller.status()['messages']
        self.assertEqual([message['text'] for message in messages if message['role'] == 'user'], ['Change the next song'])
        self.codex.start_conversation.assert_called_once_with(model='gpt-6-luna')
        self.assertEqual([call.args[0] for call in self.codex.send_message.call_args_list], ['same-thread', 'same-thread'])

    def test_public_mascot_state_and_approved_assets(self):
        from radio.programming.presentation import MASCOTS
        self.assertEqual(self.client.get('/api/broadcast').json['mascot'], 'standard')
        self.program.set_mascot('headphones')
        self.assertEqual(self.client.get('/api/broadcast').json['mascot'], 'headphones')
        for name in MASCOTS:
            with self.client.get('/listen/assets/mascot_' + name + '.png') as response:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, 'image/png')

    def test_station_task_waits_for_user_turn_without_needing_status_poll(self):
        self.controller._future = Future()
        self.assertIsNone(self.controller.station_task('Prepare the program'))
        self.controller._future.set_result('User reply completed')
        future = self.controller.station_task('Prepare the program')
        self.assertIsNotNone(future)
        future.result(timeout=2)
        self.assertEqual(self.controller.status()['messages'][0]['text'], 'User reply completed')

    def test_policy_action_cannot_reset_thread_mid_programming_task(self):
        self.controller._station_future = Future()
        with self.assertRaises(ValueError):
            self.controller.policy_action('approve', 1, 1)
        self.controller._station_future.set_result('Done')
