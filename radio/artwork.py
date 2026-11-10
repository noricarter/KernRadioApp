"""Application composition for caching artwork without replacing edited metadata."""
from services.artwork import ArtworkError
from services.catalog import RecordNotFound
from services.suno import SunoMetadataError


class CatalogArtwork:
    def __init__(self, catalog, metadata, artwork):
        self.catalog, self.metadata, self.artwork = catalog, metadata, artwork

    def cache_song(self, song_id):
        song = self.catalog.get_song(song_id)
        if song is None:
            raise RecordNotFound('Song does not exist.')
        if self.artwork.path(song['artwork_file']):
            return song
        if not song['artwork_url']:
            return song
        filename = self.artwork.fetch(song['artwork_url'])
        return self.catalog.update_song_metadata(song_id, artwork_file=filename)

    def backfill(self, *, progress=None):
        """Only target catalog songs missing a local original. No metadata rewrites."""
        result = {'saved': 0, 'existing': 0, 'unavailable': 0, 'failed': []}
        offset = 0
        while songs := self.catalog.list_songs(limit=100, offset=offset):
            for song in songs:
                song_id = song['suno_song_id']
                try:
                    if self.artwork.path(song['artwork_file']):
                        result['existing'] += 1
                        continue
                    if not song['artwork_url']:
                        metadata = self.metadata.fetch_song(song_id)
                        if metadata['suno_song_id'] != song_id:
                            raise SunoMetadataError('Artwork metadata did not match the catalog song.')
                        if not metadata.get('artwork_url'):
                            result['unavailable'] += 1
                            continue
                        self.catalog.update_song_metadata(song_id, artwork_url=metadata['artwork_url'])
                    self.cache_song(song_id)
                    result['saved'] += 1
                except (SunoMetadataError, ArtworkError) as exc:
                    result['failed'].append({'suno_song_id': song_id, 'error': str(exc)})
                finally:
                    if progress:
                        progress(song_id, result)
            offset += len(songs)
        return result
