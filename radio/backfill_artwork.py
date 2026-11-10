"""Fetch missing catalog artwork: uv run python -m radio.backfill_artwork"""
import json
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

from radio.artwork import CatalogArtwork
from services.artwork import ArtworkService
from services.catalog import MusicCatalogService
from services.database import DatabaseService
from services.suno import SunoMetadataService


def main():
    data = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'kernradio'
    path = data / 'catalog.sqlite3'
    if not path.is_file():
        raise SystemExit('There is no existing catalog to fetch artwork for. Import music first.')
    backup = data / ('catalog-before-artwork-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite3')
    source = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    target = sqlite3.connect(backup)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    catalog = MusicCatalogService(DatabaseService(path))
    service = CatalogArtwork(catalog, SunoMetadataService(), ArtworkService(data / 'artwork'))
    result = service.backfill(progress=lambda song, counts: print(
        f"Checked {song}: {counts['saved']} saved, {counts['existing']} already available, "
        f"{counts['unavailable']} unavailable, {len(counts['failed'])} failed", flush=True))
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    print(f'Catalog backup: {backup}', flush=True)


if __name__ == '__main__':
    main()
