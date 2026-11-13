import json
import queue
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from concurrent.futures import Future

from radio.controller import ApplicationController
from radio.web import create_app
from services.codex import CodexError, CodexService
from services.codex.transport import AppServerTransport, _prepare_command
from services.configuration import ConfigurationError, ConfigurationService
from services.web import FlaskWebService
from urllib.request import urlopen


class ConfigurationTests(unittest.TestCase):
    def test_restart_and_no_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nested/settings.json'
            service = ConfigurationService(path)
            self.assertFalse(service.setup_completed)
            service.update(setup_completed=True)
            self.assertTrue(ConfigurationService(path).setup_completed)
            self.assertEqual(json.loads(path.read_text()), {'setup_completed': True})

    def test_corrupt_configuration_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text('broken')
            with self.assertRaises(ConfigurationError):
                ConfigurationService(path)
            self.assertEqual(path.read_text(), 'broken')

    def test_failed_update_does_not_change_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'parent/settings.json'
            service = ConfigurationService(path)
            path.parent.write_text('not a directory')
            with self.assertRaises(ConfigurationError):
                service.update(setup_completed=True)
            self.assertFalse(service.setup_completed)


class TransportTests(unittest.TestCase):
    def test_windows_batch_cli_is_quoted_for_shell_launch(self):
        command, use_shell = _prepare_command(
            [r'C:\Program Files\Codex\codex.CMD', 'app-server', '--listen', 'stdio://'],
            windows=True,
        )
        self.assertTrue(use_shell)
        self.assertEqual(command, r'"C:\Program Files\Codex\codex.CMD" app-server --listen stdio://')

    def test_native_cli_does_not_use_shell(self):
        command = [r'C:\Program Files\Codex\codex.exe', 'app-server']
        prepared, use_shell = _prepare_command(command, windows=True)
        self.assertFalse(use_shell)
        self.assertEqual(prepared, command)

    def setUp(self):
        self.transport = AppServerTransport([sys.executable, str(Path(__file__).with_name('fake_app_server.py'))])
        self.transport.start()
        self.addCleanup(self.transport.close)

    def test_rpc_and_notifications(self):
        with self.transport.notifications() as events:
            self.assertEqual(self.transport.request('event', {}), {'ok': True})
            self.assertEqual(events.get(timeout=2)['method'], 'test/event')

    def test_upstream_details_are_hidden(self):
        with self.assertRaises(CodexError) as raised:
            self.transport.request('reject', {})
        self.assertNotIn('private upstream detail', str(raised.exception))

    def test_timeout(self):
        with self.assertRaises(CodexError):
            self.transport.request('hang', {}, timeout=.05)

    def test_connection_loss(self):
        with self.assertRaises(CodexError):
            self.transport.request('exit', {}, timeout=2)

    def test_restart_and_cleanup(self):
        process = self.transport._process
        self.transport.close()
        self.assertIsNotNone(process.poll())
        self.transport.start()
        self.assertEqual(self.transport.request('echo', {}), {'ok': True})


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = ConfigurationService(Path(self.temporary.name) / 'settings.json')
        self.codex = Mock(connected=True)
        self.codex.account.return_value = {'authenticated': False}
        self.codex.login_status.return_value = {'pending': False}
        self.controller = ApplicationController(self.config, self.codex)
        self.addCleanup(self.controller.close)
        self.app = create_app(self.controller)
        self.client = self.app.test_client()
        import re
        self.token = re.search(r'name="app-token" content="([^"]+)"', self.client.get('/').text)[1]
        self.headers = {'X-App-Token': self.token}

    def authenticate(self):
        self.codex.account.return_value = {'authenticated': True}
        self.controller.status()

    def test_fresh_walkthrough(self):
        self.assertIn('Connect your ChatGPT account', self.client.get('/').text)
        state = self.client.get('/api/status').json
        self.assertFalse(state['setup_completed'])
        self.assertFalse(state['authenticated'])

    def test_auth_completion_persists(self):
        self.authenticate()
        self.assertTrue(ConfigurationService(self.config.path).setup_completed)

    def test_connect_adapter(self):
        self.codex.begin_login.return_value = {'auth_url': 'https://example.com/signin'}
        response = self.client.post('/api/connect', headers=self.headers, json={})
        self.assertEqual(response.json['auth_url'], 'https://example.com/signin')

    def test_reconnect_starts_login_even_when_cached_account_exists(self):
        self.authenticate()
        self.codex.begin_login.return_value = {'auth_url': 'https://example.com/signin'}
        self.controller.connect()
        self.codex.begin_login.assert_called_once()

    def test_expired_auth_requires_reconnect(self):
        self.authenticate()
        self.codex.account.return_value = {'authenticated': False}
        state = self.controller.status()
        self.assertTrue(state['setup_completed'])
        self.assertFalse(state['authenticated'])
        self.assertEqual(self.client.post('/api/messages', headers=self.headers, json={'text': 'hello'}).status_code, 503)

    def test_missing_helper_does_not_prevent_web_page(self):
        self.codex.connected = False
        state = self.controller.status()
        self.assertIn('Reconnect', state['error'])
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_csrf_and_untrusted_host(self):
        self.assertEqual(self.client.post('/api/connect').status_code, 403)
        self.assertEqual(self.client.post('/api/connect', headers=self.headers | {'Origin': 'https://example.com'}).status_code, 403)
        self.assertEqual(self.client.get('/', headers={'Host': 'evil.example'}).status_code, 400)

    def test_bad_messages(self):
        for value in (None, '', ' ', 3, 'a' * 16001):
            response = self.client.post('/api/messages', headers=self.headers, json={'text': value})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post('/api/messages', headers=self.headers, json=[]).status_code, 400)

    def test_chat_and_failure(self):
        self.authenticate()
        self.codex.start_conversation.return_value = 'thread-1'
        self.codex.send_message.return_value = 'Hello back'
        self.assertEqual(self.client.post('/api/messages', headers=self.headers, json={'text': 'Hello'}).status_code, 202)
        self.controller._future.result(timeout=2)
        self.assertEqual(self.controller.status()['messages'][-1]['text'], 'Hello back')
        self.codex.send_message.side_effect = CodexError('Please reconnect.')
        self.controller.send('second')
        try:
            self.controller._future.result(timeout=2)
        except CodexError:
            pass
        self.assertEqual(self.controller.status()['messages'][-1]['role'], 'error')

    def test_radio_conversation_explicitly_uses_luna(self):
        self.authenticate()
        self.codex.start_conversation.return_value = 'thread-luna'
        self.codex.send_message.return_value = 'Hello'
        self.controller.send('Hello')
        self.controller._future.result(timeout=2)
        self.codex.start_conversation.assert_called_once_with(model='gpt-6-luna')

    def test_busy_request_rejected(self):
        self.authenticate()
        future = Future()
        self.controller._future = future
        with self.assertRaises(CodexError):
            self.controller.send('hello')
        future.set_result('Finished')

    def test_web_service_lifecycle_over_http(self):
        web = FlaskWebService(self.app, port=0)
        web.start()
        try:
            with urlopen(web.url) as response:
                self.assertEqual(response.status, 200)
        finally:
            web.stop()


class CodexProtocolTests(unittest.TestCase):
    def test_conversation_passes_explicit_model_and_preserves_generic_default(self):
        with tempfile.TemporaryDirectory() as directory:
            service = CodexService(Path(directory))
            service._transport = Mock()
            service._transport.request.return_value = {'thread': {'id': 'thread-model'}}
            service.start_conversation(model='gpt-6-luna')
            self.assertEqual(service._transport.request.call_args.args[1]['model'], 'gpt-6-luna')
            service.start_conversation()
            self.assertNotIn('model', service._transport.request.call_args.args[1])

    def test_invalid_model_is_rejected_before_protocol_request(self):
        with tempfile.TemporaryDirectory() as directory:
            service = CodexService(Path(directory))
            service._transport = Mock()
            for model in ('', ' ', 123):
                with self.assertRaises(ValueError):
                    service.start_conversation(model=model)
            service._transport.request.assert_not_called()

    def test_authentication_requires_chatgpt(self):
        with tempfile.TemporaryDirectory() as directory:
            service = CodexService(Path(directory))
            service._transport = Mock()
            for account, expected in ((None, False), ({'type': 'apiKey'}, False), ({'type': 'chatgpt'}, True)):
                service._transport.request.return_value = {'account': account}
                self.assertEqual(service.account()['authenticated'], expected)

    def test_failed_login_event(self):
        with tempfile.TemporaryDirectory() as directory:
            service = CodexService(Path(directory))
            service._login = {'loginId': '1', 'authUrl': 'https://example.com'}
            service._login_events = queue.Queue()
            service._login_events.put({'method': 'account/login/completed', 'params': {'loginId': '1', 'success': False}})
            with self.assertRaisesRegex(CodexError, 'Sign-in was not completed'):
                service.login_status()


if __name__ == '__main__':
    unittest.main()
