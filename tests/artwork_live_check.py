"""Real Suno -> ingestion -> local originals -> HTTP check in isolated storage."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
from urllib.request import urlopen

from radio.artwork import CatalogArtwork
from radio.ingestion import MusicIngestion
from radio.management import StationManagement
from radio.web import create_app
from services.artwork import ArtworkService
from services.catalog import MusicCatalogService
from services.database import DatabaseService
from services.discord_text import DiscordTextParser
from services.playback import PlaybackHistoryService, PlaybackMetricsService
from services.station_information import StationInformationService
from services.suno import SunoMetadataService
from services.web import FlaskWebService


def run():
    report = {'scope': 'Real public Suno requests and local HTTP; isolated catalog/cache, no Codex.'}
    with TemporaryDirectory(prefix='kern-artwork-live-') as temporary:
        root = Path(temporary)
        database = DatabaseService(root / 'catalog.sqlite3')
        catalog = MusicCatalogService(database)
        metadata = SunoMetadataService()
        artwork = ArtworkService(root / 'artwork')
        composition = CatalogArtwork(catalog, metadata, artwork)
        ingestion = MusicIngestion(DiscordTextParser(), metadata, catalog, artwork=composition)
        text = Path('tests/fixtures/crucible_posts.txt').read_text()
        first = ingestion.import_text(text)
        assert first['counts'] == {'added': 4, 'existing': 0, 'failed': 0}, first
        report['first_import'] = first['counts']
        history = PlaybackHistoryService(database, catalog)
        information = StationInformationService(database, catalog, history, PlaybackMetricsService(database, catalog))
        management = StationManagement(None, catalog, information, None, artwork=artwork)
        # This check serves only catalog/artwork routes. No mocked network data
        # or agent is used; no controller operations are invoked.
        web = FlaskWebService(create_app(None, ingestion, management), port=0)
        web.start()
        try:
            report['songs'] = []
            for song in catalog.list_songs():
                path = artwork.path(song['artwork_file'])
                assert path is not None, song
                detail = management.song_detail(song['suno_song_id'])
                with urlopen(web.url + detail['artwork_url']) as response:
                    data, content_type = response.read(), response.headers['Content-Type']
                assert data == path.read_bytes()
                report['songs'].append({'suno_song_id': song['suno_song_id'], 'title': song['title'],
                    'source_url': song['artwork_url'], 'file': song['artwork_file'], 'bytes': len(data),
                    'sha256': hashlib.sha256(data).hexdigest(), 'http_content_type': content_type})
            # Failing network sentinels demonstrate duplicate imports/backfill
            # make zero additional requests; all earlier retrievals were live.
            metadata._opener = Mock()
            metadata._opener.open.side_effect = AssertionError('Unexpected repeat metadata request')
            artwork._opener = Mock()
            artwork._opener.open.side_effect = AssertionError('Unexpected repeat artwork request')
            report['repeat_import'] = ingestion.import_text(text)['counts']
            report['repeat_backfill'] = composition.backfill()
            assert report['repeat_backfill']['existing'] == 4
            restored = MusicCatalogService(DatabaseService(database.path))
            assert all(artwork.path(song['artwork_file']) for song in restored.list_songs())
            report['artwork_survived_catalog_restart'] = True
        finally:
            web.stop()
    Path('docs/artwork-live-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    run()
