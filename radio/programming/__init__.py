"""Application programming lifecycle. The agent selects and scripts; services play."""
from threading import Condition, Thread
import logging

from services.codex import CodexError
from services.suno_playback import PlaybackError
from .prompts import planning_request
from .presentation import MASCOTS, validate_mascot


class RadioProgramming:
    def __init__(self, catalog, playback, dialogue, *, metrics, request_agent, open_frontend):
        self.catalog, self.playback, self.dialogue = catalog, playback, dialogue
        self.metrics = metrics
        self._request_agent, self._open_frontend = request_agent, open_frontend
        self._condition = Condition()
        self._thread = None
        self._closed = False
        self._running, self._stopping, self._interrupt = False, False, False
        self._phase, self._revision = 'idle', 0
        self._queue, self._current, self._last = [], None, None
        self._greeting = self._signoff = None
        self._mascot = self._greeting_mascot = self._signoff_mascot = 'standard'
        self._plan_future, self._plan_error = None, None
        self._error, self._failed_ids = None, []

    @staticmethod
    def _script(text):
        if not isinstance(text, str) or not text.strip() or len(text) > 360:
            raise ValueError('Keep each host script between 1 and 360 characters (aim for 180).')
        return text.strip()

    def _track(self, value):
        required = {'suno_song_id', 'introduction', 'outro'}
        expressions = {'introduction_mascot', 'playing_mascot', 'outro_mascot'}
        if not isinstance(value, dict) or required - value.keys() or value.keys() - required - expressions - {'repeat_reason'}:
            raise ValueError('Each track needs a Suno song ID, introduction, and outro.')
        if not isinstance(value['suno_song_id'], str):
            raise ValueError('Choose a Suno song ID from the catalog.')
        song = self.catalog.get_song(value['suno_song_id'])
        if song is None:
            raise ValueError('Choose an existing Suno catalog song.')
        reason = self._script(value['repeat_reason']) if 'repeat_reason' in value else None
        return {'suno_song_id': song['suno_song_id'], 'introduction': self._script(value['introduction']), 'repeat_reason': reason,
                'outro': self._script(value['outro'])} | {
                    key: validate_mascot(value.get(key, 'standard')) for key in expressions}

    def _check_repeats(self, tracks):
        for track in tracks:
            stats = self.metrics.song_statistics(track['suno_song_id'])
            if stats['started_plays'] and not track['repeat_reason']:
                song = self.catalog.get_song(track['suno_song_id'])
                raise ValueError(f'Playback history check: {song["title"]!r} has {stats["started_plays"]} recorded starts '
                    f'({stats["completed_plays"]} completed), last started {stats["last_played_at"]}. '
                    'Choose a different song, or supply repeat_reason explaining Kern’s explicit replay request '
                    'or why repetition is necessary for this program. Do not claim this song is unheard.')

    def _check_revision(self, expected_revision):
        if not self._running or self._closed:
            raise ValueError('Start Programming before changing the live program.')
        if self._stopping:
            raise ValueError('The station is finishing its current song and signing off.')
        if type(expected_revision) is not int or expected_revision != self._revision:
            raise ValueError('The program changed. Read station_programming and use its current revision.')

    def set_program(self, expected_revision, tracks, *, greeting=None, signoff=None,
                    greeting_mascot=None, signoff_mascot=None):
        if not isinstance(tracks, list) or not 1 <= len(tracks) <= 6:
            raise ValueError('Supply 1–6 upcoming tracks. More can be prepared while music plays.')
        prepared = [self._track(track) for track in tracks]
        if len({track['suno_song_id'] for track in prepared}) != len(prepared):
            raise ValueError('Each upcoming song must appear only once in the queue.')
        greeting = self._script(greeting) if greeting is not None else None
        signoff = self._script(signoff) if signoff is not None else None
        if greeting_mascot is not None:
            validate_mascot(greeting_mascot)
        if signoff_mascot is not None:
            validate_mascot(signoff_mascot)
        with self._condition:
            self._check_revision(expected_revision)
            if self._greeting is None and greeting is None or self._signoff is None and signoff is None:
                raise ValueError('The first program needs a greeting and a signoff.')
            if self._current and any(track['suno_song_id'] == self._current['suno_song_id'] for track in prepared):
                raise ValueError('The current song is already on air. Supply upcoming songs only.')
            if any(track['suno_song_id'] in self._failed_ids for track in prepared):
                raise ValueError('A selected song failed during this run. Choose another song.')
            self._check_repeats(prepared)
            self._queue = prepared
            self._plan_error = None
            self._greeting = greeting or self._greeting
            self._signoff = signoff or self._signoff
            self._greeting_mascot = greeting_mascot or self._greeting_mascot
            self._signoff_mascot = signoff_mascot or self._signoff_mascot
            self._revision += 1
            self._condition.notify_all()
            return self.status()

    def queue_song(self, expected_revision, suno_song_id, introduction, outro, *, interrupt_current=False,
                   introduction_mascot='standard', playing_mascot='standard', outro_mascot='standard',
                   repeat_reason=None, position='next'):
        values = dict(suno_song_id=suno_song_id, introduction=introduction, outro=outro,
            introduction_mascot=introduction_mascot, playing_mascot=playing_mascot, outro_mascot=outro_mascot)
        if repeat_reason is not None:
            values['repeat_reason'] = repeat_reason
        track = self._track(values)
        if type(interrupt_current) is not bool:
            raise ValueError('Interrupt must be true or false.')
        if position not in ('next', 'append'):
            raise ValueError('Choose whether the song plays next or is appended to the queue.')
        with self._condition:
            self._check_revision(expected_revision)
            if suno_song_id in self._failed_ids:
                raise ValueError('This song failed during this run; choose another song or restart programming.')
            self._check_repeats([track])
            remaining = [item for item in self._queue if item['suno_song_id'] != suno_song_id]
            if position == 'append' and len(remaining) >= 6:
                raise ValueError('The upcoming queue is full. Wait for a song to finish, then append it again.')
            self._queue = ([track] + remaining[:5] if position == 'next'
                           else remaining + [track])
            self._plan_error = None
            self._interrupt = interrupt_current
            self._revision += 1
            self._condition.notify_all()
            return self.status()

    def set_mascot(self, mascot):
        mascot = validate_mascot(mascot)
        with self._condition:
            if self._closed:
                raise ValueError('The station has been closed.')
            self._mascot = mascot
            return {'mascot': self._mascot}

    def say(self, text, mascot=None):
        text = self._script(text)
        if mascot is not None:
            validate_mascot(mascot)
        with self._condition:
            if not self._running or self._stopping:
                raise ValueError('Host dialogue can be changed during an active program.')
            self.dialogue.show(text)
            if mascot is not None:
                self._mascot = mascot
            self._condition.notify_all()
            return {'dialogue': self.dialogue.snapshot(), 'mascot': self._mascot}

    def rewrite_script(self, expected_revision, target, text, mascot=None):
        text = self._script(text)
        if mascot is not None:
            validate_mascot(mascot)
        with self._condition:
            if not self._running or expected_revision != self._revision:
                raise ValueError('Read the active program and its current revision before editing a script.')
            if target == 'current_outro' and self._current is not None:
                self._current['outro'] = text
                if mascot is not None:
                    self._current['outro_mascot'] = mascot
            elif target == 'signoff':
                self._signoff = text
                if mascot is not None:
                    self._signoff_mascot = mascot
                if self._phase == 'signing_off':
                    self.dialogue.show(text)
                    if mascot is not None:
                        self._mascot = mascot
            else:
                raise ValueError('Choose the current song outro or station signoff.')
            self._revision += 1
            self._condition.notify_all()
            return self.status()

    def _describe(self, track, *, scripts=False):
        if track is None:
            return None
        song = self.catalog.get_song(track['suno_song_id'])
        artist = self.catalog.get_artist(song['artist_id']) if song['artist_id'] else None
        result = {'id': song['suno_song_id'], 'suno_song_id': song['suno_song_id'], 'title': song['title'] or 'Untitled song',
                  'artist': artist['preferred_display_alias'] if artist and artist['preferred_display_alias'] else 'Artist not assigned',
                  'style': song['style'], 'artworkUrl': '/api/catalog/' + song['suno_song_id'] + '/artwork' if song['artwork_file'] else None}
        if scripts:
            result |= {key: track[key] for key in ('introduction', 'outro',
                'introduction_mascot', 'playing_mascot', 'outro_mascot')}
        if track['repeat_reason']:
            result['repeat_reason'] = track['repeat_reason']
        return result

    def status(self, *, scripts=True):
        with self._condition:
            return {'running': self._running, 'phase': self._phase, 'stop_requested': self._stopping,
                    'revision': self._revision, 'current': self._describe(self._current, scripts=scripts),
                    'queue': [self._describe(track, scripts=scripts) for track in self._queue],
                    'last_played': self._describe(self._last), 'dialogue': self.dialogue.snapshot(),
                    'mascot': self._mascot, 'available_mascots': list(MASCOTS),
                    'greeting_mascot': self._greeting_mascot, 'signoff_mascot': self._signoff_mascot,
                    'error': self._error, 'failed_song_ids': list(self._failed_ids)}

    def public_state(self):
        state = self.status(scripts=False)
        playback = self.playback.status()
        state['playback'] = playback['state']
        state['position_seconds'] = playback.get('position_seconds', 0)
        state['playback_error'] = playback.get('error')
        return state

    def start(self):
        with self._condition:
            if self._closed:
                raise ValueError('The station has been closed.')
            if self._running:
                return self.status()
            if self._plan_future is not None and not self._plan_future.done():
                raise ValueError('The host is finishing its previous response. Please wait a moment.')
            self._running, self._stopping, self._interrupt = True, False, False
            self._phase, self._error, self._plan_error = 'planning', None, None
            self._queue, self._current, self._last, self._failed_ids = [], None, None, []
            self._greeting = self._signoff = None
            self._mascot = self._greeting_mascot = self._signoff_mascot = 'standard'
            self.dialogue.show('Your host is gathering the next musical adventure. Stay tuned!')
            self._plan_future = None
            self._revision += 1
            self._thread = Thread(target=self._run, name='radio-programming', daemon=True)
            self._thread.start()
            return self.status()

    def stop(self):
        """Drain the actual current song. Never calls playback.stop()."""
        with self._condition:
            if self._running:
                self._stopping = True
                self._queue = []
                self._revision += 1
                self._condition.notify_all()
            return self.status()

    def _pause(self, seconds=.15):
        with self._condition:
            self._condition.wait(timeout=seconds)

    def _submit_plan(self, *, opening=False):
        if self._plan_future is not None or self._stopping or self._closed:
            return
        self._plan_future = self._request_agent(planning_request(self.status(), opening=opening))

    def _poll_plan(self):
        if self._plan_future is None or not self._plan_future.done():
            return
        future, self._plan_future = self._plan_future, None
        try:
            future.result()
            if not self._queue and not self._stopping:
                self._plan_error = 'The host did not supply a playable program. Ask your host to prepare a queue, then start again.'
        except Exception as exc:
            logging.getLogger(__name__).exception('Agent could not finish a programming request.')
            self._plan_error = str(exc) if isinstance(exc, (CodexError, ValueError)) else 'The host could not prepare the program. Please try again.'

    def _await_plan(self, *, opening=False):
        if self._plan_error and not self._queue:
            raise ValueError(self._plan_error)
        self._submit_plan(opening=opening)
        while not self._closed and not self._stopping:
            self._poll_plan()
            if self._queue and self._plan_future is None:
                return True
            if self._plan_error:
                raise ValueError(self._plan_error)
            if self._plan_future is None:
                self._submit_plan(opening=opening)
            self._pause()
        return False

    def _speak(self, text, phase, *, mascot='standard', allow_stop=True):
        with self._condition:
            self._phase = phase
            self._mascot = mascot
            self.dialogue.show(text)
        while not self._closed and not self.dialogue.advance():
            if allow_stop and self._stopping:
                return
            self._pause()

    def _run(self):
        fatal = False
        try:
            self._open_frontend()
            if not self._await_plan(opening=True):
                return
            self._speak(self._greeting, 'greeting', mascot=self._greeting_mascot)
            consecutive_failures = 0
            while not self._closed and not self._stopping:
                if not self._queue:
                    with self._condition:
                        self._phase = 'planning'
                    if not self._await_plan():
                        break
                with self._condition:
                    self._current = self._queue.pop(0)
                    self._revision += 1
                    track = dict(self._current)
                self._speak(track['introduction'], 'introducing', mascot=track['introduction_mascot'])
                if self._closed or self._stopping:
                    break
                try:
                    with self._condition:
                        self._phase = 'starting'
                    self.playback.play(track['suno_song_id'])
                    with self._condition:
                        self._phase = 'playing'
                        self._mascot = track['playing_mascot']
                    refill_requested = False
                    while not self._closed:
                        self.dialogue.advance()
                        self._poll_plan()
                        if self._interrupt and not self._stopping:
                            self._interrupt = False
                            self.playback.stop()
                        try:
                            terminal = self.playback.wait(timeout=.2)
                            break
                        except TimeoutError:
                            if len(self._queue) <= 1 and not refill_requested and not self._stopping:
                                self._submit_plan()
                                refill_requested = self._plan_future is not None
                    if self._closed:
                        break
                    if terminal['state'] == 'completed':
                        consecutive_failures = 0
                        with self._condition:
                            track = dict(self._current)
                            self._last = track
                        self._speak(track['outro'], 'outro', mascot=track['outro_mascot'], allow_stop=False)
                    elif terminal['state'] == 'failed':
                        raise PlaybackError(terminal.get('error') or 'The song could not play.')
                except PlaybackError as exc:
                    logging.getLogger(__name__).exception('Song playback failed; song=%s phase=%s.', track['suno_song_id'], self._phase)
                    consecutive_failures += 1
                    with self._condition:
                        self._error = str(exc)
                        self._failed_ids.append(track['suno_song_id'])
                        self._queue = [item for item in self._queue if item['suno_song_id'] != track['suno_song_id']]
                    if consecutive_failures >= 3:
                        raise ValueError('Three songs could not play. Programming stopped; check the connection and audio output.')
                    self._speak('This track could not play. We’ll try the next musical treasure.', 'playback_error')
                finally:
                    with self._condition:
                        self._current = None
                        self._revision += 1
        except Exception as exc:
            fatal = True
            logging.getLogger(__name__).exception('Programming stopped unexpectedly in phase %s.', self._phase)
            with self._condition:
                self._error = str(exc) if isinstance(exc, (ValueError, PlaybackError, CodexError)) else 'Programming could not continue. Please try again.'
        finally:
            if not self._closed:
                self._speak(self._signoff or 'Thanks for tuning in to Kern Radio. See you on the next adventure!',
                            'signing_off', mascot=self._signoff_mascot, allow_stop=False)
            with self._condition:
                self._running, self._stopping = False, False
                self._phase = 'failed' if fatal else 'idle'
                self._mascot = 'standard'
                self._current, self._queue = None, []
                self._condition.notify_all()

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        if self._thread:
            self._thread.join(timeout=2)
