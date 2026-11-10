"""Opt-in actual desktop audio check; never runs in unittest discovery.

Run: uv run python -m tests.suno_playback_live_check SUNO_LINK
Plays five seconds, stops, plays the whole song to its real ended event,
then verifies an actual owned-browser failure. All history is temporary.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import time

from services.catalog import MusicCatalogService
from services.database import DatabaseService
from services.playback import PlaybackHistoryService, PlaybackMetricsService
from services.suno_playback import SunoPlaybackService


def run(song):
    with TemporaryDirectory(prefix='kern-live-audio-') as temporary:
        database = DatabaseService(Path(temporary) / 'catalog.sqlite3')
        catalog = MusicCatalogService(database)
        history = PlaybackHistoryService(database, catalog)
        with SunoPlaybackService(catalog, history) as player:
            print('Starting actual audio for interruption check…', flush=True)
            started = player.play(song)
            time.sleep(5)
            audible = player.status()
            assert audible['position_seconds'] > 1, audible
            outputs = None
            if shutil.which('pactl'):
                inputs = json.loads(subprocess.check_output(['pactl', '--format=json', 'list', 'sink-inputs']))
                outputs = [{'binary': entry['properties'].get('application.process.binary'),
                    'corked': entry['corked'], 'mute': entry['mute']} for entry in inputs
                    if 'chrom' in entry.get('properties', {}).get('application.process.binary', '').lower()]
                assert any(not entry['corked'] and not entry['mute'] for entry in outputs), outputs
            stopped = player.stop()
            assert stopped['state'] == 'stopped'
            assert history.get_event(stopped['event_id'])['outcome'] == 'interrupted'
            print('Stop and real output checks passed. Playing the whole song to natural completion…', flush=True)
            player.play(started['suno_song_id'])
            deadline = time.monotonic() + 900
            while True:
                try:
                    completed = player.wait(timeout=30)
                    break
                except TimeoutError:
                    print(json.dumps(player.status()), flush=True)
                    if time.monotonic() > deadline:
                        raise RuntimeError('The browser did not report natural completion within 15 minutes.')
            assert completed['state'] == 'completed', completed
            assert history.get_event(completed['event_id'])['outcome'] == 'completed'
            player.play(started['suno_song_id'])
            player._backend._process.terminate()  # Only this test's dedicated Chrome.
            failed = player.wait(timeout=15)
            assert failed['state'] == 'failed', failed
            assert history.get_event(failed['event_id'])['outcome'] == 'interrupted'
            statistics = PlaybackMetricsService(database, catalog).song_statistics(started['suno_song_id'])
            assert statistics['completed_plays'] == 1 and statistics['interrupted_plays'] == 2
            assert statistics['unfinished_plays'] == 0 and statistics['airtime_seconds'] > 5
            print(json.dumps({'source': song, 'playing_status': audible, 'desktop_audio_outputs': outputs,
                'stop_status': stopped, 'completion_status': completed, 'failure_status': failed,
                'history': history.list_events(), 'statistics': statistics,
                'database_is_temporary': True}, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('song')
    run(parser.parse_args().song)
