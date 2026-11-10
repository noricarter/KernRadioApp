"""Compose the proven audio method with catalog references and actual history events."""
from threading import Condition, Lock
import logging
import uuid

from .browser import BrowserAudioBackend
from .resolver import PlaybackError, SunoAudioResolver

_TERMINAL = {'idle', 'completed', 'stopped', 'failed'}


class SunoPlaybackService:
    def __init__(self, catalog, history, *, resolver=None, backend=None):
        self._catalog, self._history = catalog, history
        self._resolver = resolver if resolver is not None else SunoAudioResolver()
        self._backend = backend if backend is not None else BrowserAudioBackend()
        self._condition = Condition()
        self._operation = Lock()
        self._closed = False
        self._status = {'state': 'idle', 'suno_song_id': None, 'session': None,
                        'event_id': None, 'position_seconds': 0, 'error': None}

    def status(self):
        with self._condition:
            return dict(self._status)

    def play(self, song_id_or_url, *, startup_timeout=20):
        with self._operation:
            with self._condition:
                if self._closed:
                    raise PlaybackError('This playback service has been closed.')
                if self._status['state'] not in _TERMINAL:
                    raise PlaybackError('Stop the current song before starting another.')
                self._status = {'state': 'resolving', 'suno_song_id': None, 'session': uuid.uuid4().hex,
                                'event_id': None, 'position_seconds': 0, 'error': None}
            try:
                audio = self._resolver.resolve(song_id_or_url)
                # Only create the minimal song reference required by the history FK.
                # This is not metadata ingestion and does not rewrite existing songs.
                self._catalog.get_or_create_song(audio.suno_song_id,
                    song_id_or_url if isinstance(song_id_or_url, str) and '://' in song_id_or_url
                    else f'https://suno.com/song/{audio.suno_song_id}')
                with self._condition:
                    self._status.update(state='starting', suno_song_id=audio.suno_song_id)
                    session = self._status['session']
                self._backend.play(audio, session, self._on_event)
                with self._condition:
                    ready = self._condition.wait_for(lambda: self._status['event_id'] is not None
                        or self._status['state'] in _TERMINAL, timeout=startup_timeout)
                    if not ready:
                        raise PlaybackError('The browser did not confirm audio playback. Check the desktop audio session.')
                    if self._status['state'] == 'failed':
                        raise PlaybackError(self._status['error'])
            except Exception as exc:
                logging.getLogger(__name__).exception('Audio startup failed; song=%s.', self._status.get('suno_song_id'))
                self._backend.stop()
                with self._condition:
                    self._status.update(state='failed', error=str(exc) if isinstance(exc, PlaybackError)
                                        else 'Audio playback could not start or be recorded.')
                    self._condition.notify_all()
                raise PlaybackError(self._status['error']) from exc
            return self.status()

    def _on_event(self, event):
        with self._condition:
            if event.get('session') != self._status['session'] or self._status['state'] in _TERMINAL:
                return
            kind = event.get('kind')
            position = event.get('position_seconds')
            if isinstance(position, (int, float)) and position >= 0:
                self._status['position_seconds'] = position
            try:
                if kind == 'playing':
                    if self._status['event_id'] is None:
                        recorded = self._history.start_playback(self._status['suno_song_id'])
                        self._status['event_id'] = recorded['id']
                    self._status['state'] = 'playing'
                    self._status['error'] = None
                elif kind == 'waiting':
                    self._status['state'] = 'buffering'
                elif kind in ('control_lost', 'control_paused'):
                    self._status['error'] = event.get('error') or 'The local audio connection was interrupted.'
                    if kind == 'control_paused':
                        self._status['state'] = 'buffering'
                    logging.getLogger(__name__).error('Audio control interruption; song=%s position=%s: %s',
                        self._status['suno_song_id'], self._status['position_seconds'], self._status['error'])
                elif kind == 'control_restored':
                    self._status['error'] = None
                elif kind in ('ended', 'stopped', 'error'):
                    event_id = self._status['event_id']
                    if event_id is not None:
                        if kind == 'ended':
                            self._history.finish_playback(event_id)
                        else:
                            self._history.interrupt_playback(event_id)
                    self._status['state'] = {'ended': 'completed', 'stopped': 'stopped', 'error': 'failed'}[kind]
                    if kind == 'error':
                        self._status['error'] = event.get('error') or 'Audio playback failed.'
                        logging.getLogger(__name__).error('Audio playback failed; song=%s position=%s media_error=%s: %s',
                            self._status['suno_song_id'], self._status['position_seconds'],
                            event.get('media_error_code'), self._status['error'])
                    elif kind == 'ended' and event_id is None:
                        self._status.update(state='failed', error='Audio ended without a confirmed playback start.')
            except Exception:
                logging.getLogger(__name__).exception('Playback history could not be saved; song=%s event=%s.',
                    self._status['suno_song_id'], self._status['event_id'])
                # Do not allow successful-looking playback with unrecorded history.
                self._status.update(state='failed', error='Playback history could not be saved. Audio was stopped; the event may need review.')
                self._backend.stop()
            self._condition.notify_all()

    def wait(self, timeout=None):
        with self._condition:
            if not self._condition.wait_for(lambda: self._status['state'] in _TERMINAL, timeout=timeout):
                raise TimeoutError('Audio is still playing.')
            return dict(self._status)

    def stop(self, *, timeout=10):
        with self._operation:
            if self.status()['state'] in _TERMINAL:
                return self.status()
            self._backend.stop()
            try:
                return self.wait(timeout)
            except TimeoutError:
                # Killing the owned browser confirms audio has actually stopped.
                self._backend.close()
                self._on_event({'session': self._status['session'], 'kind': 'error',
                                'error': 'The audio browser stopped responding and was closed.'})
                return self.status()

    def close(self):
        if self._closed:
            return
        try:
            self.stop()
        finally:
            self._backend.close()
            self._resolver.close()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
