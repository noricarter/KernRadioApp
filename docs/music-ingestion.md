# Initial music ingestion

Launch the management application as before:

```sh
uv run main.py
```

Open `http://127.0.0.1:5000`, paste copied posts into **Import music**, and choose
**Import**. If an older application process is already running, restart it to
load the new form. The public illustrated radio frontend is unchanged.

The application creates `catalog.sqlite3` alongside its settings in
`~/.config/kernradio` (or under `XDG_CONFIG_HOME`). Imports need no Discord or
Suno login, administrator involvement, or Codex connection.

## Component boundaries and public interfaces

- `services.discord_text.DiscordTextParser.parse(text)` returns a `ParsedBatch`
  with immutable `Submission` and `UnresolvedEntry` records. It has no Flask,
  database, Codex, or network operations. Original Unicode and copied text are
  preserved. Canonical links include the song ID immediately; short share links
  have a `None` ID until the metadata service resolves them.
  `parse(text, reference_time=...)` also accepts a runtime local-date reference
  for resolving copied post timestamps; that reference is never persisted.
- `services.suno.SunoMetadataService.resolve_song_id(song_id_or_url)` accepts a
  UUID, full song link, or `/s/` share link. Share links are resolved with a
  targeted HEAD request, not a metadata download or catalog crawl.
- `SunoMetadataService.fetch_song(song_id_or_url)` retrieves one public song
  page. It returns `suno_song_id`, `original_suno_url`, `title`, `lyrics`, `style`,
  `suno_username`, and `artwork_url`. Artwork uses the song's large image when
  available, falling back to its regular image; creator avatars are excluded.
  Missing fields are `None`.
- `radio.ingestion.MusicIngestion(parser, metadata, catalog).import_text(text)`
  resolves references, groups unique IDs, checks the catalog before metadata
  retrieval, stores metadata and submissions, and records batch outcomes. It
  uses the Discord author (without trailing server tags) as the default artist
  display name. Exact aliases resolve existing identities; Suno usernames stay
  separate. Explicit artist associations and preferred names are preserved.
- The existing catalog now exposes `get_song_by_url(original_suno_url)` and
  indexes original URLs. Reimporting a known original share URL skips both
  resolution and metadata retrieval, including after service recreation.
- `radio.web.create_app(controller, ingestion)` exposes `POST /api/import` as a
  thin adapter with existing local request protection. Results include the
  `batch_id`, added/existing/failed `counts`, and per-entry warnings/errors.
- `main.py` composes the services; SQL remains entirely inside the catalog.

Only the standard library and existing Flask dependency are used.

The [catalog review and artwork follow-up](catalog-review-artwork.md) adds the
saved failed-import list, field highlighting, original-image caching, and the
completed artwork backfill for Kern's existing 79 songs. New imports download
available artwork through the composed `CatalogArtwork`/`ArtworkService`
capabilities. Image failure adds a warning without discarding the imported song.

## Supported source structure

The parser follows the supplied posts in `tests/fixtures/crucible_posts.txt`
and `tests/fixtures/crucible_dated_posts.txt`. It handles both the flattened and
multiline examples:

- Author headers such as `Jїпж [TCBL], Role icon, The Forged — 10:01 AM` and
  undecorated `GemNoodles — 11:14 AM`.
- Inline labels in different orders: Title, Song Link, Genre, About, About the
  song, Content Warning, and Content Warnings.
- Suno embeds containing `Suno <title> Listen and make your own on Suno. <title>`.
  The repeated preview separates posts but does not overwrite submitted metadata.
- Both canonical `/song/<UUID>` URLs and short `/s/<code>` links.
- `Yesterday at 7:51 PM`, explicit `11/5/26, 1:51 PM`, the `Link:` label,
  `C/W:`, blank paragraphs inside a post, and the supplied partial Suno preview.
- Timestamped author headers are split **before** examining Suno previews. A
  partial, missing, or differently truncated preview cannot join adjacent posts.
  The observed `SeraphymRole icon, ...` badge concatenation, `Song Title:`,
  `Song Description:`, `About This Song:`, and `CONTENT WARNING ⚠️:` are handled.

Displayed Discord names are preserved as supplied; no immutable Discord user
ID can be recovered from copied text. Role badges are not part of the author.
Unknown authors remain unknown with an explicit warning (persisted as an empty
string because the current submission schema requires a string). Unrecognized
text is retained for review. Multiple unseparated field sets are unresolved
rather than assigning one post's metadata to another. Other Discord export
formats have not been implemented or claimed as supported.

## Retrieval and persistence behavior

Retrieval reads the public page's observed Next.js flight data as JSON,
including byte-length-prefixed text references used for longer lyrics. It
matches the exact song ID before extracting title, `metadata.prompt`,
`metadata.tags`, and creator handle. It never stores an unresolved flight
reference as metadata. No JavaScript is evaluated. No private API, artist
catalog, search, audio URL, or related-song link is followed.

Lyrics are the public prompt/lyrics text verbatim; they may include section
labels or instrumental instructions. Remote titles, styles, and creator handles
are not normalized. Discord titles, genre, About, warnings, author, and original
message are stored separately. For example, the submitted `Mirelle's Lullaby`
and Suno's `Mireille’s Lullaby` both survive without rewriting either value.
For new or previously unassociated songs, a recognized Discord author supplies
the default artist. For example, `Jїпж [TCBL]` defaults to `Jїпж`, while the
original tagged attribution remains in the submission and artist aliases.
`The Salt Scribe | Beat VS Break [TCBL]` becomes
`The Salt Scribe | Beat VS Break`; Unicode and the rest of the name are preserved.
An existing artist choice or customized preferred alias is not overwritten.

### Copied post dates

The application reads the machine's current local time **once per paste**, only
to interpret the copied Discord timestamp. This reference remains in memory;
no ingestion-time field has been added. What is saved is the post's time:

| Copied timestamp | Interpretation |
| --- | --- |
| `6:59 AM` or `Today at 6:59 AM` | That time on the current local calendar date |
| `Yesterday at 7:51 PM` | That time on the previous local calendar date |
| `11/5/26, 1:51 PM` | November 5, 2026 at 1:51 PM |

`discord_submissions.discord_timestamp` retains the copied timestamp text, and
`posted_at` stores its resolved ISO 8601 timestamp with a timezone offset. Calendar
subtraction handles month/year boundaries; the offset is appropriate to the post
date. Invalid dates remain unresolved with a warning, not an invented timestamp.

Existing databases acquire these two nullable columns automatically and
transactionally. Previously stored submissions remain intact with unknown post
dates; migration does not reinterpret historical relative timestamps using
today's date. Reimporting an unassociated song can supply its default artist
without fetching its Suno metadata again.

Requests are sequential, spaced at least half a second apart, with a 15-second
network timeout and a 4 MB page limit. Redirects are restricted to Suno. HTTP
errors, rate limits, changed page structure, and missing songs produce clear
per-entry feedback. There are no automatic retries. Failed entries can be
resubmitted; existing songs cause no additional metadata retrieval.

One batch runs at a time. Imports are synchronous and the page shows progress
while awaiting the response. One song's lookup failure does not discard other
successful songs. Partial storage errors are reported explicitly; this is not
a durable job queue or a transaction spanning an entire network batch. Raw batch
text remains available even when a short link cannot be resolved to an ID.

## Tests and live demonstration

Offline tests and live checks are separate:

```sh
uv run python -m unittest discover -v
uv run python -m tests.suno_live_check tests/fixtures/crucible_posts.txt
```

Offline tests use the actual supplied text plus synthetic edge fixtures,
mocked Suno retrieval for orchestration, and real isolated SQLite databases.
They cover exact author/field extraction, Unicode preservation, share-link
resolution, page text references, missing metadata, clear network errors,
deduplication, persistence, attribution separation, and the HTTP adapter.
The full offline suite passes: **77 tests**. Suno requests are mocked in offline
orchestration tests; the live demonstration below uses real network retrieval.

The opt-in live check runs HTTP requests through Flask, actual targeted Suno
resolution/retrieval, and SQLite in a temporary database. It reimports the batch
and reopens the database before deleting the temporary directory. It does not
launch Codex or change existing authentication or the user's catalog.

The actual four supplied posts were imported successfully:

| Internal ID | Suno song ID | Stored title | Discord author | Suno handle | Lyrics characters |
| --- | --- | --- | --- | --- | --- |
| 1 | f674159d-5e1b-4dc9-89aa-8e9c51712fb9 | I Went To The Pictures Tomorrow | Sickseven [TCBL] | sickseven | 1248 |
| 2 | 462fdb48-3d39-4d1d-819e-80cfe3326a6b | Whistle - with CrimsonBooty | Jїпж [TCBL] | jjprecords | 2198 |
| 3 | ba4a55b9-268a-4098-b1d3-965efd6edfa8 | The Last Sucker | SicoAnimal [BF] | sicoanimal | 3643 |
| 4 | 0746f5e7-4d09-4d6b-9129-733c341f23eb | Mireille’s Lullaby | GemNoodles | gemnoodles | 594 |

The first two remote titles have trailing spaces, preserved in SQLite and the
JSON evidence. All four songs supplied title, lyrics/prompt, style, and creator
handle; **no requested Suno metadata was unavailable** in this demonstration.

Extracted submitted fields:

| Author | Submitted title | Genre | Content warning |
| --- | --- | --- | --- |
| Sickseven [TCBL] | I Went To The Pictures Tomorrow | EDM+ | nonsense |
| Jїпж [TCBL] | Whistle - with @ℭ𝔯𝔦𝔪𝔰𝔬𝔫𝔟𝔬𝔬𝔱𝔶 | dark psytrance, uk drill | mild violent and drug imagery |
| SicoAnimal [BF] | The Last Sucker | Horrorcore rap | None |
| GemNoodles | Mirelle's Lullaby | Ballad, Folk | None. |

Every About field and original message was also retained. Full descriptions,
styles, original URLs, extracted fields, stored records, and batch summaries
are in `ingestion-live-result.json`; lyrics text is omitted from this report
but its stored character count is recorded.

First import: **4 added, 0 existing, 0 failed**, with four submission records.
Reimport: **0 added, 4 existing, 0 failed**, with four more submission records
and a second batch, but no new songs or network operations. The first import
used three HEAD resolution operations and four metadata GET operations; HEAD
requests followed Suno's own redirects. Reopened SQLite retained the four songs,
eight submissions, and two completed batches.

The management form and import endpoint were served over real local HTTP in
this check; JavaScript syntax was checked with Node. A graphical browser
interaction was not exercised. Port 5000 was already occupied by an existing
application, so the isolated HTTP check used an automatically assigned port
without stopping that process. There are no current ingestion blockers for the
supplied source format; future unfamiliar formats will be reported for review.

### Follow-up verification: dated examples and artist defaults

Both newly supplied examples also passed the real HTTP/Suno/SQLite check:

```sh
uv run python -m tests.suno_live_check tests/fixtures/crucible_dated_posts.txt
```

Using the actual local November 8, 2026 reference, the stored results were:

| Preferred artist | Song title from Suno | Stored post time |
| --- | --- | --- |
| The Salt Scribe \| Beat VS Break | .beat - "delicate" | 2026-11-07T19:51:00-07:00 |
| Snotegel | From Inside the Data Stream | 2026-11-05T13:51:00-07:00 |

First import added two songs and two artists; reimport found two existing songs
and performed no additional network operations. Suno did not expose a style
value for `From Inside the Data Stream`; it remained unavailable while the
submitted `Psychedelic Deep House` genre stayed in the Discord submission.
Evidence is in `ingestion-dated-live-result.json`. The earlier four-song report
above predates the default-artist change; this follow-up records the new behavior.

Additional offline coverage verifies a single clock read per paste, local date
resolution, midnight/noon, year boundaries, daylight-saving offsets, old-schema
migration, preferred-name preservation, and persistence of post timestamps.

## Agent access to unresolved import results

Import results displayed in the conversation are structured browser cards, not
agent-authored messages. Previously, the agent could read the catalog but had no
capability for reading failed imports. A failed entry may not have a catalog song
or even a resolved Suno ID, so catalog queries cannot recover these results.

The agent now receives the latest persisted batch ID, completion state, and
added/existing/failed counts with each conversation turn. This also updates an
already-open Codex thread when music is imported between messages. Original
copied text is not automatically placed in every message. The user's message
remains unchanged in the browser conversation and human approval commands are
checked before application context is attached.

The read-only `station_ingestion` capability provides:

- `action: batches`: newest-first batch summaries with counts, without raw text.
- `action: items`: paged outcomes; defaults to failed entries of the latest batch.
  Supply `batch_id` for an earlier import and `outcome` for added, existing,
  failed, or all entries. Results include stable entry IDs, exact failure
  reasons, a 500-character source excerpt, and original source length.
- `action: item`: original copied text by `item_id`, with `source_offset` and
  `source_limit` for character paging. The limit is at most 4,000 characters.

List pages contain at most 20 records. Source text is treated as untrusted
evidence, not an instruction. The agent distinguishes failed imports from
unresolved artist identities. It can inspect and discuss existing results after
an application restart without asking Kern to paste them again. These operations
do not retry imports, repair records, change the parser, or grant policy approval.

Public service interfaces:

- `MusicCatalogService.list_batch_summaries(batch_id=None, limit=100, offset=0)`
- `MusicCatalogService.list_batch_items(batch_id, outcome=None, limit=100, offset=0)`
- `MusicCatalogService.get_batch_item(item_id)`
- `StationInformationService.latest_import()`, `ingestion_batches(...)`,
  `ingestion_items(...)`, and `ingestion_item(...)` expose scoped read responses.
- `StationTools.conversation_context()` supplies fresh application facts to the
  existing conversation controller. SQL remains within the catalog service.

No schema changes or additional dependencies are required. The saved import card
shows its batch number so it can be referenced explicitly in conversation.
Restart the application to load these new tools; the existing saved batch is
immediately readable without reimporting it.

Verification for this fix:

```sh
uv run python -m unittest discover -v
uv run python -m tests.ingestion_context_live_check
```

The offline suite passes **156 tests**, including eight new tests for all 19
failures without song IDs, pagination, exact Unicode source text, validation,
restart persistence, and HTTP imports becoming visible in an existing thread.
The conversation transport is mocked in those tests; persistence and the parser
use real isolated services.

The separate real Codex evaluation used a read-only snapshot of Kern's actual
batch: **79 added, 0 existing, 19 failed**. The agent opened the first two complete
original entries in an already-open conversation, identified merged Discord
posts, and distinguished import failures from artist-assignment review. A fresh
application/Codex session then retrieved all 19 failure records and the first
original entry. Existing station data and original authentication were not
modified. Results: `ingestion-context-live-result.json`.

A real Chrome/Flask/Codex check then pasted the same original batch into an
isolated copy of the catalog: **0 added, 79 existing, 19 failed**, without
refetching the existing songs. The browser card showed batch #2. Sending Kern's
original question through the composer produced an answer identifying the first
merged pair of posts, without asking her to paste the results. At 1366×900,
there were no browser runtime errors or horizontal overflow. The agent returned
a Markdown table, which the existing lightweight message renderer displays as
plain text. Evidence: `ingestion-context-browser-result.json` and
`screenshots/ingestion-agent-context-1366.png`.

## Follow-up: grouped posts and current resolution status

Kern's original 19 grouped failures contain **38 Suno submissions**. The parser
now recognizes their author/timestamp boundaries directly, preserving each
post's title, genre, description, warning, attribution, date, and original text.
Two groups also contain YouTube posts, which are now accepted with their supplied
metadata (see the follow-up below). Repeated fields without a reliable post boundary are still
unresolved; titles are never used to merge or deduplicate songs.

The review window now derives **current** resolution separately from historical
batch outcomes. A group leaves Needs attention only when every post can be
recognized and every referenced Suno song is in the catalog. Canonical IDs match
exactly; short links must match a known original catalog URL. This check performs
no network requests or writes and makes no assumptions based on similar names.
Manual separate imports count too, including after restarting the application.
Unknown links or non-Suno posts keep the group pending. No historical outcome,
source message, or timestamp is rewritten and no additional schema is needed.

`radio.catalog_review.import_entry_state(...)` returns catalog coverage facts;
`pending_import_review(...)` supplies the same filtered, paged queue to the UI
and the read-only `station_ingestion` action `unresolved`. Tool actions `batches`
and `items` retain their historical meanings. Agent context explicitly identifies
saved failed counts as historical so it can retrieve the current review queue.

Verification: **183 offline tests passed**, including eleven additional parser
and current-resolution tests. They cover actual grouped source material,
per-post attribution, exact original Unicode/whitespace, missing previews,
multiline descriptions, emoji warnings, partial imports, exact ID matching,
non-Suno posts, pagination, and persistence. Offline retrieval is mocked.

The separate real Suno check imported the original intact first group into an
isolated database: **2 added, 0 existing, 0 failed**, with artists `Seraphym`
and `norioku2020` and both artwork files. Its current review count changed from
1 to 0 while the historical failed count stayed 1, including after restart.
Actual Chrome/Flask verification repeated this through Prepare corrected import
without editing or manually splitting the text; review remained clear after
refresh. Original station data was opened read-only and not reimported or changed.

Evidence: `grouped-posts-live-result.json`, `grouped-posts-browser-result.json`,
and `screenshots/grouped-posts-resolved-1366.png`.

```sh
uv run python -m tests.grouped_posts_live_check
```

Restart the application to load the fix. Groups already imported manually leave
the review queue automatically when exact catalog coverage can be verified.
Remaining supported groups can be imported together using the existing form.

## Role badge cleanup and YouTube submissions

`Role icon, ...` and its concatenated variant are copied Discord decoration,
never part of an author name. Matching is case-insensitive and removes the marker
and everything after it. The existing artist records for **Seraphym**, **Nós**,
and **Warren Veil** and their aliases/Discord attribution were cleaned, preserving
artist IDs, song associations, and original copied message text. Future imports
apply the same cleanup before resolving artist identities.

YouTube video links (`youtu.be/<id>` and `youtube.com/watch?v=<id>`) are accepted
using their own video IDs. They retain any supplied title, genre, description,
warning, Discord attribution, copied message, and post date. A link-only post is
valid. No Suno lookup, Suno ID, lyrics, artwork, creator username, or metadata
completion is required. No YouTube API, crawling, downloads, or playback were
added. Missing optional fields remain unavailable and are not marked as errors.

`MusicCatalogService` provides `get_or_create_youtube_track`, `get_youtube_track`,
`list_youtube_tracks`, `store_youtube_submission`, and `list_youtube_submissions`.
YouTube tracks and repeated Discord submissions have separate SQLite tables;
deduplication uses the video ID, while every copied attribution is retained.
Batch items now have nullable `youtube_video_id` alongside `suno_song_id`, with
exactly one source reference for successful entries. Existing batch IDs, outcomes,
source text, and errors are retained by an atomic migration. Suno songs and their
playback statistics are unchanged.

The catalog review dialog includes **YouTube submissions** showing supplied data
and an Open on YouTube link, with no Suno field requirements. The agent can read
them with `station_catalog` actions `youtube` and `youtube_track`. Current review
coverage recognizes stored YouTube video IDs, so successfully accepted YouTube
posts no longer keep their old groups pending.

Both original YouTube posts were imported into Kern's catalog: **2 added,
0 existing, 0 failed**. Titles: **Don't Make Us Face This Sober Tonight** and
**I Just Miss You**. A database backup was made before cleanup/migration:
`catalog-before-youtube-role-cleanup-20261109T091901816688Z.sqlite3`, alongside the
catalog. Details are recorded in `youtube-role-cleanup-result.json`.

Only focused checks were run: **23 tests passed** for role-name variants,
persistent name cleanup, actual YouTube post extraction, partial/link-only data,
video-ID deduplication, persistence, old batch migration, review clearing, and
the HTTP retrieval path. YouTube-only ingestion asserts zero Suno requests.
The full repository suite was not rerun, as requested.

A targeted Chrome/Flask smoke check on an isolated copy of the actual catalog
showed both YouTube entries and their supplied details, with zero Suno
missing-field notices, JavaScript errors, or horizontal overflow. Account state
was stubbed; this check made no Codex or YouTube network calls. Evidence:
`youtube-browser-smoke-result.json` and `screenshots/youtube-submission-1366.png`.

```sh
uv run python -m unittest tests.test_youtube_ingestion tests.test_discord_text -q
```

### Retried the remaining saved groups

All 15 remaining groups from the original batch were run through the current
parser and ingestion pipeline using their saved original text. Actual targeted
Suno retrieval and artwork caching completed successfully:

- **30 songs added**, with **30 original artwork files saved**.
- **1 existing YouTube entry reused**, without duplication.
- **0 failed entries** and **0 groups remaining in the current review queue**.

A database backup was created first:
`catalog-before-retrying-imports-20261109T092848273359Z.sqlite3`.
Original batch outcomes remain historical; the current review queue is clear
because the referenced songs are now present. Per-group results are recorded in
`retried-imports-result.json`. No application code changes or full test-suite run
were needed for this retry. Refresh/reopen catalog review to see the updated state.

### Radio host model

Kern Radio now explicitly selects **GPT-6 Luna** (`gpt-6-luna`) when starting
host conversations. Previously it inherited the App Server default, confirmed
as GPT-6.1 Sol in this environment. `radio.controller.AGENT_MODEL` owns the
application choice; reusable `CodexService.start_conversation(model=None, ...)`
accepts explicit model selection while preserving default behavior for other
consumers. Global Codex settings and authentication are unchanged.

Three focused model-selection tests passed. A real App Server conversation
reported `gpt-6-luna` as the effective model and successfully read station tools,
correctly reporting 117 Suno songs and 2 YouTube tracks from an isolated catalog
snapshot. Evidence: `model-selection-live-result.json`. The original
authentication file was unchanged. Restart the running application to load the
model pin for new host conversations. Music parsing and metadata ingestion use
the existing Python services rather than an AI model.

## Live programming follow-up

The [radio programming milestone](radio-programming.md) now composes this catalog,
approved policies, GPT-6 Luna, the existing Suno player/history, and the approved
illustrated frontend. Start/Stop Programming are active. The agent retrieves
actual lyrics and scoped Discord descriptions to write short animated host
dialogue, maintains an upcoming queue, and accepts changes through backend chat.
Stop finishes the current song and signs off using actual playback end events.
No ingestion behavior or catalog metadata was rewritten for this milestone.
