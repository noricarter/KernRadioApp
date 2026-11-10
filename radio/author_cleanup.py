"""Remove copied Discord badges from stored author names, retaining identity IDs."""
from services.discord_text import clean_discord_author


def clean_role_names(catalog):
    changed, offset = [], 0
    while artists := catalog.list_artists(limit=100, offset=offset):
        for artist in artists:
            fields = {key: clean_discord_author(artist[key]) for key in ('discord_handle', 'preferred_display_alias')}
            aliases = list(dict.fromkeys(clean_discord_author(alias) for alias in artist['aliases']))
            aliases = [alias for alias in aliases if alias]
            if any(fields[key] != artist[key] for key in fields) or set(aliases) != set(artist['aliases']):
                catalog.update_artist(artist['id'], aliases=aliases, **fields)
                changed.append(artist['id'])
        offset += len(artists)
    offset = 0
    while submissions := catalog.list_submissions(limit=100, offset=offset):
        for post in submissions:
            author = clean_discord_author(post['discord_handle'])
            if author and author != post['discord_handle']:
                catalog.update_submission_author(post['id'], author)
        offset += len(submissions)
    return changed
