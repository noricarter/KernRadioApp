# Kern Radio management UI and onboarding

Launch from the project root:

```sh
uv run main.py
```

Open **http://127.0.0.1:5000**. Restart any previously running application to load
the new routes, templates, and composition. There is no separate application login.

## Design and components

The layout follows the supplied approved backend mockup: Kern Radio header,
centered programming controls, explorer mascot on the left, primary conversation
in the center, and gold-framed context panels on the right. Brown, gold, cream,
and green styling uses ordinary HTML/CSS and small JavaScript modules. No
framework, generated artwork, settings icon, navigation tabs, or decorative
subtitle was added.

The mascot is the exact existing `kern_radio_windowed/assets/mascot.png`, served
through a fixed asset route. The public radio frontend and original asset are
unchanged. Templates are split into header, conversation, context, and dialogs.
Frontend modules separate API/DOM helpers, conversation/cards, catalog editing,
and application state. Untrusted message/catalog text is rendered with DOM text
nodes; simple bold/code formatting does not permit injected HTML.

## Onboarding backed by actual state

`radio.management.StationManagement` composes existing services and calculates
onboarding state; Flask routes are adapters and contain no SQL or music parsing.
The guide remains visible until all prerequisites and final confirmation exist:

1. **Connection:** current Codex account verification reports authenticated.
   Existing official sign-in and reconnect actions are reused. Saved historical
   setup completion alone cannot claim the current connection is valid.
2. **Music:** at least one actual catalog song exists. Importing is available from
   the conversation's plus button and current-step action. The existing Discord
   parser, targeted Suno lookup and ingestion orchestration return actual results.
3. **Catalog review:** songs have artist associations, associated artists have
   preferred names, and Kern confirms reviewing them. A click cannot bypass
   missing records or unresolved identities. Unnamed/unascribed songs are marked
   for review in the catalog browser.
4. **Policies:** at least one enabled, approved default policy actually exists.
   Proposals and conversational claims do not satisfy this requirement.
5. **Completion:** the finish action validates all prerequisites before persisting
   `onboarding_completed` in the existing configuration service.

The configuration also records `catalog_reviewed`. Actual unresolved identities,
missing songs, an unavailable connection, or the removal of all active defaults
can return the guide to the relevant step. There is no hardcoded musical
preference or fixed policy questionnaire. The same Codex chat is available after
connection throughout the remaining steps and normal use.

## Catalog editing and actual station information

After setup, **Catalog Summary → Import music** opens the existing Discord-paste
import dialog. The labeled action remains available during normal operation,
alongside **Browse & edit music**. It uses the same import endpoint and results
cards as onboarding. The small + beside the chat input is also retained.

The catalog dialog supports paginated browsing and direct edits of the existing
song title, style, lyrics, Suno username, artist association, preferred artist
name, and aliases. Kern can select an existing artist or create a named identity
for an unassociated song. Name/alias changes apply to all that artist's songs;
Discord attribution stays separate. Alias ownership conflicts are rejected rather
than merging artists. If a typed preferred name matches another loaded artist's
name or alias, the editor offers **Use existing artist**. Kern must explicitly
select that action and save; the existing artist's name and aliases are kept.
Save feedback appears below the scrolling editor, beside the action controls.
Fresh song details replace cached artist details after saving or reselecting.

Existing `MusicCatalogService.update_artist` now accepts an optional `aliases`
list. Replacement, preferred-name update, conflict detection and preservation of
the preferred name as an alias occur in one transaction. Existing callers that
omit aliases retain their previous behavior. Song/artist management otherwise
composes existing catalog interfaces. A multi-record save is not a new global
transaction system; storage failures report that earlier changes may have saved.

The normal right panel shows:

- Actual shared playback-service state, or a clear idle message.
- Actual counts of catalog songs and artists.
- Selected-song details with an edit action.
- Read-only completed/interrupted counts, latest recorded play and airtime from
  the existing metrics service. Calculated fields cannot be submitted as metadata.

Song artwork is now persisted and displayed from the local cache in the catalog
editor, song list, current-song details, and actual Now Playing details. Missing
images are omitted. Per-song and total catalog duration remain unavailable;
albums, tags, notes, rotation settings and active-song toggles are not fabricated.
See [catalog review and artwork](catalog-review-artwork.md) for the failed-import
review section, field highlights, service interfaces, and latest verification.

`main.py` owns an instance of the existing `SunoPlaybackService`, without starting
playback, and injects it into management for status reading. Only an actual
`playing` state yields Playing; starting/buffering have their own labels. Old
unfinished history is never treated as live playback. A standalone CLI player is
another process and is not automatically discoverable by this in-process status
adapter. No new audio controls or playback logic were added.

## Policy modal and human authority

The **Policies** button beside Now Playing opens a dedicated modal showing each
saved policy's exact title, instruction, status, rationale and basis. Proposed
policies can be approved or declined there; active policies can be disabled.
**Discuss / Edit** closes the modal and prepares a message in the existing chat.
The modal's add button does the same for a new policy discussion. New proposals
and revisions are still written by the host after discussion, then appear in the
modal for a human decision. Policy cards and action acknowledgements do not add
noise to the conversation.

Approval, decline and disable buttons call `ApplicationController.policy_action`,
which invokes the same `apply_human_command` authority boundary used by explicit
chat commands and clears stale agent context. No approval or decline tool was
added to the agent.
All mutation routes retain the existing per-process browser token and same-origin
checks. Stale revision actions are rejected.

A small `OperatingPolicyService.decline(policy_id, expected_revision)` capability
was needed for actual proposal rejection. Existing revision tables gain a
checked `declined` flag defaulting to
zero, through an idempotent transaction. Declining a draft does **not** disable an
earlier approved revision. The declined revision cannot be approved until a new
revision is proposed. A reconsidered identical instruction creates a new revision
rather than silently reopening the declined one. Agent policy summaries now
include the declined flag so persisted status remains visible in later discussion.

## HTTP application interface

Existing `/api/status`, `/api/connect`, `/api/messages`, and `/api/import` remain
in use. Status adds a `station` section containing grounded onboarding/context
state. Management adds:

- `GET /api/catalog?offset=…` and `GET /api/artists?offset=…`.
- `GET /api/catalog/<suno_song_id>` for actual metadata, artist and statistics.
- `POST /api/catalog/<suno_song_id>` for supported metadata/identity edits.
- `POST /api/catalog-review` and `POST /api/onboarding/complete` with validated
  prerequisites.
- `POST /api/policies/action` for a human action, policy ID and exact revision.

## Live programming integration

**Start Programming** and **Stop Programming** are now connected to the separately
authorized programming controller. Start becomes available when actual setup
prerequisites are satisfied, prepares an agent-created queue, and opens the public
station frontend. Stop requests that the current song finish, then the host signs
off. The button state and station badge come from real runtime state. Selection,
scripts, and audio lifecycle remain outside Flask routes and templates.

See [live programming](radio-programming.md) for interfaces, actual Luna/Suno
verification, current limitations, and screenshots.

## Verification performed

All **148 repository tests passed**, including 15 new management tests against
isolated real SQLite. Coverage includes onboarding gates, existing database
migration, persisted editing and alias removal, alias rollback on conflict,
read-only metric protection, stale/unauthorized policy actions, decline preserving
old approval, persistence after reopening, and honest idle/playback state.
Existing service, Codex, ingestion, metrics and playback regressions remain green.

Actual Chrome browser renders and interactions were exercised against the real
Flask application using the dependency-free DevTools driver in
`tests/browser_driver.mjs`. No JavaScript framework or test dependency was added.
Two isolated server configurations were used:

- A disconnected Codex stub for the fresh connection/empty-catalog view. This is
  a mocked authentication state, not a claim of completing a new OAuth login.
- A real Codex App Server using a private temporary copy of existing valid auth,
  plus real Suno retrieval and real isolated database services for the workflow.

The browser workflow verified actual authenticated status, ingestion of the four
supplied posts, result cards, persisted song/artist/alias edits after refresh,
a real agent's unapproved proposal, human button approval, onboarding completion,
and actual idle/catalog/statistics panels. After stopping and restarting the
application server against the same test database, browser checks verified
persisted onboarding and approval, the saved metadata, and Save remaining visible
outside the editor's scroll area. Discuss/Edit used the existing composer; a real
agent proposed a replacement, and the Decline button preserved the older active
policy. No programming was started.

Renders were captured and inspected at **1680×941, 1366×900, and 1024×768** desktop
viewports. The checks found no horizontal overflow, kept the composer in the
window, loaded the approved mascot, and reported zero browser JavaScript
exceptions. At short/narrow desktop sizes the conversation and context scroll
independently, so longer details remain reachable without overlapping controls.
Inspection prompted fixes for mascot-edge cropping, persistent Save-button
placement, and transient feedback covering the composer. Final renders were
recaptured after these corrections.

Evidence:

- [Fresh connection view](screenshots/onboarding-1366.png)
- [Connected onboarding](screenshots/onboarding-connected-1680.png)
- [Import results](screenshots/import-results-1366.png)
- [Metadata editor](screenshots/catalog-editor-1366.png)
- [Real proposed revision](screenshots/policy-revision-1366.png)
- [Declined replacement](screenshots/policy-declined-1366.png)
- [Station at 1680](screenshots/station-final-1680.png)
- [Station at 1366](screenshots/station-final-1366.png)
- [Station at 1024](screenshots/station-final-1024.png)
- [Workflow browser assertions](ui-browser-workflow.json)
- [Restart/decline browser assertions](ui-browser-restart.json)

Test titles, aliases and policy preferences in these screenshots are isolated
verification fixtures, not changes to Kern's real catalog or intended defaults.
No user data or original authentication files were written. Fresh interactive
OAuth completion was not performed; verified existing Codex authentication was
reused. No actual audio was started by this UI test because programming controls
must remain inactive. Browser authentication and backend audio retain their
previous integration tests.

Chat messages retain the existing process-session lifetime; catalog, policy,
review and onboarding state persist across restarts. No new chat persistence
system was added. There are no blockers for the implemented milestone.
