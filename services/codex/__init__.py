"""Public agent/authentication interface; protocol details stay inside this package."""
import os
import json
import queue
import time
from pathlib import Path
from threading import Lock

from .transport import AppServerTransport, CodexError

__all__ = ['CodexService', 'CodexError']


class CodexService:
    def __init__(self, workspace: Path, *, codex_home: Path | None = None, executable='codex'):
        workspace.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        if codex_home is not None:
            env['CODEX_HOME'] = str(codex_home)
        self.workspace = workspace.resolve()
        self._transport = AppServerTransport(
            [executable, 'app-server', '--listen', 'stdio://'], env=env, cwd=str(self.workspace),
        )
        self._lifecycle = Lock()
        self._conversation = Lock()
        self._login = None
        self._login_events = None
        self._login_subscription = None
        self._tools = {}

    @property
    def connected(self):
        return self._transport.connected

    def start(self):
        with self._lifecycle:
            if not self.connected:
                self._tools.clear()
                self._clear_login()
                self._transport.close()
                self._transport.start()

    def account(self):
        """Read Codex-managed authentication; never read or store credentials."""
        result = self._transport.request('account/read', {'refreshToken': False})
        account = result.get('account') or {}
        return {'authenticated': account.get('type') == 'chatgpt'}

    def _clear_login(self):
        if self._login_subscription:
            self._login_subscription.__exit__(None, None, None)
        self._login_subscription = self._login_events = self._login = None

    def login_status(self):
        with self._lifecycle:
            if not self._login:
                return {'pending': False}
            while True:
                try:
                    event = self._login_events.get_nowait()
                except queue.Empty:
                    return {'pending': True, 'auth_url': self._login['authUrl']}
                if event['method'] == 'connection/closed':
                    self._clear_login()
                    raise CodexError('Connection lost. Choose Reconnect to try again.')
                if event['method'] == 'account/login/completed':
                    if event['params'].get('loginId') != self._login['loginId']:
                        continue
                    success = event['params']['success']
                    self._clear_login()
                    if not success:
                        raise CodexError('Sign-in was not completed. Choose Connect ChatGPT to try again.')
                    return {'pending': False}

    def begin_login(self):
        self.start()
        with self._lifecycle:
            if self._login:
                return {'auth_url': self._login['authUrl']}
            self._login_subscription = self._transport.notifications()
            self._login_events = self._login_subscription.__enter__()
            try:
                self._login = self._transport.request('account/login/start', {'type': 'chatgpt'})
            except Exception:
                self._clear_login()
                raise
            return {'auth_url': self._login['authUrl']}

    def start_conversation(self, *, model=None, tools=None, tool_handler=None, instructions=None, config=None):
        if tools and tool_handler is None:
            raise ValueError('Tools require a handler.')
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ValueError('Choose a nonempty model identifier.')
        params = {
            'cwd': str(self.workspace), 'approvalPolicy': 'never', 'sandbox': 'read-only',
            'ephemeral': True,
            'developerInstructions': instructions or 'Provide conversational assistance. Do not execute commands, access files, or use tools.',
        }
        if model is not None:
            params['model'] = model
        if tools:
            params['dynamicTools'] = tools
        if config is not None:
            params['config'] = config
        result = self._transport.request('thread/start', params)
        thread_id = result['thread']['id']
        self._tools[thread_id] = ({tool['name'] for tool in tools or []}, tool_handler)
        return thread_id

    def send_message(self, thread_id, text, *, timeout=180):
        """Run one turn and return its completed agent text. Raises on failed/lost turns."""
        with self._conversation, self._transport.notifications() as events:
            turn = self._transport.request('turn/start', {
                'threadId': thread_id, 'input': [{'type': 'text', 'text': text}],
            })['turn']
            deadline = time.monotonic() + timeout
            replies = {}
            try:
                while time.monotonic() < deadline:
                    event = events.get(timeout=max(0.01, deadline - time.monotonic()))
                    method, params = event['method'], event.get('params', {})
                    if method == 'connection/closed':
                        raise CodexError('Connection lost. Choose Reconnect to try again.')
                    if 'id' in event:
                        if method == 'item/tool/call' and params.get('threadId') == thread_id and params.get('turnId') == turn['id']:
                            names, handler = self._tools.get(thread_id, (set(), None))
                            try:
                                if params.get('namespace') or params['tool'] not in names or handler is None:
                                    raise ValueError('This tool is not available in this conversation.')
                                arguments = params['arguments']
                                if isinstance(arguments, str):
                                    arguments = json.loads(arguments)
                                value = handler(params['tool'], arguments)
                                success = True
                            except (ValueError, LookupError, TypeError) as exc:
                                value, success = {'error': str(exc)}, False
                            except Exception:
                                value, success = {'error': 'The station operation failed. No success is confirmed; inspect the current state before retrying.'}, False
                            self._transport.respond(event['id'], result={'success': success,
                                'contentItems': [{'type': 'inputText', 'text': json.dumps(value, ensure_ascii=False)}]})
                        else:
                            self._transport.respond(event['id'], error={'code': -32601, 'message': 'This interaction is not supported.'})
                        continue
                    if params.get('threadId') != thread_id or params.get('turnId', turn['id']) != turn['id']:
                        continue
                    if method == 'item/completed' and params['item']['type'] == 'agentMessage':
                        item = params['item']
                        replies[item['id']] = item['text']
                    if method == 'turn/completed':
                        if params['turn']['id'] != turn['id']:
                            continue
                        if params['turn']['status'] != 'completed':
                            raise CodexError('The agent could not reply. Try again or reconnect your ChatGPT account.')
                        return '\n\n'.join(replies.values()) or 'The agent finished without a text response. Please try again.'
            except queue.Empty:
                pass
            try:
                self._transport.request('turn/interrupt', {'threadId': thread_id, 'turnId': turn['id']}, timeout=5)
            except CodexError:
                pass
            raise CodexError('The reply took too long. Please try again.')

    def close(self):
        with self._lifecycle:
            self._tools.clear()
            self._clear_login()
            self._transport.close()
