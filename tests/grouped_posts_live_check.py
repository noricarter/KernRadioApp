"""Read original failed groups; import one intact group with live Suno in isolation."""
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from radio.artwork import CatalogArtwork
from radio.catalog_review import pending_import_review
from radio.ingestion import MusicIngestion
from services.artwork import ArtworkService
from services.catalog import MusicCatalogService
from services.database import DatabaseService
from services.discord_text import DiscordTextParser
from services.suno import SunoMetadataService


def run():
    path = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'kernradio/catalog.sqlite3'
    source = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    try:
        original = [dict(row) for row in source.execute("SELECT * FROM ingestion_batch_items WHERE batch_id=1 AND outcome='failed' ORDER BY id")]
    finally:
        source.close()
    parser = DiscordTextParser()
    report = {'scope': 'Original station DB read-only; real Suno metadata/artwork, isolated writes.', 'groups': []}
    for entry in original:
        parsed = parser.parse(entry['source_text'])
        report['groups'].append({'original_item_id': entry['id'], 'songs': sum(post.source == 'suno' for post in parsed.submissions),
            'youtube': sum(post.source == 'youtube' for post in parsed.submissions),
            'authors': [post.discord_handle for post in parsed.submissions],
            'remaining': [post.reason for post in parsed.unresolved]})
    assert len(original) == 19
    assert sum(group['songs'] for group in report['groups']) == 38
    assert sum(group['youtube'] for group in report['groups']) == 2
    assert sum(len(group['remaining']) for group in report['groups']) == 0
    with TemporaryDirectory(prefix='kern-grouped-live-') as temporary:
        root = Path(temporary)
        catalog = MusicCatalogService(DatabaseService(root / 'catalog.sqlite3'))
        old = catalog.create_batch(source_text=original[0]['source_text'])
        failed = catalog.record_batch_item(old['id'], outcome='failed', source_text=original[0]['source_text'], error=original[0]['error'])
        catalog.finish_batch(old['id'])
        assert pending_import_review(catalog)['total'] == 1
        metadata, artwork = SunoMetadataService(), ArtworkService(root / 'artwork')
        result = MusicIngestion(parser, metadata, catalog, artwork=CatalogArtwork(catalog, metadata, artwork)).import_text(original[0]['source_text'])
        assert result['counts'] == {'added': 2, 'existing': 0, 'failed': 0}, result
        report['live_import'] = result['counts']
        report['stored'] = []
        for song in catalog.list_songs():
            artist = catalog.get_artist(song['artist_id'])
            assert artwork.path(song['artwork_file'])
            report['stored'].append({'suno_song_id': song['suno_song_id'], 'title': song['title'],
                'preferred_artist': artist['preferred_display_alias'], 'artwork_saved': True})
        assert [song['preferred_artist'] for song in report['stored']] == ['Seraphym', 'norioku2020']
        report['current_unresolved'] = pending_import_review(catalog)['total']
        assert report['current_unresolved'] == 0
        assert catalog.get_batch(old['id'])['items'] == [failed]
        report['historical_failed_count'] = catalog.get_batch(old['id'])['counts']['failed']
        restarted = MusicCatalogService(DatabaseService(root / 'catalog.sqlite3'))
        assert pending_import_review(restarted)['total'] == 0
        report['resolution_survives_restart'] = True
    Path('docs/grouped-posts-live-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    run()
