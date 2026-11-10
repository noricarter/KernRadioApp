"""Identify supplied YouTube video links. No fetching, playback, or metadata rules."""
import re
from urllib.parse import parse_qs, urlsplit

YOUTUBE_LINK = re.compile(r'https?://(?:(?:www\.)?youtube\.com|youtu\.be)/[^\s<>"\]\)]+', re.IGNORECASE)


def video_identity(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('youtu.be', 'youtube.com', 'www.youtube.com'):
        raise ValueError('Use a YouTube video link.')
    if parsed.hostname == 'youtu.be':
        video_id = parsed.path.strip('/')
    elif parsed.path == '/watch':
        video_id = parse_qs(parsed.query).get('v', [''])[0]
    else:
        video_id = ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise ValueError('This YouTube link does not identify a video.')
    return video_id
