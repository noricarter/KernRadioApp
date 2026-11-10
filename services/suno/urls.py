"""Canonical song identity validation shared by text extraction and retrieval."""
import re
from urllib.parse import urlsplit

SONG_ID = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')
SONG_LINK = re.compile(r'https?://(?:www\.)?suno\.com/[^\s<>"\]\)]+', re.IGNORECASE)


def song_identity(value: str) -> tuple[str | None, str]:
    if not isinstance(value, str):
        raise ValueError('Provide a Suno song ID or a full Suno song link.')
    if SONG_ID.fullmatch(value):
        song_id = value.lower()
        return song_id, f'https://suno.com/song/{song_id}'
    parsed = urlsplit(value)
    if (parsed.scheme not in ('https', 'http') or parsed.hostname not in ('suno.com', 'www.suno.com')
            or parsed.username or parsed.password or parsed.port not in (None, 80, 443)):
        raise ValueError('Use a full https://suno.com/song/… link.')
    match = re.fullmatch(r'/song/(' + SONG_ID.pattern + r')/?', parsed.path)
    if re.fullmatch(r'/s/[A-Za-z0-9]+/?', parsed.path):
        return None, value
    if not match:
        raise ValueError('This Suno link is not supported. Copy a song-page or /s/ share link.')
    return match[1].lower(), value
