"""Extract the observed Crucible copied-post structure, without external services."""
from dataclasses import dataclass
from datetime import datetime, timedelta
import re

from services.suno.urls import SONG_LINK, song_identity
from services.youtube import YOUTUBE_LINK, video_identity


@dataclass(frozen=True)
class Submission:
    suno_song_id: str | None  # Short share links need network resolution later.
    original_suno_url: str
    discord_handle: str | None
    original_message: str
    submitted_title: str | None = None
    genre: str | None = None
    description: str | None = None
    content_warning: str | None = None
    preferred_artist_name: str | None = None
    discord_timestamp: str | None = None
    posted_at: str | None = None
    timestamp_error: str | None = None
    source: str = 'suno'
    youtube_video_id: str | None = None


@dataclass(frozen=True)
class UnresolvedEntry:
    source_text: str
    reason: str


@dataclass(frozen=True)
class ParsedBatch:
    submissions: list[Submission]
    unresolved: list[UnresolvedEntry]


# The supplied copy includes inline fields, decorated author headers, and
# repeated Suno embed titles. Match that structure, not arbitrary Discord exports.
_HEADER = re.compile(r'^\s*(?P<author>[^\r\n—]{1,200}?)\s+—\s+(?P<timestamp>(?:(?:Yesterday|Today)\s+at\s+|\d{1,2}/\d{1,2}/(?:\d{4}|\d{2}),\s*)?\d{1,2}:\d{2}\s*[AP]M)\b', re.IGNORECASE | re.MULTILINE)
_PREVIEW = re.compile(r'\bSuno\s+(?P<title>[^\r\n]+?)\s+Listen and make your own on Suno\.\s+(?P=title)', re.DOTALL)
_FIELD = re.compile(r'(?<!\w)(Song Title|Title|Song Link|Song|Link|Genres?|Song Description|Description|About(?: (?:the|this) song)?|Content Warnings?|C/W|CW|Server Profile)\s*(?:⚠\ufe0f?\s*)?:\s*', re.IGNORECASE)
_FIELDS = {'title': 'submitted_title', 'song title': 'submitted_title', 'genre': 'genre', 'genres': 'genre',
           'description': 'description', 'song description': 'description', 'about': 'description', 'about the song': 'description', 'about this song': 'description',
           'content warning': 'content_warning', 'content warnings': 'content_warning', 'cw': 'content_warning', 'c/w': 'content_warning'}


def field_occurrences(text):
    """Locate known copied-post fields without assigning ambiguous posts to songs.

    Character offsets refer to the original Python string; consumers may render
    string segments to preserve Unicode across different runtime encodings.
    """
    matches = list(_FIELD.finditer(text))
    return [{'field': _FIELDS.get(match[1].lower(), 'original_suno_url'),
             'label': match[1], 'start': match.start(),
             'end': matches[index + 1].start() if index + 1 < len(matches) else len(text)}
            for index, match in enumerate(matches) if match[1].lower() != 'server profile']


def clean_discord_author(value):
    """Badge text and everything after it are UI decoration, never an author."""
    if value is None:
        return None
    return re.split(r'\s*,?\s*Role\s+icon\b', value, maxsplit=1, flags=re.IGNORECASE)[0].strip().rstrip(',').rstrip()


def _post_time(timestamp, reference_time):
    """Resolve the post's local calendar date; never persist the reference clock."""
    date = reference_time.date()
    value = timestamp
    if value.lower().startswith('yesterday at '):
        date -= timedelta(days=1)
        value = value[len('Yesterday at '):]
    elif value.lower().startswith('today at '):
        value = value[len('Today at '):]
    elif '/' in value:
        day, value = value.split(',', 1)
        date = datetime.strptime(day, '%m/%d/%Y' if len(day.split('/')[-1]) == 4 else '%m/%d/%y').date()
    time = datetime.strptime(value.strip().upper(), '%I:%M %p').time()
    result = datetime.combine(date, time)
    # A naive reference means the machine's local timezone, including the
    # offset appropriate to the post's date. Tests may supply an aware reference.
    result = result.replace(tzinfo=reference_time.tzinfo) if reference_time.tzinfo else result.astimezone()
    return result.isoformat()


def _blocks(text):
    # Timestamped author lines are stronger boundaries than optional/truncated
    # Suno previews. Split them first, preserving every character of each post.
    headers = list(_HEADER.finditer(text))
    starts = [0] + [header.start() for header in headers if header.start() > 0]
    # Leading whitespace belongs to the first post, not an empty extra block.
    if len(starts) > 1 and not text[:starts[1]].strip():
        starts.pop(1)
    for start, end in zip(starts, starts[1:] + [len(text)]):
        yield from _preview_blocks(text[start:end])


def _preview_blocks(text):
    cursor = 0
    for preview in _PREVIEW.finditer(text):
        end = preview.end()
        while end < len(text) and text[end].isspace():
            end += 1
        yield text[cursor:end], text[cursor:preview.start()]
        cursor = end
    remainder = text[cursor:]
    if _HEADER.match(remainder):
        blocks = [remainder]
    else:
        blocks = re.split(r'\r?\n[ \t]*\r?\n', remainder)
    for block in blocks:
        if block.strip():
            # The supplied older post ends with a partial Suno preview.
            yield block, re.split(r'(?:^|\r?\n)(?:Suno|YouTube)\s*\r?\n', block, maxsplit=1)[0]


class DiscordTextParser:
    def parse(self, text: str, *, reference_time: datetime | None = None) -> ParsedBatch:
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Paste some Discord messages first.')
        submissions, unresolved = [], []
        reference_time = reference_time if reference_time is not None else datetime.now()
        for original, body in _blocks(text):
            header = _HEADER.match(body)
            author = None
            timestamp = posted_at = timestamp_error = None
            if header:
                # Some copies concatenate the badge directly: SeraphymRole icon.
                author = clean_discord_author(header['author'])
                timestamp = header['timestamp']
                try:
                    posted_at = _post_time(timestamp, reference_time)
                except ValueError:
                    timestamp_error = 'The copied post date could not be interpreted; its original timestamp was saved for review.'
                body = body[header.end():]
            preferred = re.sub(r'(?:\s*\[[^\[\]\r\n]+\])+\s*$', '', author).strip() if author else None
            links = sorted([*SONG_LINK.finditer(body), *YOUTUBE_LINK.finditer(body)], key=lambda match: match.start())
            if not links:
                unresolved.append(UnresolvedEntry(original, 'No Suno or YouTube song link was found in this text.'))
                continue
            fields = {}
            matches = list(_FIELD.finditer(body))
            ambiguous = False
            for index, match in enumerate(matches):
                field = _FIELDS.get(match[1].lower())
                if field is None:
                    continue
                if field in fields:
                    ambiguous = True
                end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
                value = body[match.end():end]
                link = next(iter(sorted([*SONG_LINK.finditer(value), *YOUTUBE_LINK.finditer(value)], key=lambda match: match.start())), None)
                if link:
                    value = value[:link.start()]
                fields[field] = value.strip() or None
            if ambiguous:
                unresolved.append(UnresolvedEntry(original, 'Multiple sets of post fields were found without recognized message boundaries. This format needs review before import.'))
                continue
            seen = set()
            for link in links:
                url = link[0].rstrip('.,;!')
                youtube = YOUTUBE_LINK.fullmatch(url) is not None
                try:
                    video_id = video_identity(url) if youtube else None
                    song_id, original_url = (None, url) if youtube else song_identity(url)
                except ValueError as exc:
                    unresolved.append(UnresolvedEntry(url, str(exc)))
                    continue
                key = ('youtube', video_id) if youtube else ('suno', song_id or original_url)
                if key not in seen:
                    submissions.append(Submission(song_id, original_url, author, original,
                        preferred_artist_name=preferred or None, discord_timestamp=timestamp,
                        posted_at=posted_at, timestamp_error=timestamp_error,
                        source='youtube' if youtube else 'suno', youtube_video_id=video_id, **fields))
                    seen.add(key)
        return ParsedBatch(submissions, unresolved)
