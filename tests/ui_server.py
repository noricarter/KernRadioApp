"""Isolated actual Flask server for browser verification; never production data."""
import argparse
import os
from pathlib import Path
import shutil
import signal

from radio.controller import ApplicationController
from radio.ingestion import MusicIngestion
from radio.management import StationManagement
from radio.station_tools import StationTools
from radio.web import create_app
from services.catalog import MusicCatalogService
from services.codex import CodexService
from services.configuration import ConfigurationService
from services.database import DatabaseService
from services.discord_text import DiscordTextParser
from services.playback import PlaybackHistoryService, PlaybackMetricsService
from services.policies import OperatingPolicyService
from services.station_information import StationInformationService
from services.suno import SunoMetadataService
from services.web import FlaskWebService
from services.artwork import ArtworkService
from radio.artwork import CatalogArtwork


class DisconnectedCodex:
    connected = True
    def start(self): pass
    def close(self): pass
    def account(self): return {'authenticated': False}
    def login_status(self): return {'pending': False}
    def begin_login(self): return {'auth_url': 'https://auth.openai.com/'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    args.data.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = ConfigurationService(args.data / 'settings.json')
    database = DatabaseService(args.data / 'catalog.sqlite3')
    catalog = MusicCatalogService(database)
    history = PlaybackHistoryService(database, catalog)
    information = StationInformationService(database, catalog, history, PlaybackMetricsService(database, catalog))
    policies = OperatingPolicyService(database)
    auth_copy = None
    if args.live:
        home = args.data / 'codex'
        home.mkdir(mode=0o700, exist_ok=True)
        auth = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'auth.json'
        auth_copy = home / 'auth.json'
        shutil.copy2(auth, auth_copy)
        auth_copy.chmod(0o600)
        codex = CodexService(args.data / 'workspace', codex_home=home)
    else:
        codex = DisconnectedCodex()
    controller = ApplicationController(config, codex,
        station_tools=StationTools(information, policies, config), policies=policies)
    metadata = SunoMetadataService()
    artwork = ArtworkService(args.data / 'artwork')
    ingestion = MusicIngestion(DiscordTextParser(), metadata, catalog, artwork=CatalogArtwork(catalog, metadata, artwork))
    management = StationManagement(config, catalog, information, policies, artwork=artwork)
    web = FlaskWebService(create_app(controller, ingestion, management), port=0)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        controller.start()
        web.start()
        print(web.url, flush=True)
        web.wait()
    except KeyboardInterrupt:
        pass
    finally:
        web.stop()
        controller.close()
        if auth_copy:
            auth_copy.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
