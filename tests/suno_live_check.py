"""Opt-in live HTTP -> parser -> Suno -> SQLite verification, not a unit test.

Run: uv run python -m tests.suno_live_check /path/to/pasted-posts.txt
The supplied file is the sole source of song targets. Only missing song IDs
are fetched. All database writes use a deleted-afterward temporary directory.
"""
import argparse
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.request import Request, urlopen

from radio.ingestion import MusicIngestion
from radio.web import create_app
from services.catalog import MusicCatalogService
from services.database import DatabaseService
from services.discord_text import DiscordTextParser
from services.suno import SunoMetadataService
from services.web import FlaskWebService


class _CountedMetadata(SunoMetadataService):
    def __init__(self):
        super().__init__()
        self.requests = 0
        self.operations = []

    def fetch_song(self, value):
        self.requests += 1
        return super().fetch_song(value)

    def _request(self, url, *, method='GET'):
        self.operations.append({'method': method, 'url': url})
        return super()._request(url, method=method)


def run(text_path):
    text = text_path.read_text(encoding='utf-8')
    parsed = DiscordTextParser().parse(text)
    with TemporaryDirectory(prefix='kern-ingestion-live-') as temporary:
        path = Path(temporary) / 'catalog.sqlite3'
        catalog = MusicCatalogService(DatabaseService(path))
        metadata = _CountedMetadata()
        ingestion = MusicIngestion(DiscordTextParser(), metadata, catalog)
        web = FlaskWebService(create_app(SimpleNamespace(), ingestion), port=0)
        web.start()
        try:
            with urlopen(web.url) as response:
                token = re.search('name="app-token" content="([^"]+)"', response.read().decode())[1]

            def import_batch():
                request = Request(web.url + '/api/import', data=json.dumps({'text': text}).encode(),
                    headers={'Content-Type': 'application/json', 'X-App-Token': token})
                with urlopen(request, timeout=300) as response:
                    return json.load(response)

            first = import_batch()
            requests_after_first = metadata.requests
            operations_after_first = len(metadata.operations)
            assert first['counts']['added'] > 0, first
            second = import_batch()
            assert second['counts']['added'] == 0, second
            # Failed songs may intentionally be retried, so only assert zero extra
            # retrievals when every parsed song succeeded on the first pass.
            if first['counts']['failed'] == 0:
                assert metadata.requests == requests_after_first
                assert len(metadata.operations) == operations_after_first
            restored = MusicCatalogService(DatabaseService(path))
            songs = restored.list_songs()
            assert len(songs) == first['counts']['added']
            report = {
                'source_file': str(text_path),
                'extracted': [dict(suno_song_id=entry.suno_song_id,
                    original_suno_url=entry.original_suno_url, discord_handle=entry.discord_handle,
                    submitted_title=entry.submitted_title, genre=entry.genre,
                    description=entry.description, content_warning=entry.content_warning,
                    preferred_artist_name=entry.preferred_artist_name,
                    discord_timestamp=entry.discord_timestamp, posted_at=entry.posted_at)
                    for entry in parsed.submissions],
                'first_import': first, 'second_import': second,
                'live_metadata_lookups_after_first_import': requests_after_first,
                'live_metadata_lookups_total': metadata.requests,
                'network_operations_after_first_import': operations_after_first,
                'network_operations_total': len(metadata.operations),
                'network_operations': metadata.operations,
                'stored_songs': [dict(id=song['id'], suno_song_id=song['suno_song_id'],
                    original_suno_url=song['original_suno_url'], title=song['title'],
                    style=song['style'], suno_username=song['suno_username'], artist_id=song['artist_id'],
                    lyrics_characters=len(song['lyrics']) if song['lyrics'] is not None else None)
                    for song in songs],
                'stored_submissions': restored.list_submissions(),
                'stored_artists': restored.list_artists(),
                'stored_batches': restored.list_batches(),
                'temporary_database_removed_after_check': True,
            }
            print(json.dumps(report, ensure_ascii=False, indent=2))
        finally:
            web.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('text_path', type=Path)
    run(parser.parse_args().text_path)
