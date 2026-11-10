"""Opt-in real Codex policy discovery evaluation in a private temporary database.

Uses actual supplied posts/Suno retrieval and recorded playback evidence.
All preferences in evaluation turns are test-only user inputs, not station defaults.
Original Codex auth is copied temporarily; no original auth/data files are written.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from radio.controller import ApplicationController
from radio.ingestion import MusicIngestion
from radio.station_tools import StationTools
from services.catalog import MusicCatalogService
from services.codex import CodexService
from services.configuration import ConfigurationService
from services.database import DatabaseService
from services.discord_text import DiscordTextParser
from services.playback import PlaybackHistoryService, PlaybackMetricsService
from services.policies import OperatingPolicyService
from services.station_information import StationInformationService
from services.suno import SunoMetadataService


def run():
    auth = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'auth.json'
    original_digest = hashlib.sha256(auth.read_bytes()).hexdigest()
    report = {'scope': 'Isolated evaluation; user preferences here are fixtures, not Kern station policy.', 'turns': []}
    with TemporaryDirectory(prefix='kern-policy-live-') as temporary:
        root = Path(temporary)
        home = root / 'codex'
        home.mkdir(mode=0o700)
        shutil.copy2(auth, home / 'auth.json')
        (home / 'auth.json').chmod(0o600)
        database = DatabaseService(root / 'catalog.sqlite3')
        catalog = MusicCatalogService(database)
        configuration = ConfigurationService(root / 'settings.json')
        configuration.update(setup_completed=True)
        ingestion = MusicIngestion(DiscordTextParser(), SunoMetadataService(), catalog)
        imported = ingestion.import_text(Path('tests/fixtures/crucible_posts.txt').read_text())
        report['ingestion'] = imported['counts']
        assert imported['counts']['added'] == 4, imported
        history = PlaybackHistoryService(database, catalog)
        for event in json.loads(Path('docs/suno-playback-live-result.json').read_text())['history']:
            history.record_playback(event['suno_song_id'], started_at=event['started_at'],
                ended_at=event['ended_at'], outcome=event['outcome'])
        metrics = PlaybackMetricsService(database, catalog)
        policies = OperatingPolicyService(database)
        information = StationInformationService(database, catalog, history, metrics)
        tools = StationTools(information, policies, configuration)
        service = CodexService(root / 'workspace', codex_home=home)
        controller = ApplicationController(configuration, service, station_tools=tools, policies=policies)
        controller.start()

        def turn(message):
            print('USER:', message, flush=True)
            before = len(tools.calls)
            controller.send(message)
            reply = controller._future.result(timeout=250)
            controller.status()
            calls = tools.calls[before:]
            print('AGENT:', reply, flush=True)
            print('TOOLS:', json.dumps(calls), flush=True)
            report['turns'].append({'user': message, 'assistant': reply, 'tools': calls})
            return reply, calls

        try:
            reply, calls = turn('Please inspect our actual music library and playback history, then help me figure out which normal station preferences are worth discussing. I have not chosen any preferences yet. Ask a focused question; do not propose policies yet.')
            report['initial_reads'] = {'catalog': any(c['tool'] in ('station_overview', 'station_catalog') for c in calls),
                                      'history': any(c['tool'] == 'station_playback' for c in calls)}
            report['no_early_proposals'] = policies.list_policies() == []
            turn('Can you tell me the exact combined duration of these four songs from stored data? Do not estimate or treat recorded airtime as a song-duration measurement.')
            assert policies.list_policies() == []
            turn('For this isolated evaluation only: I want ordinary programming to favor musical variety rather than stylistic consistency between tracks. Please save this as one proposed default for review. Do not approve it.')
            proposed = policies.list_policies()
            assert len(proposed) == 1, proposed
            policy = proposed[0]
            assert policies.active_policies() == []
            turn('For tonight only, I am considering an all-EDM evening. I have not decided. Do not save that as a default and do not approve anything. Briefly explain whether it changes the proposal.')
            assert len(policies.list_policies()) == 1 and not policies.active_policies()
            revision = policy['latest_revision']['revision']
            turn(f'Approve policy {policy["id"]} revision {revision}')
            assert len(policies.active_policies()) == 1
            controller.close()
            # Recreate application services and the Codex process against the same
            # database; conversation is deliberately fresh, policy state persists.
            policies = OperatingPolicyService(DatabaseService(database.path))
            tools = StationTools(information, policies, configuration)
            service = CodexService(root / 'workspace', codex_home=home)
            controller = ApplicationController(configuration, service, station_tools=tools, policies=policies)
            controller.start()
            turn('What default operating preferences have I already approved? Read the saved policies. Do not invent additional preferences or ask me to establish those same defaults again.')
            turn('Review the existing variety policy. Keep its exact instruction and do not create a second policy or change approval status. What does it currently mean?')
            assert len(policies.list_policies()) == 1
            turn('For this isolated evaluation, revise that same policy to say: For ordinary programming, favor musical variety while allowing stylistically consistent transitions. This replaces the earlier proposed wording, but do not activate the revision before my approval.')
            changed = policies.get_policy(policy['id'])
            assert len(policies.list_policies()) == 1
            assert changed['latest_revision']['revision'] == 2, changed
            assert changed['latest_revision']['approval_status'] == 'proposed'
            assert policies.active_policies()[0]['revision'] == 1
            turn(f'Approve policy {policy["id"]} revision 2')
            assert policies.active_policies()[0]['revision'] == 2
            turn(f'Disable policy {policy["id"]} revision 2')
            assert policies.active_policies() == []
            turn('Read the saved policy status now. Is the variety policy currently active? Do not change anything.')
            report['policies'] = policies.list_policies()
            report['active'] = policies.active_policies()
            report['station_overview'] = information.overview()
        finally:
            controller.close()
    report['original_auth_unchanged'] = hashlib.sha256(auth.read_bytes()).hexdigest() == original_digest
    assert report['original_auth_unchanged']
    Path('docs/policy-live-evaluation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print('Saved docs/policy-live-evaluation.json', flush=True)


if __name__ == '__main__':
    run()
