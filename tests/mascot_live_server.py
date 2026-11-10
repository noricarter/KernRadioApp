"""Isolated Flask/Luna mascot check. No station data writes or audio playback."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
from tempfile import TemporaryDirectory

from flask import request, jsonify
from main import (ApplicationController, MusicCatalogService, DatabaseService, ConfigurationService,
    PlaybackHistoryService, PlaybackMetricsService, OperatingPolicyService, StationInformationService,
    SunoPlaybackService, RadioProgramming, DialogueService, StationTools, StationManagement,
    FlaskWebService, create_app, CodexService)


def run():
    auth = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'auth.json'
    digest = hashlib.sha256(auth.read_bytes()).hexdigest()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    with TemporaryDirectory(prefix='kern-mascot-check-') as temporary:
        root = Path(temporary)
        home = root / 'codex'
        home.mkdir(mode=0o700)
        shutil.copy2(auth, home / 'auth.json')
        (home / 'auth.json').chmod(0o600)
        database = DatabaseService(root / 'catalog.sqlite3')
        catalog = MusicCatalogService(database)
        config = ConfigurationService(root / 'settings.json')
        config.update(setup_completed=True)
        history = PlaybackHistoryService(database, catalog)
        policies = OperatingPolicyService(database)
        information = StationInformationService(database, catalog, history, PlaybackMetricsService(database, catalog))
        playback = SunoPlaybackService(catalog, history)
        controller = None
        programming = RadioProgramming(catalog, playback, DialogueService(),
            metrics=information.metrics, request_agent=lambda text: controller.station_task(text), open_frontend=lambda: None)
        tools = StationTools(information, policies, config, programming=programming)
        controller = ApplicationController(config, CodexService(root / 'workspace', codex_home=home),
            station_tools=tools, policies=policies)
        manager = StationManagement(config, catalog, information, policies, playback=playback, programming=programming)
        app = create_app(controller, management=manager)

        @app.post('/test/mascot')
        def fixture_switch():
            return jsonify(tools.dispatch('set_host_mascot', request.get_json()))

        web = FlaskWebService(app, port=0)
        try:
            controller.start()
            web.start()
            print(web.url, flush=True)
            web.wait()
        except KeyboardInterrupt:
            pass
        finally:
            report = {'scope': 'Isolated empty station, real Luna agent and Chrome; no audio playback or production catalog writes.',
                'messages': controller.status()['messages'], 'tool_calls': tools.calls,
                'final_state': programming.public_state()}
            programming.close()
            playback.close()
            web.stop()
            controller.close()
            report['original_auth_unchanged'] = hashlib.sha256(auth.read_bytes()).hexdigest() == digest
            Path('docs/mascot-agent-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    run()
