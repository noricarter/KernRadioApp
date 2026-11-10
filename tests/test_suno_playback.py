"""Offline lifecycle tests: real SQLite/crypto, fake network and browser events."""
import base64
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from services.catalog import MusicCatalogService
from services.database import DatabaseService
from services.playback import PlaybackHistoryService, PlaybackMetricsService
from services.suno_playback import PlaybackError, ResolvedAudio, SunoAudioResolver, SunoPlaybackService

SONG = '0746f5e7-4d09-4d6b-9129-733c341f23eb'


class FakeBrowser:
    def __init__(self):
        self.callback = None
        self.session = None
        self.closed = False

    def play(self, audio, session, callback):
        self.session, self.callback = session, callback
        self.emit('playing')

    def emit(self, kind, **values):
        if self.callback:
            self.callback({'session': self.session, 'kind': kind, **values})

    def stop(self):
        self.emit('stopped')

    def close(self):
        self.closed = True


class LifecycleTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        database = DatabaseService(Path(temporary.name) / 'catalog.sqlite3')
        self.catalog = MusicCatalogService(database)
        self.history = PlaybackHistoryService(database, self.catalog)
        self.metrics = PlaybackMetricsService(database, self.catalog)
        self.resolver = Mock()
        self.resolver.resolve.return_value = ResolvedAudio(SONG, Path(temporary.name) / 'audio.m4a')
        self.browser = FakeBrowser()
        self.player = SunoPlaybackService(self.catalog, self.history, resolver=self.resolver, backend=self.browser)
        self.addCleanup(self.player.close)

    def test_start_requires_actual_playing_event_not_loading_or_buffering(self):
        def play(audio, session, callback):
            self.browser.session, self.browser.callback = session, callback
            self.browser.emit('waiting')
            self.assertEqual(self.history.list_events(), [])
            self.assertEqual(self.player.status()['state'], 'buffering')
            self.browser.emit('playing')
        self.browser.play = play
        status = self.player.play(SONG)
        self.assertEqual(status['state'], 'playing')
        self.assertEqual(len(self.history.list_events()), 1)
        self.assertIsNone(self.history.get_event(status['event_id'])['ended_at'])

    def test_ended_event_completes_history_and_metrics(self):
        self.player.play(SONG)
        self.browser.emit('progress', position_seconds=10)
        self.browser.emit('ended', position_seconds=12)
        status = self.player.wait(timeout=.1)
        self.assertEqual(status['state'], 'completed')
        self.assertEqual(status['position_seconds'], 12)
        event = self.history.get_event(status['event_id'])
        self.assertEqual(event['outcome'], 'completed')
        self.assertIsNotNone(event['ended_at'])
        self.assertEqual(self.metrics.song_statistics(SONG)['completed_plays'], 1)

    def test_stop_interrupts_and_can_play_another_attempt(self):
        self.player.play('https://suno.com/s/example')
        self.assertEqual(self.player.stop()['state'], 'stopped')
        self.player.play(SONG)
        self.browser.emit('ended')
        self.assertEqual([e['outcome'] for e in self.history.list_events()], ['interrupted', 'completed'])
        stats = self.metrics.song_statistics(SONG)
        self.assertEqual(stats['interrupted_plays'], 1)
        self.assertEqual(stats['completed_plays'], 1)

    def test_repeated_playing_and_terminal_events_do_not_duplicate_history(self):
        self.player.play(SONG)
        self.browser.emit('playing')
        self.browser.emit('waiting')
        self.browser.emit('playing')
        self.browser.emit('ended')
        original = self.history.list_events()
        self.browser.emit('ended')
        self.browser.emit('error')
        self.assertEqual(self.history.list_events(), original)
        self.assertEqual(len(original), 1)
        self.assertEqual(self.player.status()['state'], 'completed')

    def test_stale_browser_session_events_are_ignored(self):
        self.player.play(SONG)
        session = self.player.status()['session']
        self.player.stop()
        self.player.play(SONG)
        self.player._on_event({'session': session, 'kind': 'ended'})
        self.assertEqual(self.player.status()['state'], 'playing')
        self.assertIsNone(self.history.list_events()[-1]['outcome'])

    def test_playback_error_interrupts_started_history(self):
        self.player.play(SONG)
        self.browser.emit('error', error='Decode failed')
        self.assertEqual(self.player.wait(timeout=.1)['state'], 'failed')
        self.assertEqual(self.player.status()['error'], 'Decode failed')
        self.assertEqual(self.history.list_events()[0]['outcome'], 'interrupted')

    def test_connection_pause_and_resume_preserve_the_same_history_event(self):
        self.player.play(SONG)
        event_id = self.player.status()['event_id']
        with self.assertLogs('services.suno_playback.service', level='ERROR') as logs:
            self.browser.emit('control_lost', error='Temporary control loss')
            self.browser.emit('control_paused', error='Waiting for control')
        self.assertEqual(self.player.status()['state'], 'buffering')
        self.assertIn(SONG, '\n'.join(logs.output))
        self.assertIsNone(self.history.get_event(event_id)['outcome'])
        self.browser.emit('control_restored')
        self.browser.emit('playing')
        self.assertEqual(self.player.status()['event_id'], event_id)
        self.assertIsNone(self.player.status()['error'])
        self.browser.emit('ended')
        self.assertEqual(len(self.history.list_events()), 1)
        self.assertEqual(self.metrics.song_statistics(SONG)['completed_plays'], 1)

    def test_media_failure_is_logged_with_song_position_and_error_code(self):
        self.player.play(SONG)
        with self.assertLogs('services.suno_playback.service', level='ERROR') as logs:
            self.browser.emit('error', error='Decode failed', position_seconds=12, media_error_code=3)
        text = '\n'.join(logs.output)
        self.assertIn(SONG, text)
        self.assertIn('position=12', text)
        self.assertIn('media_error=3', text)

    def test_resolution_failure_creates_no_history(self):
        self.resolver.resolve.side_effect = PlaybackError('Unavailable song')
        with self.assertRaisesRegex(PlaybackError, 'Unavailable'):
            self.player.play(SONG)
        self.assertEqual(self.player.status()['state'], 'failed')
        self.assertEqual(self.history.list_events(), [])
        self.assertEqual(self.catalog.list_songs(), [])

    def test_browser_startup_failure_has_no_false_start(self):
        self.browser.play = Mock(side_effect=PlaybackError('Browser missing'))
        with self.assertRaisesRegex(PlaybackError, 'Browser missing'):
            self.player.play(SONG)
        self.assertEqual(self.history.list_events(), [])

    def test_missing_playing_confirmation_times_out_without_fabricating_history(self):
        self.browser.play = Mock()
        with self.assertRaisesRegex(PlaybackError, 'did not confirm'):
            self.player.play(SONG, startup_timeout=.01)
        self.assertEqual(self.history.list_events(), [])

    def test_history_write_failure_stops_audio_and_reports_failure(self):
        self.history.start_playback = Mock(side_effect=RuntimeError('database unavailable'))
        self.browser.stop = Mock()
        with self.assertRaisesRegex(PlaybackError, 'history could not be saved'):
            self.player.play(SONG)
        self.browser.stop.assert_called()
        self.assertEqual(self.history.list_events(), [])

    def test_wait_timeout_does_not_mark_completion(self):
        self.player.play(SONG)
        with self.assertRaises(TimeoutError):
            self.player.wait(timeout=.01)
        self.assertEqual(self.player.status()['state'], 'playing')
        self.assertIsNone(self.history.list_events()[0]['outcome'])

    def test_overlapping_play_requests_are_rejected(self):
        self.player.play(SONG)
        with self.assertRaisesRegex(PlaybackError, 'Stop the current song'):
            self.player.play(SONG)
        self.assertEqual(len(self.history.list_events()), 1)

    def test_close_interrupts_releases_resources_and_rejects_new_play(self):
        self.player.play(SONG)
        self.player.close()
        self.assertTrue(self.browser.closed)
        self.resolver.close.assert_called_once()
        self.assertEqual(self.history.list_events()[0]['outcome'], 'interrupted')
        with self.assertRaises(PlaybackError):
            self.player.play(SONG)

    def test_existing_catalog_metadata_and_artist_are_preserved(self):
        artist = self.catalog.create_artist(preferred_display_alias='𝓐rtist')
        original, _ = self.catalog.get_or_create_song(SONG, 'original-url', title='Original', artist_id=artist['id'])
        self.player.play(SONG)
        self.assertEqual(self.catalog.get_song(SONG), original)


class BrowserContactTests(TestCase):
    def test_missing_contact_is_logged_once_and_stops_the_old_session(self):
        from services.suno_playback import BrowserAudioBackend
        backend = BrowserAudioBackend()
        self.addCleanup(backend.close)
        callback = Mock()
        backend._callback = callback
        backend._command = {'revision': 1, 'action': 'play', 'session': 'lost-session'}
        with self.assertLogs('services.suno_playback.browser', level='ERROR'):
            backend._check_contact(backend._last_event_at + 16)
        self.assertEqual(backend._command['action'], 'stop')
        self.assertEqual(callback.call_args.args[0]['kind'], 'error')
        backend._check_contact(backend._last_event_at + 60)
        callback.assert_called_once()


class ResolverTests(TestCase):
    def test_reference_rights_decryption_and_temporary_cache(self):
        resolver = SunoAudioResolver()
        self.addCleanup(resolver.close)
        audio = b'\x00\x00\x00\x18ftypM4A ' + b'audio bytes' * 10
        key, counter, nonce = b'k' * 16, b'i' * 16, b'n' * 12
        encryptor = Cipher(algorithms.AES(key), modes.CTR(counter)).encryptor()
        encrypted = encryptor.update(audio) + encryptor.finalize()
        wrapper = AESGCM(hashlib.sha256(b'guest-token').digest())
        rights = {'glt': 'guest-token',
            'key': base64.b64encode(nonce + wrapper.encrypt(nonce, key, SONG.encode())).decode(),
            'iv': base64.b64encode(nonce + wrapper.encrypt(nonce, counter, SONG.encode())).decode()}
        page = ('{"media_urls":["https://cdn1.suno.ai/' + SONG + '.m4a"]}').encode()
        resolver._identities.resolve_song_id = Mock(return_value=SONG)
        resolver._read = Mock(side_effect=[page, json.dumps(rights).encode(), encrypted])
        result = resolver.resolve(SONG)
        self.assertEqual(result.source.read_bytes(), audio)
        self.assertEqual(resolver.resolve(SONG), result)
        self.assertEqual(resolver._read.call_count, 3)
        resolver.close()
        self.assertFalse(result.source.exists())

    def test_unplayable_payload_is_rejected(self):
        resolver = SunoAudioResolver()
        self.addCleanup(resolver.close)
        resolver._identities.resolve_song_id = Mock(return_value=SONG)
        page = ('{"media_urls":["https://cdn1.suno.ai/' + SONG + '.m4a"]}').encode()
        resolver._read = Mock(side_effect=[page, b'{"glt":"guest","key":"","iv":""}', b'bad'])
        with self.assertRaises(PlaybackError):
            resolver.resolve(SONG)

    def test_source_reference_mp3_fallback(self):
        resolver = SunoAudioResolver()
        self.addCleanup(resolver.close)
        resolver._identities.resolve_song_id = Mock(return_value=SONG)
        url = 'https://cdn1.suno.ai/' + SONG + '.mp3'
        resolver._read = Mock(return_value=('{"audio_url":"' + url + '"}').encode())
        self.assertEqual(resolver.resolve(SONG).source, url)

    def test_unavailable_audio_is_reported(self):
        resolver = SunoAudioResolver()
        self.addCleanup(resolver.close)
        resolver._identities.resolve_song_id = Mock(return_value=SONG)
        resolver._read = Mock(return_value=b'<html>No audio</html>')
        with self.assertRaisesRegex(PlaybackError, 'No playable audio'):
            resolver.resolve(SONG)
