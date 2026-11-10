"""Management/onboarding orchestration. Presentation adapters contain no SQL."""
from services.catalog import AliasConflict, RecordNotFound
from urllib.parse import quote
from .catalog_review import import_entry_review, import_entry_state, pending_import_review, song_field_issues


class StationManagement:
    def __init__(self, configuration, catalog, information, policies, *, playback=None, artwork=None, programming=None):
        self.configuration, self.catalog = configuration, catalog
        self.information, self.policies, self.playback = information, policies, playback
        self.artwork = artwork
        self.programming = programming

    def snapshot(self, *, authenticated):
        overview = self.information.overview()
        unresolved = overview['unassociated_songs'] + overview['unnamed_artists']
        requirements = [bool(authenticated), overview['songs'] + overview['youtube_tracks'] > 0,
            bool(self.configuration.get('catalog_reviewed')) and unresolved == 0,
            bool(self.policies.active_policies(kind='default'))]
        complete = all(requirements) and self.configuration.get('onboarding_completed') is True
        names = [('connection', 'Connect your host'), ('music', 'Import community music'),
                 ('catalog', 'Review your music'), ('policies', 'Agree on station defaults')]
        steps = [{'id': key, 'title': title, 'complete': done} for (key, title), done in zip(names, requirements)]
        current = next((step['id'] for step in steps if not step['complete']), 'finish')
        playback = self.playback.status() if self.playback is not None else {'state': 'idle', 'suno_song_id': None}
        now_playing = None
        if playback.get('suno_song_id') and playback['state'] in ('playing', 'buffering', 'starting'):
            now_playing = self.song_detail(playback['suno_song_id']) | {'playback_state': playback['state']}
        result = {'onboarding': {'complete': bool(complete), 'current': current, 'steps': steps,
                    'unresolved_artists': unresolved},
                'catalog': {'songs': overview['songs'], 'artists': overview['artists']}
                    | ({'youtube_tracks': overview['youtube_tracks']} if overview['youtube_tracks'] else {}),
                'policies': self.policies.list_policies(limit=100),
                'now_playing': now_playing, 'playback_state': playback['state'],
                'station_status': {'playing': 'Playing', 'buffering': 'Buffering', 'starting': 'Starting'}.get(playback['state'], 'Idle')}
        if self.programming is not None:
            program = self.programming.public_state()
            result['programming'] = program | {'ready': all(requirements), 'available': True}
            if program['running']:
                result['station_status'] = ('Finishing current song' if program['stop_requested'] and program['phase'] != 'signing_off'
                    else 'Reconnecting audio' if program['phase'] == 'playing' and playback['state'] == 'buffering' and program.get('playback_error')
                    else {'planning': 'Preparing program', 'greeting': 'Greeting listeners', 'introducing': 'Introducing a song',
                          'starting': 'Loading audio', 'playing': 'Playing', 'outro': 'Radio host',
                          'signing_off': 'Signing off', 'playback_error': 'Playback needs attention'}.get(program['phase'], 'Idle'))
        return result

    def start_programming(self, *, authenticated):
        if self.programming is None:
            raise ValueError('Programming is not available.')
        if not all(step['complete'] for step in self.snapshot(authenticated=authenticated)['onboarding']['steps']):
            raise ValueError('Connect your host, review your catalog, and approve station defaults before starting.')
        self.finish_onboarding(authenticated=authenticated)
        return self.programming.start()

    def stop_programming(self):
        if self.programming is None:
            raise ValueError('Programming is not available.')
        return self.programming.stop()

    def catalog_page(self, *, limit=20, offset=0, query=''):
        self.information.validate_page(limit, offset)
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError('Search text must be 200 characters or fewer.')
        if query.strip():
            songs, total = self.catalog.search_songs(query.strip(), limit=limit, offset=offset)
        else:
            songs = self.catalog.list_songs(limit=limit, offset=offset)
            total = self.information.overview()['songs']
        items = []
        for song in songs:
            artist = self.catalog.get_artist(song['artist_id']) if song['artist_id'] else None
            issues = song_field_issues(song, artist)
            items.append({key: song[key] for key in ('suno_song_id', 'title', 'artist_id')} |
                         {'artist_name': artist['preferred_display_alias'] if artist else None,
                          'field_issues': issues, 'artwork_url': self._artwork_url(song)})
        return {'items': items, 'total': total, 'offset': offset}

    def artist_page(self, *, offset=0):
        if type(offset) is not int or offset < 0:
            raise ValueError('Invalid artist page.')
        return self.catalog.list_artists(limit=100, offset=offset)

    def youtube_page(self, *, offset=0):
        self.information.validate_page(20, offset)
        return {'items': self.catalog.list_youtube_tracks(limit=20, offset=offset), 'offset': offset,
                'total': self.information.overview()['youtube_tracks']}

    def youtube_detail(self, video_id):
        track = self.catalog.get_youtube_track(video_id)
        if track is None:
            raise RecordNotFound('YouTube submission does not exist.')
        return {'track': track, 'submissions': self.catalog.list_youtube_submissions(video_id),
                'artist': self.catalog.get_artist(track['artist_id']) if track['artist_id'] else None}

    def song_detail(self, song_id):
        song = self.catalog.get_song(song_id)
        if song is None:
            raise RecordNotFound('This song is no longer in the catalog.')
        return {'song': song, 'artist': self.catalog.get_artist(song['artist_id']) if song['artist_id'] else None,
                'statistics': self.information.metrics.song_statistics(song_id),
                'field_issues': song_field_issues(song, self.catalog.get_artist(song['artist_id']) if song['artist_id'] else None),
                'duration_seconds': None, 'artwork_url': self._artwork_url(song)}

    def _artwork_url(self, song):
        if self.artwork is not None and self.artwork.path(song['artwork_file']):
            return '/api/catalog/' + quote(song['suno_song_id'], safe='') + '/artwork'
        return None

    def artwork_path(self, song_id):
        song = self.catalog.get_song(song_id)
        path = self.artwork.path(song['artwork_file']) if song and self.artwork is not None else None
        if path is None:
            raise RecordNotFound('No saved artwork is available for this song.')
        return path

    def import_review(self, *, batch_id=None, offset=0):
        return pending_import_review(self.catalog, batch_id=batch_id, offset=offset)

    def import_review_entry(self, item_id):
        if type(item_id) is not int or item_id < 1:
            raise ValueError('Choose a valid import entry.')
        entry = self.catalog.get_batch_item(item_id)
        if entry is None or entry['outcome'] != 'failed':
            raise RecordNotFound('Unresolved import entry does not exist.')
        return import_entry_review(entry) | {'review_state': import_entry_state(entry, self.catalog)}

    def save_song(self, song_id, data):
        if not isinstance(data, dict) or data.keys() - {'metadata', 'artist_id', 'artist'}:
            raise ValueError('Please check the song details.')
        current = self.song_detail(song_id)
        metadata = data.get('metadata', {})
        if not isinstance(metadata, dict) or metadata.keys() - {'title', 'lyrics', 'style', 'suno_username'} or any(value is not None and not isinstance(value, str) for value in metadata.values()):
            raise ValueError('Song metadata must contain text values.')
        artist_id = data.get('artist_id', current['song']['artist_id'])
        if artist_id is not None and (type(artist_id) is not int or artist_id < 1 or self.catalog.get_artist(artist_id) is None):
            raise ValueError('Choose an existing artist or create a new artist.')
        artist = data.get('artist', {})
        if not isinstance(artist, dict) or artist.keys() - {'preferred_display_alias', 'aliases'}:
            raise ValueError('Please check the artist details.')
        name, aliases = artist.get('preferred_display_alias'), artist.get('aliases', [])
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise ValueError('Enter a preferred artist name.')
        if not isinstance(aliases, list) or any(not isinstance(alias, str) or not alias.strip() for alias in aliases):
            raise ValueError('Enter one nonempty alias per line.')
        for alias in aliases + ([name] if name is not None else []):
            owner = self.catalog.resolve_artist_alias(alias)
            if owner is not None and owner['id'] != artist_id:
                raise AliasConflict(f'The alias “{alias}” already belongs to another artist. Choose that artist instead of merging identities.')
        if artist_id is None and name is not None:
            artist_id = self.catalog.create_artist(preferred_display_alias=name, aliases=aliases)['id']
        elif artist_id is not None and artist:
            changes = {'preferred_display_alias': name} if name is not None else {}
            self.catalog.update_artist(artist_id, aliases=aliases if 'aliases' in artist else None, **changes)
        self.catalog.update_song_metadata(song_id, **metadata)
        if 'artist_id' in data or artist_id is not None:
            self.catalog.associate_song(song_id, artist_id)
        return self.song_detail(song_id)

    def review_catalog(self):
        overview = self.information.overview()
        if not (overview['songs'] or overview['youtube_tracks']):
            raise ValueError('Import some songs before reviewing the catalog.')
        if overview['unassociated_songs'] or overview['unnamed_artists']:
            raise ValueError('Give each song an artist and each artist a preferred name before completing this review.')
        self.configuration.update(catalog_reviewed=True)
        return {'reviewed': True}

    def finish_onboarding(self, *, authenticated):
        state = self.snapshot(authenticated=authenticated)
        if not all(step['complete'] for step in state['onboarding']['steps']):
            raise ValueError('Finish the remaining setup steps first.')
        self.configuration.update(onboarding_completed=True)
        return {'completed': True}
