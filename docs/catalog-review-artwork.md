# Catalog review and song artwork

Launch normally with `uv run main.py`; restart a previously running process to
load the updated review window and artwork routes.

## Review window

The catalog dialog now includes a highlighted **Needs attention** section above
the imported-song list. Kern's batch #1 has 19 failed import outcomes. They are
not catalog song rows: some contain multiple combined posts without recognized
message boundaries. Selecting an outcome opens its exact original copied text,
failure reason, and a list of questioned fields. Repeated Title, Genre, About,
Content Warning, and Link fields are highlighted where present. The UI marks
their uncertain post association, without claiming the actual values are wrong.
Song lookup failures instead highlight the relevant link. Unicode is preserved
by rendering text segments, not injecting HTML or sharing character offsets
between Python and JavaScript.

The most recent batch with failures opens by default; the batch selector retains
access to earlier failures among the most recent 100 batches. Each list pages
through 20 results at a time. New successful imports do not hide earlier failures.
**Prepare corrected import** places the original text in the existing import
form for Kern to edit/separate before importing through the existing pipeline.

Original batch outcomes remain historical records. The current review list now
excludes a group once all its posts are recognizable and all their exact Suno
IDs/original share URLs are present in the catalog, including after Kern imports
the songs separately. Historical outcomes are never rewritten. Missing songs,
unsupported links, and still-ambiguous posts remain pending. No artist merging
or retry queue was introduced. Catalog-review confirmation retains its existing
prerequisites. See [the grouped-post follow-up](music-ingestion.md#follow-up-grouped-posts-and-current-resolution-status).

The song list marks records with missing metadata. The editor highlights the
specific fields and provides explanatory text: missing title, artist association,
preferred name, style, creator, or lyrics. Optional unavailable information is
identified as such; instrumental songs may legitimately have no lyrics, and
empty aliases are valid. Saving a correction reloads the persisted record and
removes the corresponding marker. Playback statistics remain read-only.

## Components and interfaces

- `services.discord_text.field_occurrences(text)` locates known copied-post
  field labels without assigning ambiguous posts to songs. Parsing now splits
  timestamped author headers before optional Suno previews, fixing the grouped
  submissions identified during review.
- `radio.catalog_review` derives presentation facts (`song_field_issues` and
  `import_entry_review`) from actual stored records. `import_entry_state` and
  `pending_import_review` derive current catalog coverage without modifying data.
- `StationManagement.import_review(batch_id=None, offset=0)` returns batch
  summaries and failed-entry pages. `import_review_entry(item_id)` returns the
  selected original with highlighted segments and field explanations.
- `GET /api/import-review` and `GET /api/import-review/<item_id>` are thin Flask
  adapters. Catalog responses now include `field_issues` and local `artwork_url`.
- `radio/static/review.js` owns failed-entry presentation. The existing catalog
  editor owns metadata fields, highlights, artwork, and saves.
- `services.suno.SunoMetadataService.fetch_song(...)` now includes `artwork_url`
  from the exact matching song's `image_large_url` or `image_url`. It never uses
  an artist avatar or another song's artwork.
- `services.artwork.ArtworkService(directory)` encapsulates downloading and
  caching source image bytes. `fetch(url)` returns an opaque cache filename;
  `path(filename)` returns an existing safe local path or `None`. It has no
  database, Flask, agent, or application dependency.
- `radio.artwork.CatalogArtwork(catalog, metadata, artwork)` composes the
  services. `cache_song(song_id)` downloads a known artwork URL. `backfill()`
  targets only catalog songs missing local artwork, fetching song metadata
  only when an artwork URL is absent. It updates only artwork columns.
- `StationManagement.artwork_path(song_id)` and
  `GET /api/catalog/<song_id>/artwork` serve cached originals from this computer.

No additional dependencies are required. Downloads accept HTTPS on the observed
Suno image CDNs, preserve JPEG/PNG/WebP bytes, restrict redirects to those CDNs,
use a 15-second timeout and a 10 MB limit, and write cache files atomically. Image
failures are reported without undoing successful song ingestion. Known image
URLs reuse cached files, including after restart. Catalog metadata requests
retain their existing sequential request spacing; no catalog crawling occurs.

## Schema and local storage

Two nullable columns are added to `songs` automatically and transactionally:

- `artwork_url`: original Suno song artwork URL.
- `artwork_file`: opaque filename within the supplied local artwork directory.

The application uses `~/.config/kernradio/artwork` (or the corresponding
`XDG_CONFIG_HOME` location). The generic SQLite service remains unchanged. There
are no additional database tables or calculated-statistic fields. Catalog
backups should include both the database and artwork directory.

The largest image available from Suno's metadata is saved unmodified. Small list
previews currently display that same file at a smaller CSS size. Actual thumbnail
generation is deferred; a future thumbnail capability can read the original via
`ArtworkService.path(song['artwork_file'])` and store a separate derivative.

## Existing-catalog backfill

The requested backfill was completed against Kern's actual catalog:

- **79 songs; 79 original images saved; 0 unavailable; 0 failed downloads.**
- Total cached source bytes: **30,245,165**.
- Existing song metadata, artists/aliases, Discord submissions, batch history,
  playback history, and policies matched the pre-backfill backup exactly.
- All **19 failed import outcomes** remain intact for review.

Backup: `~/.config/kernradio/catalog-before-artwork-20261109T083738408957Z.sqlite3`.
Evidence: [artwork-backfill-result.json](artwork-backfill-result.json).

To explicitly check for missing artwork again:

```sh
uv run python -m radio.backfill_artwork
```

This makes a SQLite backup before migration/writes. It skips cached songs,
preserves edits, and reports unavailable images or lookup/download failures.
It does not run automatically at startup or refetch already cached images.

## Verification

The full offline suite passes **172 tests**. Sixteen added tests cover image
selection, byte preservation/cache reuse, bounded/failing downloads, old-schema
migration, preservation of edited metadata, image HTTP delivery, ingestion
warnings, all 19 failed records, exact Unicode highlighting, historical batch
selection, and field markers disappearing after saved corrections. Offline
network behavior is mocked; SQLite and orchestration are real isolated services.

`uv run python -m tests.artwork_live_check` separately imported the four supplied
real Suno songs into isolated storage, downloaded all four original images,
served identical bytes over Flask HTTP, and verified restart persistence and
zero repeat network requests for reimport/cached backfill. Evidence:
[artwork-live-result.json](artwork-live-result.json). The production backfill
above also used actual targeted Suno requests and image downloads.

Actual Chrome/Flask renders were inspected at **1366×900, 1024×768, and
1680×941**, using an isolated copy of Kern's real catalog and originals. The
browser showed all 19 failures, selected the original source with ten highlighted
field occurrences, preserved the exact original Unicode text, loaded real
artwork (1254×1254 for the selected song), highlighted its missing artist fields,
and saved an isolated correction. Markers cleared after save; the 19 failure
records remained after refresh. Programming controls stayed disabled. No browser
JavaScript errors or horizontal overflow occurred. Shorter desktop windows keep
the dialog's action buttons visible while its lists and editor scroll.

Evidence:

- [Unresolved import review](screenshots/review-unresolved-1366.png)
- [Song artwork and questioned fields](screenshots/review-fields-artwork-1366.png)
- [Review at 1024 pixels](screenshots/review-unresolved-1024.png)
- [Browser results](review-artwork-browser-result.json)

Run browser verification only against an isolated `tests.ui_server` catalog;
`node tests/review_browser_check.mjs <test-server-url>` makes a test metadata edit.
No original authentication or production metadata was changed by browser tests.
