"""Real Suno decoding in isolated MUTED Chrome, with injected connection failures."""
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import time

from flask import jsonify
from radio.diagnostics import start_diagnostics
from radio.web import create_app
from services.catalog import MusicCatalogService
from services.database import DatabaseService
from services.playback import PlaybackHistoryService, PlaybackMetricsService
from services.suno_playback import BrowserAudioBackend, SunoPlaybackService
from services.web import FlaskWebService


def run():
    song = '0746f5e7-4d09-4d6b-9129-733c341f23eb'
    with TemporaryDirectory(prefix='kern-audio-recovery-') as temporary:
        root = Path(temporary)
        close_logs = start_diagnostics(root / 'logs' / 'errors.log')
        wrapper = root / 'chrome'
        wrapper.write_text('#!/bin/sh\nexec /usr/bin/google-chrome --headless=new --disable-gpu --mute-audio --remote-debugging-port=0 "$@"\n')
        wrapper.chmod(0o700)
        database = DatabaseService(root / 'catalog.sqlite3')
        catalog = MusicCatalogService(database)
        history = PlaybackHistoryService(database, catalog)
        metrics = PlaybackMetricsService(database, catalog)
        backend = BrowserAudioBackend(executable=str(wrapper))
        player = SunoPlaybackService(catalog, history, backend=backend)
        app = create_app(None)

        @app.get('/test/audio')
        def state():
            return jsonify(status=player.status(), history=history.list_events(), statistics=metrics.song_statistics(song))

        @app.post('/test/replay')
        def replay():
            return jsonify(player.play(song))

        web = FlaskWebService(app, port=0)
        try:
            web.start()
            backend.set_display_url(web.url + '/listen/')
            player.play(song)
            port_file = Path(backend._profile.name) / 'DevToolsActivePort'
            deadline = time.monotonic() + 10
            while not port_file.exists() and time.monotonic() < deadline:
                time.sleep(.1)
            debug_port = port_file.read_text().splitlines()[0]
            subprocess.run(['node', 'tests/audio_recovery_browser_check.mjs', debug_port, web.url], check=True, timeout=120)
            report = json.loads(Path('docs/audio-recovery-live-result.json').read_text())
            log = (root / 'logs' / 'errors.log').read_text()
            assert 'Audio control interruption' in log
            assert 'stopped reporting for 15 seconds' in log
            assert 'Audio playback failed' in log
            report['saved_error_log'] = log
            report['scope'] = 'Real Suno lookup/decryption and HTML Audio in isolated muted headless Chrome. No human audibility claim; no production station/auth writes. EOF check seeks near the end rather than playing the full song.'
            report['passed'] = True
            Path('docs/audio-recovery-live-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            print('PASS: real Suno playback recovery, reload, EOF retry, contact timeout, history and saved logs.', flush=True)
        finally:
            player.close()
            web.stop()
            close_logs()


if __name__ == '__main__':
    run()
