"""Play one explicitly supplied song. No queue or scheduler."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from services.catalog import MusicCatalogService
from services.database import DatabaseService
from services.playback import PlaybackHistoryService, PlaybackMetricsService
from . import PlaybackError, SunoPlaybackService


def main():
    parser = argparse.ArgumentParser(description='Play one Suno song with the Crucible Radio browser audio method. Ctrl+C stops playback.')
    parser.add_argument('song', help='Suno song URL or UUID')
    parser.add_argument('--database', type=Path, help='Persist history here; otherwise use an isolated temporary database')
    args = parser.parse_args()
    with TemporaryDirectory(prefix='kern-playback-check-') as temporary:
        database = DatabaseService(args.database or Path(temporary) / 'catalog.sqlite3')
        catalog = MusicCatalogService(database)
        history = PlaybackHistoryService(database, catalog)
        player = SunoPlaybackService(catalog, history)
        try:
            print('Preparing Suno audio…', flush=True)
            print(json.dumps(player.play(args.song)), flush=True)
            while True:
                try:
                    final = player.wait(timeout=30)
                    break
                except TimeoutError:
                    print(json.dumps(player.status()), flush=True)
            print(json.dumps(final), flush=True)
            if final['suno_song_id']:
                print(json.dumps(PlaybackMetricsService(database, catalog).song_statistics(final['suno_song_id'])), flush=True)
            return 1 if final['state'] == 'failed' else 0
        except KeyboardInterrupt:
            print(json.dumps(player.stop()), flush=True)
            return 0
        except PlaybackError as exc:
            print(f'Playback failed: {exc}', flush=True)
            return 1
        finally:
            player.close()


if __name__ == '__main__':
    raise SystemExit(main())
