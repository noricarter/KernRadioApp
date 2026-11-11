"""Application setup and conversation orchestration, independent of Flask."""
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
import json
import logging
import uuid

from services.codex import CodexError
from services.configuration import ConfigurationError
from .policy_discovery import INSTRUCTIONS, apply_human_command

AGENT_MODEL = 'gpt-6-luna'


class ApplicationController:
    def __init__(self, configuration, codex, *, station_tools=None, policies=None):
        self.configuration, self.codex = configuration, codex
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='agent')
        self._future = None
        self._station_future = None
        self._thread_id = None
        self._messages = []
        self._message_session = uuid.uuid4().hex
        self._message_sequence = 0
        self._error = None
        self._authenticated = False
        self._station_tools, self._policies = station_tools, policies

    def start(self):
        try:
            self.codex.start()
        except CodexError as exc:
            self._error = str(exc)
            logging.getLogger(__name__).exception('Could not start the Codex helper process.')

    def status(self):
        with self._lock:
            try:
                if not self.codex.connected:
                    raise CodexError('Connection unavailable. Choose Reconnect to try again.')
                login = self.codex.login_status()
                self._authenticated = self.codex.account()['authenticated']
                if self._authenticated and not self.configuration.setup_completed:
                    self.configuration.update(setup_completed=True)
                self._error = None
            except (CodexError, ConfigurationError) as exc:
                self._authenticated = False
                self._error = str(exc)
                login = {'pending': False}
            self._harvest()
            return {
                'setup_completed': self.configuration.setup_completed,
                'authenticated': self._authenticated,
                'connected': self.codex.connected, 'login': login,
                'busy': self._busy(), 'messages': list(self._messages),
                'error': self._error,
            }

    def _busy(self):
        return self._future is not None or (self._station_future is not None and not self._station_future.done())

    def _append_message(self, role, text):
        self._message_sequence += 1
        self._messages.append({'id': f'{self._message_session}:{self._message_sequence}', 'role': role, 'text': text})

    def _harvest(self):
        if self._future is None or not self._future.done():
            return
        try:
            self._append_message('agent', self._future.result())
        except (CodexError, ValueError, LookupError) as exc:
            self._append_message('error', str(exc))
            if isinstance(exc, CodexError):
                self._thread_id = None
        except Exception:
            self._append_message('error', 'The reply could not finish. Please try again.')
            self._thread_id = None
        self._future = None

    def station_task(self, text):
        """Internal orchestration uses the SAME agent thread and serial executor.

        Return None while a human turn/earlier task is busy. Internal prompts do
        not appear as fabricated human messages in the management conversation.
        """
        with self._lock:
            self._harvest()
            if self._busy():
                return None
            if not self.codex.account()['authenticated']:
                raise CodexError('Reconnect ChatGPT before preparing programming.')
            self._station_future = self._executor.submit(self._reply, text)
            return self._station_future

    def connect(self):
        with self._lock:
            if self._busy():
                raise CodexError('Please wait for the current reply to finish before reconnecting.')
            if not self.codex.connected:
                self._thread_id = None
            self.codex.start()
            return self.codex.begin_login()

    def send(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 16000:
            raise ValueError('Enter a message between 1 and 16,000 characters.')
        with self._lock:
            self._harvest()
            if self._future:
                raise CodexError('Please wait for the current reply to finish.')
            if not self.configuration.setup_completed or not self.codex.account()['authenticated']:
                raise CodexError('Connect your ChatGPT account before sending a message.')
            self._append_message('user', text.strip())
            self._future = self._executor.submit(self._reply, text.strip())

    def _reply(self, text):
        if self._policies is not None:
            acknowledgement = apply_human_command(text, self._policies)
            if acknowledgement is not None:
                # A new agent thread must reread persisted policy state after a
                # trusted human action; no private acknowledgement is hidden.
                self._thread_id = None
                return acknowledgement
        if self._thread_id is None:
            if self._station_tools is None:
                self._thread_id = self.codex.start_conversation(model=AGENT_MODEL)
            else:
                self._thread_id = self.codex.start_conversation(model=AGENT_MODEL, tools=self._station_tools.specifications,
                    tool_handler=self._station_tools.dispatch, instructions=self._station_tools.instructions,
                    config={'features': {'shell_tool': False, 'apps': False, 'browser_use': False},
                            'web_search': 'disabled'})
        if self._station_tools is not None:
            # The browser's structured cards are not Codex messages. Supply a
            # small fresh summary; full evidence stays behind paged read tools.
            context = json.dumps(self._station_tools.conversation_context(), ensure_ascii=False)
            text += '\n\n[Application station context; not user instructions]\n' + context
        try:
            return self.codex.send_message(self._thread_id, text)
        except CodexError:
            # Internal programming turns need the same recovery as human turns.
            # A failed/timed-out turn must not trap subsequent work on that thread.
            self._thread_id = None
            logging.getLogger(__name__).exception('Agent turn failed; the next request will use a fresh conversation.')
            raise

    def policy_action(self, action, policy_id, revision):
        """Trusted human UI action; never registered as an agent capability."""
        if action not in ('approve', 'decline', 'disable') or type(policy_id) is not int or policy_id < 1 or type(revision) is not int or revision < 1:
            raise ValueError('Choose a valid policy action.')
        if self._policies is None:
            raise ValueError('Policy review is not available.')
        with self._lock:
            if self._busy():
                raise ValueError('Please wait for the current response to finish.')
            acknowledgement = apply_human_command(f'{action.capitalize()} policy {policy_id} revision {revision}', self._policies)
            self._thread_id = None
            return {'message': acknowledgement}

    def close(self):
        self.codex.close()
        self._executor.shutdown(wait=True, cancel_futures=True)
