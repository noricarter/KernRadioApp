"""Opt-in real Codex check against a read-only snapshot of a local import batch.

Run: uv run python -m tests.ingestion_context_live_check
Original station data/authentication are never written. No Suno requests/reimports.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory

from radio.controller import ApplicationController
from radio.station_tools import StationTools
from services.catalog import MusicCatalogService
from services.codex import CodexService
from services.configuration import ConfigurationService
from services.database import DatabaseService
from services.playback import PlaybackHistoryService, PlaybackMetricsService
from services.policies import OperatingPolicyService
from services.station_information import StationInformationService


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    default = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'kernradio/catalog.sqlite3'
    parser.add_argument('--catalog', type=Path, default=default)
    args = parser.parse_args()
    auth = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'auth.json'
    digest = hashlib.sha256(auth.read_bytes()).hexdigest()
    report = {'scope': 'Real Codex; isolated SQLite snapshot. No import repair or production writes.', 'turns': []}
    with TemporaryDirectory(prefix='kern-import-context-') as temporary:
        root = Path(temporary)
        home = root / 'codex'
        home.mkdir(mode=0o700)
        shutil.copy2(auth, home / 'auth.json')
        (home / 'auth.json').chmod(0o600)
        database = DatabaseService(root / 'catalog.sqlite3')
        catalog = MusicCatalogService(database)
        history = PlaybackHistoryService(database, catalog)
        policies = OperatingPolicyService(database)
        information = StationInformationService(database, catalog, history, PlaybackMetricsService(database, catalog))
        configuration = ConfigurationService(root / 'settings.json')
        configuration.update(setup_completed=True)

        def application():
            tools = StationTools(information, policies, configuration)
            controller = ApplicationController(configuration, CodexService(root / 'workspace', codex_home=home),
                station_tools=tools, policies=policies)
            controller.start()
            assert controller.status()['authenticated'], 'Existing ChatGPT authentication is required.'
            return controller, tools

        def turn(message):
            before = len(tools.calls)
            controller.send(message)
            reply = controller._future.result(timeout=200)
            controller.status()
            result = {'user': message, 'assistant': reply, 'tools': tools.calls[before:]}
            report['turns'].append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return result

        controller, tools = application()
        try:
            turn('Hello. I will import some community music next. Just acknowledge; do not propose any policies.')
            thread = controller._thread_id
            # Simulate data arriving after a conversation started. The source is
            # explicitly opened read-only; only this temporary DB is changed.
            source = sqlite3.connect(args.catalog.resolve().as_uri() + '?mode=ro', uri=True)
            target = sqlite3.connect(database.path)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
            summary = information.latest_import()
            assert summary and summary['counts']['failed'] > 0, 'A saved unresolved batch is required.'
            report['batch'] = summary
            failures = summary['counts']['failed']
            result = turn(f'{failures} unresolved results can we walk through them and fix them? '
                'Read the first two original unresolved entries and explain what needs attention. '
                'Do not change or reimport anything yet.')
            calls = result['tools']
            assert controller._thread_id == thread, 'Existing conversation should remain open.'
            assert any(call['tool'] == 'station_ingestion' and call['arguments']['action'] == 'items' for call in calls), calls
            assert any(call['tool'] == 'station_ingestion' and call['arguments']['action'] == 'item' for call in calls), calls
            report['existing_thread_retrieved_originals'] = True
            controller.close()
            controller, tools = application()
            result = turn(f'Which saved import batch has {failures} unresolved entries? Read its saved failures '
                'and the first original entry. Do not ask me to paste them and do not change anything.')
            assert any(call['tool'] == 'station_ingestion' and call['arguments']['action'] == 'item' for call in result['tools'])
            report['restart_retrieved_originals'] = True
            assert information.latest_import() == summary
        finally:
            controller.close()
    report['original_auth_unchanged'] = hashlib.sha256(auth.read_bytes()).hexdigest() == digest
    assert report['original_auth_unchanged']
    Path('docs/ingestion-context-live-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print('Saved docs/ingestion-context-live-result.json', flush=True)


if __name__ == '__main__':
    run()
