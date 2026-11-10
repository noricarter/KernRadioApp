"""Own a Chrome HTML Audio client and a loopback-only event/file bridge."""
import json
import logging
import math
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread
import time
from urllib.parse import urlencode, urlsplit

from .resolver import PlaybackError


class _BridgeServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        logging.getLogger(__name__).exception('The audio bridge could not handle a browser request.')


class BrowserAudioBackend:
    def __init__(self, *, executable=None, display_url=None):
        self._executable = executable
        self._server = self._process = self._profile = None
        self._thread = self._watcher = None
        self._monitor = None
        self._closing = Event()
        self._lock = Lock()
        self._command = {'revision': 0, 'action': 'idle'}
        self._callback = None
        self._source = None
        self._prefix = '/' + secrets.token_urlsafe(32) + '/'
        self._display_url = display_url
        self._last_event_at = time.monotonic()
        self._resume_position = 0
        self._contact_failed = False

    def set_display_url(self, url):
        parsed = urlsplit(url)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1':
            raise PlaybackError('The radio display must use this computer’s local web address.')
        self._display_url = url

    def open_display(self):
        with self._lock:
            self._command = {'revision': self._command['revision'] + 1, 'action': 'idle'}
        self._start()

    def _start(self):
        if self._process and self._process.poll() is None:
            return
        candidates = ['google-chrome', 'chromium', 'chromium-browser', 'chrome', 'chrome.exe']
        if os.name == 'nt':
            candidates.extend(str(Path(base) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe')
                              for base in (os.environ.get('PROGRAMFILES'), os.environ.get('PROGRAMFILES(X86)'),
                                           os.environ.get('LOCALAPPDATA')) if base)
        elif sys.platform == 'darwin':
            candidates.append('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        executable = self._executable or next((value for name in candidates
                                               if (value := shutil.which(name) or (name if Path(name).is_file() else None))), None)
        if executable is None:
            raise PlaybackError('Chrome or Chromium is required for the browser audio method used by Crucible Radio.')
        if self._server is None:
            self._start_bridge()
        self._launch(executable)

    def _start_bridge(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                if not self.path.startswith(owner._prefix):
                    self.send_error(404)
                    return
                route = self.path[len(owner._prefix):]
                if route == '':
                    self._bytes(b'<!doctype html><title>Kern Radio audio service</title><p>Audio is controlled by the local playback service.</p><script src="player.js"></script>', 'text/html')
                elif route == 'player.js':
                    self._bytes(Path(__file__).with_name('player.js').read_bytes(), 'text/javascript')
                elif route == 'command':
                    with owner._lock:
                        command = dict(owner._command, resume_seconds=owner._resume_position)
                    self._bytes(json.dumps(command).encode(), 'application/json')
                elif route.startswith('audio/'):
                    with owner._lock:
                        source = owner._source
                        valid = route == 'audio/' + str(owner._command.get('session'))
                    if not valid or not isinstance(source, Path) or not source.is_file():
                        self.send_error(404)
                    else:
                        self._audio(source)
                else:
                    self.send_error(404)

            def _bytes(self, data, content_type, code=200):
                self.send_response(code)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _audio(self, source):
                size = source.stat().st_size
                start, end, code = 0, size - 1, 200
                requested = self.headers.get('Range')
                if requested:
                    match = re.fullmatch(r'bytes=(\d+)-(\d*)', requested)
                    if not match or int(match[1]) >= size:
                        self.send_error(416)
                        return
                    start = int(match[1])
                    end = min(int(match[2]), end) if match[2] else end
                    if end < start:
                        self.send_error(416)
                        return
                    code = 206
                self.send_response(code)
                self.send_header('Content-Type', 'audio/mp4')
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Length', str(end - start + 1))
                if code == 206:
                    self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                self.end_headers()
                try:
                    with source.open('rb') as stream:
                        stream.seek(start)
                        remaining = end - start + 1
                        while remaining > 0:
                            chunk = stream.read(min(65536, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_POST(self):
                if self.path != owner._prefix + 'event':
                    self.send_error(404)
                    return
                if self.headers.get('Origin') not in (None, owner._origin):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 4096:
                        raise ValueError()
                    event = json.loads(self.rfile.read(length))
                    if not isinstance(event, dict):
                        raise ValueError()
                except (ValueError, OSError):
                    self.send_error(400)
                    return
                with owner._lock:
                    if event.get('session') == owner._command.get('session'):
                        owner._last_event_at = time.monotonic()
                        position = event.get('position_seconds')
                        if type(position) in (int, float) and math.isfinite(position) and position >= 0:
                            owner._resume_position = position
                        if event.get('kind') in ('ended', 'stopped', 'error'):
                            owner._command = {'revision': owner._command['revision']+1,
                                'action':'idle', 'session':event['session']}
                callback = owner._callback
                if callback:
                    callback(event)
                self._bytes(b'{}', 'application/json')

        self._server = _BridgeServer(('127.0.0.1', 0), Handler)
        self._server.daemon_threads = True
        self._origin = f'http://127.0.0.1:{self._server.server_port}'
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._monitor = Thread(target=self._monitor_contact, name='audio-contact', daemon=True)
        self._monitor.start()

    def _check_contact(self, now=None):
        with self._lock:
            if (self._closing.is_set() or self._command['action'] != 'play' or self._contact_failed
                    or (time.monotonic() if now is None else now)-self._last_event_at < 15):
                return
            self._contact_failed = True
            session = self._command['session']
            self._command = {'revision':self._command['revision']+1, 'action':'stop', 'session':session}
            callback = self._callback
        logging.getLogger(__name__).error('The audio browser stopped reporting for 15 seconds; session=%s.', session)
        if callback:
            callback({'session':session, 'kind':'error',
                'error':'The audio browser stopped responding. The song was interrupted; reconnect the station window.'})

    def _monitor_contact(self):
        while not self._closing.wait(1):
            self._check_contact()
    def _launch(self, executable):
        if self._profile is None:
            self._profile = TemporaryDirectory(prefix='kern-audio-browser-')
        address = self._origin + self._prefix
        if self._display_url:
            address = self._display_url + '?' + urlencode({'audio_bridge': address})
        try:
            self._process = subprocess.Popen([executable, '--no-first-run', '--no-default-browser-check',
                '--autoplay-policy=no-user-gesture-required', '--disable-background-timer-throttling',
                '--window-size=1280,800', '--user-data-dir=' + self._profile.name, '--new-window', address],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise PlaybackError('The audio browser could not start.') from exc
        self._watcher = Thread(target=self._watch, args=(self._process,), daemon=True)
        self._watcher.start()

    def _watch(self, process):
        process.wait()
        if process is self._process and not self._closing.is_set() and self._callback:
            logging.getLogger(__name__).error('The audio browser exited unexpectedly; returncode=%s.', process.returncode)
            self._callback({'session': self._command.get('session'), 'kind': 'error',
                            'error': 'The audio browser closed unexpectedly.'})

    def play(self, audio, session, callback):
        self._callback = callback
        self._start()
        with self._lock:
            self._last_event_at = time.monotonic()
            self._resume_position = 0
            self._contact_failed = False
            self._source = audio.source
            self._command = {'revision': self._command['revision'] + 1, 'action': 'play', 'session': session,
                'src': self._origin + self._prefix + 'audio/' + session if isinstance(audio.source, Path) else audio.source}

    def stop(self):
        with self._lock:
            self._command = {'revision': self._command['revision'] + 1, 'action': 'stop',
                             'session': self._command.get('session')}

    def close(self):
        self._closing.set()
        if self._monitor:
            self._monitor.join(timeout=2)
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        if self._watcher:
            self._watcher.join(timeout=2)
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=2)
        if self._profile:
            self._profile.cleanup()
