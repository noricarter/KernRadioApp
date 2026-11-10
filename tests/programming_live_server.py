"""Isolated real Flask/Codex/audio station for interactive programming verification.

Copies catalog, approved policies, config, artwork, and authentication privately.
Start/Stop are exercised through the normal UI. Audio can actually be audible.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
from tempfile import TemporaryDirectory

from main import (ApplicationController, MusicCatalogService, DatabaseService, ConfigurationService,
    PlaybackHistoryService, PlaybackMetricsService, OperatingPolicyService, StationInformationService,
    BrowserAudioBackend, SunoPlaybackService, RadioProgramming, DialogueService, StationTools,
    StationManagement, FlaskWebService, create_app, CodexService, ArtworkService)


def run():
    data = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'kernradio'
    auth = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'auth.json'
    digest = hashlib.sha256(auth.read_bytes()).hexdigest()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    with TemporaryDirectory(prefix='kern-programming-live-') as temporary:
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
        shutil.copy2(data / 'settings.json', root / 'settings.json')
        shutil.copytree(data / 'artwork', root / 'artwork')
        database = DatabaseService(root / 'catalog.sqlite3')
        catalog = MusicCatalogService(database)
        config = ConfigurationService(root / 'settings.json')
        history = PlaybackHistoryService(database, catalog)
        previous_event_ids = {event['id'] for event in history.list_events(limit=10000)}
        metrics = PlaybackMetricsService(database, catalog)
        policies = OperatingPolicyService(database)
        information = StationInformationService(database, catalog, history, metrics)
        audio = BrowserAudioBackend()
        playback = SunoPlaybackService(catalog, history, backend=audio)
        controller = None
        programming = RadioProgramming(catalog, playback, DialogueService(),
            metrics=metrics, request_agent=lambda text: controller.station_task(text), open_frontend=audio.open_display)
        tools = StationTools(information, policies, config, programming=programming)
        codex = CodexService(root / 'workspace', codex_home=home)
        thread_models = []
        original_request = codex._transport.request
        def request(method, *args, **kwargs):
            result = original_request(method, *args, **kwargs)
            if method == 'thread/start':
                thread_models.append(result.get('model'))
            return result
        codex._transport.request = request
        controller = ApplicationController(config, codex, station_tools=tools, policies=policies)
        manager = StationManagement(config, catalog, information, policies, playback=playback,
            artwork=ArtworkService(root / 'artwork'), programming=programming)
        web = FlaskWebService(create_app(controller, management=manager), port=0)
        try:
            controller.start()
            web.start()
            audio.set_display_url(web.url + '/listen/')
            print('ISOLATED_DATA=' + str(root), flush=True)
            print(web.url, flush=True)
            web.wait()
        except KeyboardInterrupt:
            pass
        finally:
            report = {'scope': 'Isolated real catalog/config/auth copy; real Luna tools, Suno audio and frontend.',
                      'thread_models': thread_models, 'tool_calls': tools.calls,
                      'programming': programming.status(), 'messages': controller.status()['messages'],
                      'history': [event for event in history.list_events(limit=10000) if event['id'] not in previous_event_ids]}
            programming.close()
            playback.close()
            web.stop()
            controller.close()
            report['original_auth_unchanged'] = hashlib.sha256(auth.read_bytes()).hexdigest() == digest
            Path('docs/programming-live-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            print('Saved docs/programming-live-result.json', flush=True)


if __name__ == '__main__':
    run()
