"""Targeted public song-page retrieval. No catalog, application, or auth dependency."""
from html.parser import HTMLParser
from http.client import HTTPException
import json
import re
import time
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .urls import song_identity

__all__ = ['SunoMetadataService', 'SunoMetadataError']


class SunoMetadataError(Exception):
    pass


class _SunoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        parsed = urlsplit(new_url)
        if parsed.scheme != 'https' or parsed.hostname not in ('suno.com', 'www.suno.com'):
            raise SunoMetadataError('Suno redirected away from its public song page. This song was not imported.')
        return super().redirect_request(request, response, code, message, headers, new_url)


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self._current = []

    def handle_data(self, data):
        if self._current is not None:
            self._current.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self._current is not None:
            self.scripts.append(''.join(self._current))
            self._current = None


def _find_song(value, song_id):
    if isinstance(value, dict):
        if value.get('id') == song_id and isinstance(value.get('metadata'), dict):
            return value
        children = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return None
    for child in children:
        found = _find_song(child, song_id)
        if found is not None:
            return found
    return None


def _flight_rows(chunks):
    """Decode JSON rows and observed byte-length-prefixed text references."""
    data = ''.join(chunks).encode('utf-8')
    position, values, references = 0, [], {}
    while position < len(data):
        header = re.match(rb'([0-9a-f]+):', data[position:])
        if header:
            row_id = header[1].decode('ascii')
            start = position + header.end()
            text_header = re.match(rb'T([0-9a-f]+),', data[start:])
            if text_header:
                text_start = start + text_header.end()
                text_end = text_start + int(text_header[1], 16)
                if text_end <= len(data):
                    try:
                        references[row_id] = data[text_start:text_end].decode('utf-8')
                    except UnicodeError:
                        pass
                position = text_end
                continue
        end = data.find(b'\n', position)
        end = len(data) if end < 0 else end
        if header:
            try:
                value = json.loads(data[start:end])
            except (ValueError, UnicodeError):
                pass
            else:
                values.append(value)
                references[row_id] = value
        position = end + 1
    return values, references


def parse_song_page(page: str, song_id: str) -> dict:
    """Read the observed Next.js flight data; never evaluate embedded JavaScript."""
    parser = _Scripts()
    parser.feed(page)
    chunks = []
    for script in parser.scripts:
        match = re.search(r'self\.__next_f\.push\(', script)
        if match:
            try:
                data, _ = json.JSONDecoder().raw_decode(script[match.end():])
            except ValueError:
                continue
            if isinstance(data, list) and len(data) > 1 and data[0] == 1 and isinstance(data[1], str):
                chunks.append(data[1])
    values, references = _flight_rows(chunks)
    for data in values:
        song = _find_song(data, song_id)
        if song is not None:
            def text(value):
                if not isinstance(value, str) or not value:
                    return None
                if value.startswith('$$'):
                    return value[1:]
                if value.startswith('$'):
                    resolved = references.get(value[1:])
                    return resolved if isinstance(resolved, str) else None
                return value
            metadata = song['metadata']
            return {
                'suno_song_id': song_id, 'title': text(song.get('title')),
                'lyrics': text(metadata.get('prompt')), 'style': text(metadata.get('tags')),
                'suno_username': text(song.get('handle')),
                'artwork_url': text(song.get('image_large_url')) or text(song.get('image_url')),
            }
    raise SunoMetadataError('No metadata for this song was available on the public page. It may be private, removed, or use an unsupported page format.')


class SunoMetadataService:
    def __init__(self, *, timeout=15):
        self.timeout = timeout
        self._opener = build_opener(_SunoRedirects())
        self._lock = Lock()
        self._last_request = 0.0

    def fetch_song(self, song_id_or_url: str) -> dict:
        _, original_url = song_identity(song_id_or_url)
        song_id = self.resolve_song_id(song_id_or_url)
        _, page = self._request(f'https://suno.com/song/{song_id}')
        metadata = parse_song_page(page, song_id)
        return metadata | {'original_suno_url': original_url}

    def resolve_song_id(self, song_id_or_url: str) -> str:
        """Resolve a specific share link via HEAD, without fetching song metadata."""
        song_id, original_url = song_identity(song_id_or_url)
        if song_id is not None:
            return song_id
        final_url, _ = self._request(original_url.replace('http://', 'https://', 1), method='HEAD')
        try:
            resolved, _ = song_identity(final_url)
        except ValueError as exc:
            raise SunoMetadataError('The Suno share link did not resolve to a song page.') from exc
        if resolved is None:
            raise SunoMetadataError('The Suno share link did not resolve to a song page.')
        return resolved

    def _request(self, url, *, method='GET'):
        # Always construct the request from the validated ID, never an arbitrary URL.
        request = Request(url, method=method, headers={
            'User-Agent': 'KernRadio/0.1 (public song metadata)', 'Accept': 'text/html',
        })
        with self._lock:
            time.sleep(max(0, .5 - (time.monotonic() - self._last_request)))
            self._last_request = time.monotonic()
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    final_url = response.geturl()
                    content = response.read(4_000_001) if method == 'GET' else b''
                    if len(content) > 4_000_000:
                        raise SunoMetadataError('The public song page was too large to read. This song was not imported.')
                    page = content.decode('utf-8')
            except HTTPError as exc:
                if exc.code == 429:
                    message = 'Suno is asking us to slow down. Please try this entry again later.'
                elif exc.code in (403, 404):
                    message = 'This song is unavailable or Suno is blocking public access. Check the song link.'
                else:
                    message = 'Suno could not provide this song right now. Please try again later.'
                raise SunoMetadataError(message) from exc
            except (URLError, OSError, UnicodeError, HTTPException) as exc:
                raise SunoMetadataError('Could not reach Suno. Check your connection and try this entry again.') from exc
        return final_url, page
