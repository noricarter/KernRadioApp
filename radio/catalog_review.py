"""Review presentation facts, without guessing metadata or correcting ingestion."""
from collections import Counter

from services.discord_text import DiscordTextParser, field_occurrences
from services.catalog import RecordNotFound
from services.suno.urls import SONG_LINK


def import_entry_state(entry, catalog, *, parser=None):
    """Derive current coverage from exact catalog IDs/URLs, without network or writes.

    The historical failed outcome stays unchanged. A group is covered only when
    every recognized post has a Suno link and every target is already stored.
    """
    source = entry['source_text'] or ''
    parsed = (parser or DiscordTextParser()).parse(source) if source.strip() else None
    if parsed is None:
        return {'resolved': False, 'parsed_songs': 0, 'catalog_songs': 0, 'unrecognized_posts': 1}
    existing = sum(bool(catalog.get_youtube_track(post.youtube_video_id) if post.source == 'youtube'
                       else catalog.get_song(post.suno_song_id) if post.suno_song_id
                       else catalog.get_song_by_url(post.original_suno_url)) for post in parsed.submissions)
    return {'resolved': bool(parsed.submissions) and not parsed.unresolved and existing == len(parsed.submissions),
            'parsed_songs': len(parsed.submissions), 'catalog_songs': existing,
            'unrecognized_posts': len(parsed.unresolved),
            'remaining_reasons': sorted({entry.reason for entry in parsed.unresolved})}


def pending_import_review(catalog, *, batch_id=None, limit=20, offset=0):
    """Current review queue; batch counts still report original import outcomes."""
    if type(limit) is not int or not 1 <= limit <= 20 or type(offset) is not int or offset < 0:
        raise ValueError('Use pages of 1–20 records and a nonnegative offset.')
    if batch_id is not None and (type(batch_id) is not int or batch_id < 1):
        raise ValueError('Choose a valid batch ID.')
    batches, selected_items, selected_batch = [], [], None
    summaries = catalog.list_batch_summaries(limit=100)
    if batch_id is not None and not any(batch['id'] == batch_id for batch in summaries):
        summaries += catalog.list_batch_summaries(batch_id=batch_id, limit=1)
        if not any(batch['id'] == batch_id for batch in summaries):
            raise RecordNotFound('Import batch does not exist.')
    parser = DiscordTextParser()
    for batch in summaries:
        pending, item_offset = [], 0
        while items := catalog.list_batch_items(batch['id'], outcome='failed', limit=100, offset=item_offset):
            for item in items:
                state = import_entry_state(item, catalog, parser=parser)
                if not state['resolved']:
                    source = item.pop('source_text') or ''
                    pending.append(item | {'review_state': state, 'source_excerpt': source[:500],
                                           'source_total_characters': len(source)})
            item_offset += len(items)
        batch = batch | {'unresolved_count': len(pending)}
        if pending:
            batches.append(batch)
        if batch['id'] == batch_id or (batch_id is None and selected_batch is None and pending):
            selected_batch, selected_items = batch, pending
    return {'batches': batches, 'batch': selected_batch, 'items': selected_items[offset:offset + limit],
            'total': len(selected_items), 'limit': limit, 'offset': offset,
            'outcome': 'unresolved', 'unresolved_total': sum(batch['unresolved_count'] for batch in batches)}


def song_field_issues(song, artist):
    issues = {}
    if not song['title'] or not song['title'].strip():
        issues['title'] = 'No display title is available. Please check the title.'
    if song['artist_id'] is None:
        issues['artist_id'] = 'This song has no artist association. Choose or create its artist.'
    if artist is None or not (artist['preferred_display_alias'] or '').strip():
        issues['preferred_display_alias'] = 'A preferred artist name is needed.'
    for field, label in (('style', 'style information'), ('suno_username', 'Suno creator name')):
        if not (song[field] or '').strip():
            issues[field] = f'No {label} was available. Review if you have this information.'
    if not song['lyrics']:
        issues['lyrics'] = 'No lyrics were available. Instrumental songs may have none.'
    return issues


def import_entry_review(entry):
    """Highlight exact repeated fields or links; preserve the original source."""
    source, error = entry['source_text'] or '', entry['error'] or ''
    spans, questions = [], []
    if 'message boundaries' in error:
        fields = field_occurrences(source)
        repeated = Counter(field['field'] for field in fields)
        seen = set()
        for field in fields:
            if repeated[field['field']] > 1:
                # Highlight the labelled line, not following embed previews or
                # another author's header included in this unseparated block.
                line_end = source.find('\n', field['start'], field['end'])
                end = line_end if line_end >= 0 else field['end']
                spans.append((field['start'], end, field['label']))
                if field['field'] not in seen:
                    questions.append({'field': field['field'], 'label': field['label'],
                        'reason': 'Repeated in this entry; which post owns each value is unclear.'})
                    seen.add(field['field'])
        explanation = 'Several posts were grouped together. The highlighted repeated fields need their post associations checked; no value has been assigned to a song.'
    else:
        spans = [(match.start(), match.end(), 'Song link') for match in SONG_LINK.finditer(source)]
        questions = [{'field': 'original_suno_url', 'label': 'Song link',
            'reason': error or 'Check the original song link.'}]
        explanation = 'Check the song link and the saved failure reason before importing a correction.'
    parts, cursor = [], 0
    for start, end, label in spans:
        if start > cursor:
            parts.append({'text': source[cursor:start], 'questioned': False})
        parts.append({'text': source[start:end], 'questioned': True, 'label': label})
        cursor = end
    if cursor < len(source):
        parts.append({'text': source[cursor:], 'questioned': False})
    return entry | {'explanation': explanation, 'questioned_fields': questions, 'source_parts': parts}
