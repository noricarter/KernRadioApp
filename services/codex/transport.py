"""Newline-delimited JSON RPC transport for a single managed child process."""
import json
import os
import queue
import shutil
import subprocess
from contextlib import contextmanager
from threading import Lock, Thread


def _prepare_command(command, *, windows=None):
    """Launch Windows .cmd/.bat CLI shims through cmd.exe's shell."""
    if windows is None:
        windows = os.name == 'nt'
    if windows:
        executable = shutil.which(command[0]) or command[0]
        if executable.lower().endswith(('.cmd', '.bat')):
            return subprocess.list2cmdline([executable, *command[1:]]), True
    return command, False


class CodexError(Exception):
    """A connection or protocol operation could not be completed."""


class AppServerTransport:
    def __init__(self, command, *, env=None, cwd=None):
        self.command, self.env, self.cwd = command, env, cwd
        self._process = None
        self._lock = Lock()
        self._write_lock = Lock()
        self._pending = {}
        self._subscribers = []
        self._next_id = 0
        self._reader = None

    @property
    def connected(self):
        return self._process is not None and self._process.poll() is None

    def start(self):
        if self.connected:
            return
        try:
            command, use_shell = _prepare_command(self.command)
            self._process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                # Keep helper diagnostics visible in the terminal that launched
                # Kern Radio. This is especially useful when the CLI fails to
                # start on a fresh Windows installation.
                stderr=None, text=True, encoding='utf-8',
                env=self.env, cwd=self.cwd, bufsize=1, shell=use_shell,
            )
        except OSError as exc:
            raise CodexError('The connection helper could not start. Install Codex CLI and try again.') from exc
        self._reader = Thread(target=self._read, daemon=True)
        self._reader.start()
        try:
            self.request('initialize', {'clientInfo': {
                'name': 'community_radio', 'title': 'Community Radio', 'version': '0.1.0',
            }, 'capabilities': {'experimentalApi': True}})
            self._send({'method': 'initialized', 'params': {}})
        except Exception:
            self.close()
            raise

    def _send(self, message):
        with self._write_lock:
            if not self.connected:
                raise CodexError('Connection lost. Choose Reconnect to try again.')
            try:
                self._process.stdin.write(json.dumps(message) + '\n')
                self._process.stdin.flush()
            except (OSError, ValueError) as exc:
                raise CodexError('Connection lost. Choose Reconnect to try again.') from exc

    def request(self, method, params, timeout=30):
        response = queue.Queue(maxsize=1)
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            self._pending[request_id] = response
        try:
            self._send({'id': request_id, 'method': method, 'params': params})
            try:
                message = response.get(timeout=timeout)
            except queue.Empty as exc:
                raise CodexError('The connection is taking too long. Please reconnect and try again.') from exc
            if 'error' in message:
                # Upstream errors can contain paths or other implementation details.
                raise CodexError('The request could not finish. Try again or reconnect your ChatGPT account.')
            return message['result']
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    @contextmanager
    def notifications(self):
        events = queue.Queue()
        with self._lock:
            self._subscribers.append(events)
        try:
            yield events
        finally:
            with self._lock:
                self._subscribers.remove(events)

    def respond(self, request_id, *, result=None, error=None):
        self._send({'id': request_id, 'error': error} if error is not None
                   else {'id': request_id, 'result': result})

    def _read(self):
        try:
            for line in self._process.stdout:
                message = json.loads(line)
                if 'method' in message and 'id' in message:
                    with self._lock:
                        subscribers = list(self._subscribers)
                    if subscribers:
                        for subscriber in subscribers:
                            subscriber.put(message)
                    else:
                        self.respond(message['id'], error={'code': -32601, 'message': 'No active tool consumer.'})
                else:
                    with self._lock:
                        if 'id' in message:
                            pending = self._pending.get(message['id'])
                            if pending is not None:
                                pending.put_nowait(message)
                        else:
                            for subscriber in self._subscribers:
                                subscriber.put(message)
        except (ValueError, OSError, CodexError, queue.Full):
            pass
        finally:
            with self._lock:
                for pending in self._pending.values():
                    if pending.empty():
                        pending.put({'error': {'message': 'Connection lost'}})
                for subscriber in self._subscribers:
                    subscriber.put({'method': 'connection/closed', 'params': {}})

    def close(self):
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if self._reader:
            self._reader.join(timeout=2)
        for stream in (process.stdin, process.stdout):
            stream.close()
        self._process = None
