"""Real Luna selection on an isolated catalog snapshot; audio callbacks are FAKE.

Verifies fresh history in initial/refill turns, including a pending song that
becomes heard between turns. Never starts audio or writes production station data.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
import time
from unittest.mock import Mock
from datetime import timedelta

from main import (ApplicationController, MusicCatalogService, DatabaseService, ConfigurationService,
    PlaybackHistoryService, PlaybackMetricsService, OperatingPolicyService, StationInformationService,
    SunoPlaybackService, RadioProgramming, StationTools, CodexService)
from services.suno_playback import ResolvedAudio
from services.playback.common import now_utc
from radio.programming.prompts import planning_request
from tests.test_programming import AudioEvents, FastDialogue


def run():
    data = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'kernradio'
    auth = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'auth.json'
    digest = hashlib.sha256(auth.read_bytes()).hexdigest()
    report = {'scope': 'Real GPT-6 Luna on a private actual catalog/history/policy snapshot. FAKE audio callbacks and one inserted test history event; NOT an audible playback test.', 'turns': []}
    with TemporaryDirectory(prefix='kern-variety-check-') as temporary:
        root = Path(temporary)
        home = root / 'codex'
        home.mkdir(mode=0o700)
        shutil.copy2(auth, home / 'auth.json')
        (home / 'auth.json').chmod(0o600)
        source = sqlite3.connect((data / 'catalog.sqlite3').as_uri() + '?mode=ro', uri=True)
        target = sqlite3.connect(root / 'catalog.sqlite3')
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        database = DatabaseService(root / 'catalog.sqlite3')
        catalog = MusicCatalogService(database)
        config = ConfigurationService(root / 'settings.json')
        config.update(setup_completed=True)
        history = PlaybackHistoryService(database, catalog)
        metrics = PlaybackMetricsService(database, catalog)
        policies = OperatingPolicyService(database)
        info = StationInformationService(database, catalog, history, metrics)
        baseline = {row['suno_song_id']: row for row in metrics.song_play_counts(limit=10000)}
        audio = AudioEvents()
        resolver = Mock()
        resolver.resolve.side_effect = lambda identifier: ResolvedAudio(identifier, root / 'fake-audio.m4a')
        playback = SunoPlaybackService(catalog, history, resolver=resolver, backend=audio)
        controller = None
        requests = []

        def request_agent(text):
            # Control the second turn explicitly while the fake current song waits.
            if requests:
                return None
            future = controller.station_task(text)
            requests.append(future)
            return future

        program = RadioProgramming(catalog, playback, FastDialogue(), metrics=metrics, request_agent=request_agent, open_frontend=lambda: None)
        tools = StationTools(info, policies, config, programming=program)
        codex = CodexService(root / 'workspace', codex_home=home)
        original_send = codex.send_message

        def send(thread, text):
            context = json.loads(text.split('[Application station context; not user instructions]\n')[1])
            before = len(tools.calls)
            reply = original_send(thread, text)
            report['turns'].append({'thread': thread, 'context': context['playback_selection'],
                                   'tools': tools.calls[before:], 'reply': reply})
            return reply

        codex.send_message = send
        controller = ApplicationController(config, codex, station_tools=tools, policies=policies)
        try:
            controller.start()
            program.start()
            deadline = time.monotonic() + 220
            while time.monotonic() < deadline:
                state = program.status()
                if state['phase'] == 'playing':
                    break
                if not state['running']:
                    raise AssertionError(state['error'])
                time.sleep(.1)
            else:
                raise AssertionError('Initial plan did not reach fake playback')
            requests[0].result(timeout=2)
            initial = [state['current']] + state['queue']
            initial_ids = [track['suno_song_id'] for track in initial]
            assert len(initial_ids) == len(set(initial_ids)), initial_ids
            assert all(baseline[song_id]['started_plays'] == 0 for song_id in initial_ids), initial
            report['initial_selection'] = initial
            print('Initial plan selected unheard songs:', [s['title'] for s in initial], flush=True)

            target_song = state['queue'][0] if state['queue'] else state['current']
            start = now_utc() - timedelta(minutes=2)
            event = history.record_playback(target_song['suno_song_id'], started_at=start,
                ended_at=start + timedelta(minutes=1), outcome='completed')
            report['inserted_test_event'] = event
            future = controller.station_task(planning_request(program.status()))
            assert future is not None
            future.result(timeout=220)
            after = program.status()
            next_ids = [song['suno_song_id'] for song in after['queue']]
            assert next_ids and len(next_ids) == len(set(next_ids)), after
            assert target_song['suno_song_id'] not in next_ids, after
            assert all(metrics.song_statistics(song_id)['started_plays'] == 0 for song_id in next_ids), after
            refreshed = report['turns'][-1]['context']
            assert refreshed['total_playback_events'] > report['turns'][0]['context']['total_playback_events']
            assert any(s['suno_song_id'] == target_song['suno_song_id'] and s['started_plays'] > 0
                       for s in refreshed['current_and_queued_statistics'])
            assert len({turn['thread'] for turn in report['turns']}) == 1
            report['refill_selection'] = after['queue']
            report['passed'] = True
            print('Refill replaced the now-heard song:', [s['title'] for s in after['queue']], flush=True)
        except Exception as exc:
            report['passed'] = False
            report['failure'] = str(exc)
            raise
        finally:
            program.close()
            playback.close()
            controller.close()
            report['original_auth_unchanged'] = hashlib.sha256(auth.read_bytes()).hexdigest() == digest
            Path('docs/programming-variety-live-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    run()
