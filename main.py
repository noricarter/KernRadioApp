"""Launch the local application with: uv run main.py"""
import os
import signal
import logging
import webbrowser
from pathlib import Path

from radio.controller import ApplicationController
from radio.web import create_app
from radio.ingestion import MusicIngestion
from services.database import DatabaseError, DatabaseService
from services.catalog import MusicCatalogService
from services.discord_text import DiscordTextParser
from services.suno import SunoMetadataService
from services.codex import CodexService
from services.configuration import ConfigurationError, ConfigurationService
from services.web import FlaskWebService
from services.playback import PlaybackHistoryService, PlaybackMetricsService
from services.station_information import StationInformationService
from services.policies import OperatingPolicyService
from radio.station_tools import StationTools
from radio.management import StationManagement
from services.suno_playback import SunoPlaybackService
from services.artwork import ArtworkService
from radio.artwork import CatalogArtwork
from services.dialogue import DialogueService
from services.suno_playback import BrowserAudioBackend
from radio.programming import RadioProgramming
from radio.diagnostics import start_diagnostics


def main():
    def stop_requested(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_requested)
    if os.environ.get('XDG_CONFIG_HOME'):
        config_root = Path(os.environ['XDG_CONFIG_HOME'])
    elif os.name == 'nt':
        config_root = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
    else:
        config_root = Path.home() / '.config'
    data = config_root / 'kernradio'
    controller = None
    web = None
    playback = None
    programming = None
    diagnostics = None
    try:
        diagnostics = start_diagnostics(data / 'logs' / 'errors.log')
        configuration = ConfigurationService(data / 'settings.json')
        database = DatabaseService(data / 'catalog.sqlite3')
        catalog = MusicCatalogService(database)
        history = PlaybackHistoryService(database, catalog)
        metrics = PlaybackMetricsService(database, catalog)
        policies = OperatingPolicyService(database)
        information = StationInformationService(database, catalog, history, metrics)
        audio_backend = BrowserAudioBackend()
        playback = SunoPlaybackService(catalog, history, backend=audio_backend)
        programming = RadioProgramming(catalog, playback, DialogueService(), metrics=metrics,
            request_agent=lambda text: controller.station_task(text), open_frontend=audio_backend.open_display)
        station_tools = StationTools(information, policies, configuration, programming=programming)
        controller = ApplicationController(configuration, CodexService(data / 'workspace'),
            station_tools=station_tools, policies=policies)
        metadata = SunoMetadataService()
        artwork = ArtworkService(data / 'artwork')
        ingestion = MusicIngestion(DiscordTextParser(), metadata, catalog,
            artwork=CatalogArtwork(catalog, metadata, artwork))
        management = StationManagement(configuration, catalog, information, policies, playback=playback, artwork=artwork, programming=programming)
        web = FlaskWebService(create_app(controller, ingestion, management))
        controller.start()
        web.start()
        audio_backend.set_display_url(web.url + '/listen/')
        print(f'Kern Radio is ready. Open {web.url} in your browser.', flush=True)
        if os.environ.get('KERNRADIO_OPEN_BROWSER') == '1':
            webbrowser.open(web.url)
        print('Press Ctrl+C to stop.', flush=True)
        web.wait()
    except KeyboardInterrupt:
        print('\nStopping Community Radio.')
    except (ConfigurationError, DatabaseError, OSError) as exc:
        logging.getLogger(__name__).exception('Application startup or storage failed.')
        print(f'Could not start Community Radio: {exc}')
    except Exception:
        logging.getLogger(__name__).exception('Application stopped unexpectedly.')
        print(f'Kern Radio stopped unexpectedly. Error details are saved in {data / "logs" / "errors.log"}.')
    finally:
        if programming:
            programming.close()
        if web:
            web.stop()
        if controller:
            controller.close()
        if playback:
            playback.close()
        if diagnostics:
            diagnostics()


if __name__ == '__main__':
    main()
