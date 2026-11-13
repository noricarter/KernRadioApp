"""Application-level composition of parsing, targeted retrieval, and catalog APIs."""
from collections import defaultdict
from datetime import datetime
from threading import Lock

from services.suno import SunoMetadataError
from services.catalog import AliasConflict
from services.artwork import ArtworkError


class MusicIngestion:
    def __init__(self, parser, metadata, catalog, *, clock=None, artwork=None):
        self.parser, self.metadata, self.catalog = parser, metadata, catalog
        self._lock = Lock()
        self._clock = clock or datetime.now
        self.artwork = artwork

    def import_text(self, text, *, progress=None):
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Paste some Discord messages first.')
        if len(text) > 100_000:
            raise ValueError('Please split these messages into smaller batches of up to 100,000 characters.')
        if not self._lock.acquire(blocking=False):
            raise ValueError('An import is already running. Please wait for it to finish.')
        try:
            return self._import(text, progress=progress)
        finally:
            self._lock.release()

    def _import(self, text, *, progress=None):
        parsed = self.parser.parse(text, reference_time=self._clock())
        total = len(parsed.unresolved) + len(parsed.submissions)
        completed = 0

        def report(stage, current='', advance=0):
            nonlocal completed
            completed = min(total, completed + advance)
            if progress is not None:
                progress({'stage': stage, 'current': str(current)[:160], 'completed': completed, 'total': total})

        report('ready', 'Preparing entries')
        batch = self.catalog.create_batch(source_text=text)
        results = []
        for entry in parsed.unresolved:
            self.catalog.record_batch_item(batch['id'], outcome='failed', source_text=entry.source_text, error=entry.reason)
            results.append({'outcome': 'failed', 'suno_song_id': None, 'title': None,
                            'error': entry.reason, 'source_text': entry.source_text})
            report('review', entry.reason, 1)
        grouped = defaultdict(list)
        resolved_urls = {}
        unresolved_share_counts = defaultdict(int)
        for submission in parsed.submissions:
            if submission.source == 'suno' and submission.suno_song_id is None:
                unresolved_share_counts[submission.original_suno_url] += 1
        youtube = defaultdict(list)
        for submission in parsed.submissions:
            if submission.source == 'youtube':
                youtube[submission.youtube_video_id].append(submission)
                continue
            url = submission.original_suno_url
            song_id = submission.suno_song_id
            if song_id is None:
                if url not in resolved_urls:
                    existing = self.catalog.get_song_by_url(url)
                    try:
                        report('resolving', submission.submitted_title or 'Suno share link')
                        resolved_urls[url] = existing['suno_song_id'] if existing else self.metadata.resolve_song_id(url)
                    except SunoMetadataError as exc:
                        resolved_urls[url] = None
                        self.catalog.record_batch_item(batch['id'], outcome='failed',
                            source_text=submission.original_message, error=str(exc))
                        results.append({'outcome': 'failed', 'suno_song_id': None,
                            'title': submission.submitted_title, 'error': str(exc),
                            'source_text': submission.original_message})
                        report('failed', submission.submitted_title or 'Suno share link', unresolved_share_counts[url])
                song_id = resolved_urls[url]
            if song_id is not None:
                grouped[song_id].append(submission)
        for song_id, submissions in grouped.items():
            # Attribution is preserved for failures and existing songs too.
            for post in submissions:
                self.catalog.store_submission(song_id, post.discord_handle or '', post.original_message,
                    submitted_title=post.submitted_title, genre=post.genre,
                    description=post.description, content_warning=post.content_warning, batch_id=batch['id'],
                    discord_timestamp=post.discord_timestamp, posted_at=post.posted_at)
            song = self.catalog.get_song(song_id)
            outcome, error = 'existing', None
            if song is None:
                try:
                    report('metadata', submissions[0].submitted_title or song_id)
                    metadata = self.metadata.fetch_song(song_id)
                    if metadata['suno_song_id'] != song_id:
                        raise SunoMetadataError('The returned metadata did not match the submitted song link.')
                    song, created = self.catalog.get_or_create_song(song_id, submissions[0].original_suno_url,
                        **{key: metadata.get(key) for key in ('title', 'lyrics', 'style', 'suno_username', 'artwork_url')})
                    outcome = 'added' if created else 'existing'
                except SunoMetadataError as exc:
                    outcome, error = 'failed', str(exc)
            warnings = [post.timestamp_error for post in submissions if post.timestamp_error]
            if outcome == 'added' and self.artwork is not None:
                try:
                    report('artwork', song['title'] or song_id)
                    song = self.artwork.cache_song(song_id)
                except ArtworkError as exc:
                    warnings.append(str(exc) + ' The song itself was imported.')
            if song is not None and song['artist_id'] is None:
                author_post = next((post for post in submissions if post.preferred_artist_name), None)
                if author_post:
                    try:
                        artist = self._default_artist(author_post)
                        song = self.catalog.associate_song(song_id, artist['id'])
                    except AliasConflict:
                        warnings.append('The Discord name conflicts with an existing artist alias. The song was saved; please review its artist identity.')
            self.catalog.record_batch_item(batch['id'], outcome=outcome, suno_song_id=song_id,
                source_text=submissions[0].original_message, error=error)
            if any(post.discord_handle is None for post in submissions):
                warnings.append('Discord author could not be identified; the original message was saved for review.')
            results.append({'outcome': outcome, 'suno_song_id': song_id,
                'title': song['title'] if song else submissions[0].submitted_title,
                'error': error, 'warnings': warnings,
                'missing_metadata': [key for key in ('title', 'lyrics', 'style', 'suno_username')
                                     if song is not None and song[key] is None]})
            report('saved' if outcome != 'failed' else 'failed',
                (song['title'] if song else submissions[0].submitted_title) or song_id, len(submissions))
        for video_id, posts in youtube.items():
            first = posts[0]
            try:
                artist = self._default_artist(first) if first.preferred_artist_name else None
            except AliasConflict:
                artist = None  # Save supplied YouTube data even if attribution needs review.
            track, created = self.catalog.get_or_create_youtube_track(video_id, first.original_suno_url,
                title=first.submitted_title, genre=first.genre, description=first.description,
                content_warning=first.content_warning, artist_id=artist['id'] if artist else None)
            for post in posts:
                self.catalog.store_youtube_submission(video_id, post.discord_handle or '', post.original_message,
                    submitted_title=post.submitted_title, genre=post.genre, description=post.description,
                    content_warning=post.content_warning, discord_timestamp=post.discord_timestamp,
                    posted_at=post.posted_at, batch_id=batch['id'])
            outcome = 'added' if created else 'existing'
            self.catalog.record_batch_item(batch['id'], outcome=outcome, youtube_video_id=video_id,
                source_text=first.original_message)
            results.append({'outcome': outcome, 'source': 'youtube', 'youtube_video_id': video_id,
                'suno_song_id': None, 'title': track['title'], 'error': None, 'warnings': [], 'missing_metadata': []})
            report('saved', track['title'] or 'YouTube submission', len(posts))
        completed = self.catalog.finish_batch(batch['id'])
        report('complete', 'Import complete')
        return {'batch_id': batch['id'], 'counts': completed['counts'], 'entries': results}

    def _default_artist(self, post):
        artist = (self.catalog.resolve_artist_alias(post.discord_handle)
                  or self.catalog.resolve_artist_alias(post.preferred_artist_name))
        if artist is None:
            return self.catalog.create_artist(discord_handle=post.discord_handle,
                preferred_display_alias=post.preferred_artist_name, aliases=[post.discord_handle])
        self.catalog.add_artist_alias(artist['id'], post.discord_handle)
        if artist['preferred_display_alias'] is None:
            artist = self.catalog.update_artist(artist['id'], preferred_display_alias=post.preferred_artist_name)
        return artist
