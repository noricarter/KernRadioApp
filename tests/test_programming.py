"""Programming lifecycle with real isolated history; agent/audio callbacks are fakes."""
from concurrent.futures import Future
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from unittest import TestCase
from unittest.mock import Mock

from radio.programming import RadioProgramming
from radio.station_tools import StationTools
from services.catalog import MusicCatalogService
from services.database import DatabaseService
from services.dialogue import DialogueService
from services.playback import PlaybackHistoryService, PlaybackMetricsService
from services.suno_playback import SunoPlaybackService, ResolvedAudio, PlaybackError


class FastDialogue(DialogueService):
    def __init__(self):
        super().__init__()
        self.shown = []

    def show(self, text):
        self.shown.append(text)
        return super().show(text)

    def _duration(self, text):
        return .01


class AudioEvents:
    def __init__(self):
        self.callback = None
        self.session = None
        self.plays = []
        self.stops = 0

    def play(self, audio, session, callback):
        self.callback, self.session = callback, session
        self.plays.append(audio.suno_song_id)
        self.emit('playing')

    def emit(self, kind):
        self.callback({'kind': kind, 'session': self.session, 'position_seconds': 1})

    def stop(self):
        self.stops += 1
        if self.callback:
            self.emit('stopped')

    def close(self):
        pass


class DialogueTests(TestCase):
    def test_pages_preserve_unicode_and_allow_animation_plus_reading_time(self):
        now = [0]
        dialogue = DialogueService(page_characters=20, characters_per_second=10, words_per_second=2,
                                   minimum_seconds=2, clock=lambda: now[0])
        text = 'Hello 🌲 fellow explorers! Here comes a little musical treasure.'
        dialogue.show(text)
        pages = []
        while True:
            state = dialogue.snapshot()
            pages.append(state['text'])
            self.assertLessEqual(len(state['text']), 20)
            self.assertFalse(dialogue.advance())
            now[0] += max(2, state['reveal_seconds'] + len(state['text'].split()) / 2 + 1) + .001
            if dialogue.advance():
                break
        self.assertEqual(' '.join(pages), text)
        self.assertGreater(state['reveal_seconds'], 0)

    def test_rewrite_resets_page_and_timing(self):
        now = [0]
        dialogue = DialogueService(clock=lambda: now[0])
        first = dialogue.show('First message')
        now[0] = 10
        second = dialogue.show('Replacement 🌙')
        self.assertNotEqual(first, second)
        self.assertEqual(dialogue.snapshot()['text'], 'Replacement 🌙')
        self.assertEqual(dialogue.snapshot()['elapsed_seconds'], 0)
        with self.assertRaises(ValueError):
            dialogue.show('x' * 601)


class ProgrammingTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        database = DatabaseService(Path(temporary.name) / 'catalog.sqlite3')
        self.catalog = MusicCatalogService(database)
        self.history = PlaybackHistoryService(database, self.catalog)
        for identifier in ('a', 'b', 'c'):
            artist = self.catalog.create_artist(preferred_display_alias='Artist ' + identifier)
            self.catalog.get_or_create_song(identifier, 'url', title='Song ' + identifier, lyrics='Actual test lyrics', artist_id=artist['id'])
        self.audio = AudioEvents()
        self.resolver = Mock()
        self.resolver.resolve.side_effect = lambda identifier: ResolvedAudio(identifier, Path(temporary.name) / 'audio.m4a')
        self.playback = SunoPlaybackService(self.catalog, self.history, backend=self.audio, resolver=self.resolver)
        self.dialogue = FastDialogue()
        self.tasks = []
        def request(text):
            future = Future()
            self.tasks.append((text, future))
            return future
        self.open_display = Mock()
        self.program = RadioProgramming(self.catalog, self.playback, self.dialogue,
            metrics=PlaybackMetricsService(database, self.catalog), request_agent=request, open_frontend=self.open_display)
        self.addCleanup(self.playback.close)
        self.addCleanup(self.program.close)

    def wait_for(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail('Timed out: ' + str(self.program.status()))

    @staticmethod
    def track(identifier):
        return {'suno_song_id': identifier, 'introduction': 'Introducing Song ' + identifier + ' by Artist ' + identifier,
                'outro': 'That was Song ' + identifier + ' by Artist ' + identifier}

    def prepare(self, identifiers=('a', 'b')):
        self.program.start()
        self.wait_for(lambda: len(self.tasks) == 1)
        self.program.set_program(self.program.status()['revision'], [self.track(identifier) for identifier in identifiers],
                                 greeting='Hey, explorers! Welcome to Kern Radio!', signoff='Thanks for tuning in. Adventure awaits next time!')
        self.tasks[0][1].set_result('Prepared')

    def finish_current_and_stop(self):
        self.program.stop()
        self.audio.emit('ended')
        self.wait_for(lambda: not self.program.status()['running'])

    def test_stop_waits_for_actual_end_then_outro_and_signoff_without_next_song(self):
        self.prepare()
        self.wait_for(lambda: self.playback.status()['state'] == 'playing')
        self.program.stop()
        self.assertEqual(self.audio.stops, 0)
        self.assertTrue(self.program.status()['stop_requested'])
        self.assertEqual(self.program.status()['queue'], [])
        self.assertIsNone(self.history.list_events()[0]['outcome'])
        self.audio.emit('ended')
        self.wait_for(lambda: not self.program.status()['running'])
        self.assertEqual(self.audio.plays, ['a'])
        self.assertEqual(self.history.list_events()[0]['outcome'], 'completed')
        self.assertEqual(self.dialogue.shown[-2:], [self.track('a')['outro'], 'Thanks for tuning in. Adventure awaits next time!'])

    def test_natural_completion_introduces_and_plays_next(self):
        self.prepare()
        self.wait_for(lambda: self.audio.plays == ['a'])
        self.audio.emit('ended')
        self.wait_for(lambda: self.audio.plays == ['a', 'b'])
        self.assertLess(self.dialogue.shown.index(self.track('a')['outro']), self.dialogue.shown.index(self.track('b')['introduction']))
        self.finish_current_and_stop()
        self.assertEqual([event['outcome'] for event in self.history.list_events()], ['completed', 'completed'])

    def test_requested_song_goes_next_and_dialogue_can_be_rewritten_during_music(self):
        self.prepare(('a', 'c'))
        self.wait_for(lambda: self.audio.plays == ['a'])
        self.program.queue_song(self.program.status()['revision'], **self.track('b'))
        self.program.say('A fresh adventure is coming up, friends!')
        self.program.rewrite_script(self.program.status()['revision'], 'current_outro', 'That was Song a by Artist a. Another treasure awaits!')
        self.assertEqual(self.program.status()['dialogue']['text'], 'A fresh adventure is coming up, friends!')
        self.assertEqual(self.audio.stops, 0)
        self.audio.emit('ended')
        self.wait_for(lambda: self.audio.plays == ['a', 'b'])
        self.assertIn('That was Song a by Artist a. Another treasure awaits!', self.dialogue.shown)
        self.finish_current_and_stop()

    def test_explicit_immediate_switch_records_interruption(self):
        self.prepare(('a', 'c'))
        self.wait_for(lambda: self.audio.plays == ['a'])
        self.program.queue_song(self.program.status()['revision'], **self.track('b'), interrupt_current=True)
        self.wait_for(lambda: self.audio.plays == ['a', 'b'])
        self.assertEqual(self.history.list_events()[0]['outcome'], 'interrupted')
        self.finish_current_and_stop()

    def test_planned_mascots_follow_real_stages_and_reset_after_signoff(self):
        self.assertEqual(self.program.public_state()['mascot'], 'standard')
        spoken = []
        speak = self.program._speak
        def record(text, phase, **options):
            spoken.append((phase, options.get('mascot', 'standard')))
            return speak(text, phase, **options)
        self.program._speak = record
        self.program.start()
        self.wait_for(lambda: self.tasks)
        self.program.set_program(self.program.status()['revision'],
            [self.track('a') | {'introduction_mascot': 'explain', 'playing_mascot': 'headphones', 'outro_mascot': 'consider'}],
            greeting='Welcome!', signoff='Thanks for listening!', greeting_mascot='excited', signoff_mascot='cute')
        self.tasks[0][1].set_result('Prepared')
        self.wait_for(lambda: self.program.status()['phase'] == 'playing')
        self.assertEqual(self.program.public_state()['mascot'], 'headphones')
        self.program.say('A little musical surprise!', mascot='surprise')
        self.assertEqual(self.program.public_state()['mascot'], 'surprise')
        self.program.rewrite_script(self.program.status()['revision'], 'current_outro', 'That was Song a.', mascot='mischief')
        self.finish_current_and_stop()
        self.assertEqual(spoken, [('greeting', 'excited'), ('introducing', 'explain'), ('outro', 'mischief'), ('signing_off', 'cute')])
        self.assertEqual(self.program.public_state()['mascot'], 'standard')
        self.assertEqual(self.audio.stops, 0)
        self.assertEqual(self.history.list_events()[0]['outcome'], 'completed')

    def test_mascot_tool_does_not_change_playback_queue_or_dialogue(self):
        self.prepare()
        self.wait_for(lambda: self.program.status()['phase'] == 'playing')
        tools = StationTools(Mock(catalog=self.catalog), Mock(), Mock(), programming=self.program)
        before = self.program.status()
        result = tools.dispatch('set_host_mascot', {'mascot': 'sad'})
        self.assertEqual(result, {'mascot': 'sad'})
        after = self.program.public_state()
        self.assertEqual(after['mascot'], 'sad')
        self.assertEqual(after['revision'], before['revision'])
        self.assertEqual(after['dialogue']['id'], before['dialogue']['id'])
        self.assertEqual([s['id'] for s in after['queue']], [s['id'] for s in before['queue']])
        self.assertEqual(self.audio.stops, 0)
        for invalid in ('../main.py', 'https://example.com/image.png', 'ecstatic', None):
            with self.assertRaises(ValueError):
                tools.dispatch('set_host_mascot', {'mascot': invalid})
        self.assertEqual(self.program.public_state()['mascot'], 'sad')
        self.finish_current_and_stop()

    def test_invalid_planned_mascot_is_rejected_without_partial_changes(self):
        self.program.start()
        self.wait_for(lambda: self.tasks)
        revision = self.program.status()['revision']
        for options in ({'tracks': [self.track('a') | {'playing_mascot': 'missing'}]}, {'greeting_mascot': 'missing'}, {'signoff_mascot': 'missing'}):
            arguments = dict(expected_revision=revision, tracks=[self.track('a')], greeting='Hi', signoff='Bye') | options
            with self.assertRaises(ValueError):
                self.program.set_program(**arguments)
        self.assertEqual(self.program.status()['revision'], revision)
        self.assertEqual(self.program.status()['queue'], [])
        self.assertEqual(self.program.status()['mascot'], 'standard')

    def test_played_song_requires_reason_and_rejection_preserves_existing_plan(self):
        self.history.record_playback('b', started_at='2026-11-09T10:00:00Z', outcome='interrupted')
        self.prepare(('a', 'c'))
        self.wait_for(lambda: self.program.status()['phase'] == 'playing')
        before = self.program.status()
        for operation in (lambda: self.program.set_program(before['revision'], [self.track('b')]),
                          lambda: self.program.queue_song(before['revision'], **self.track('b'))):
            with self.assertRaisesRegex(ValueError, '1 recorded starts .*0 completed'):
                operation()
            self.assertEqual(self.program.status()['revision'], before['revision'])
            self.assertEqual(self.program.status()['queue'], before['queue'])
        self.program.queue_song(before['revision'], **self.track('b'), repeat_reason='Kern explicitly asked to hear Song b again.')
        self.assertIn('explicitly asked', self.program.status()['queue'][0]['repeat_reason'])
        self.assertEqual(self.audio.stops, 0)
        self.finish_current_and_stop()

    def test_double_start_does_not_open_duplicate_display_or_agent_turn(self):
        self.prepare()
        self.program.start()
        self.open_display.assert_called_once()
        self.wait_for(lambda: self.audio.plays == ['a'])
        self.assertEqual(len(self.audio.plays), 1)
        self.finish_current_and_stop()

    def test_stop_during_planning_never_starts_audio_and_rejects_late_plan(self):
        self.program.start()
        self.wait_for(lambda: self.tasks)
        self.program.stop()
        self.wait_for(lambda: not self.program.status()['running'])
        self.assertEqual(self.audio.plays, [])
        with self.assertRaises(ValueError):
            self.program.set_program(self.program.status()['revision'], [self.track('a')], greeting='Hello', signoff='Bye')
        with self.assertRaises(ValueError):
            self.program.start()  # Old agent turn is still pending.

    def test_stop_during_intro_signs_off_without_starting_a_song(self):
        advance = self.dialogue.advance
        self.dialogue.advance = lambda: False if self.dialogue.snapshot()['text'].startswith('Introducing') else advance()
        self.prepare()
        self.wait_for(lambda: self.program.status()['phase'] == 'introducing')
        self.program.stop()
        self.wait_for(lambda: not self.program.status()['running'])
        self.assertEqual(self.audio.plays, [])
        self.assertEqual(self.history.list_events(), [])

    def test_bad_or_stale_plan_cannot_change_queue_or_approve_policies(self):
        self.program.start()
        self.wait_for(lambda: self.tasks)
        revision = self.program.status()['revision']
        for changes in ({'expected_revision': revision - 1}, {'tracks': [self.track('missing')]},
                        {'tracks': [self.track('a'), self.track('a')]},
                        {'tracks': [self.track('a') | {'outro': 'x' * 361}]}):
            arguments = dict(expected_revision=revision, tracks=[self.track('a')], greeting='Hi', signoff='Bye') | changes
            with self.assertRaises(ValueError):
                self.program.set_program(**arguments)
        self.assertEqual(self.program.status()['queue'], [])
        info = Mock(catalog=self.catalog)
        tools = StationTools(info, Mock(), Mock(), programming=self.program)
        with self.assertRaises(ValueError):
            tools.dispatch('approve_station_policy', {})

    def test_three_start_failures_stop_without_fabricating_history(self):
        self.resolver.resolve.side_effect = PlaybackError('Unavailable test audio')
        self.prepare(('a', 'b', 'c'))
        self.wait_for(lambda: not self.program.status()['running'])
        self.assertEqual(self.program.status()['phase'], 'failed')
        self.assertEqual(self.history.list_events(), [])

    def test_agent_failure_reports_error_and_signs_off(self):
        self.program.start()
        self.wait_for(lambda: self.tasks)
        self.tasks[0][1].set_exception(ValueError('Agent could not prepare a program'))
        self.wait_for(lambda: not self.program.status()['running'])
        self.assertEqual(self.program.status()['phase'], 'failed')
        self.assertIn('could not prepare', self.program.status()['error'])
        self.assertEqual(self.audio.plays, [])
