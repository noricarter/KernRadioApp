# Operating policy discovery

The existing management chat now gives Codex scoped station information and
proposed-policy capabilities. Launch with `uv run main.py` and use the normal
chat. Restart an already-running application to load the new composition.
There is no questionnaire, policy-seeding routine, programming algorithm, playlist
generator, or autonomous broadcast action.

## Components and boundaries

- `services.policies.OperatingPolicyService`: reusable SQLite persistence for
  human-readable instructions. It has no Flask, Codex, catalog, or radio
  programming imports. No policy text is interpreted as executable logic.
- `services.station_information.StationInformationService`: scoped read-only
  catalog summaries, song details/lyrics pages, and artist representation. It
  composes the existing catalog, playback history, and metrics services.
- `services.codex.CodexService`: now supports official App Server dynamic function
  tools and returns their results while continuing the same agent turn. The
  transport reader queues server requests rather than blocking on their handlers.
- `radio.station_tools.StationTools`: application-owned capability schemas and
  dispatch. It supplies bounded catalog/metrics readers and proposal/revision
  writers, with no approval, disabling, raw SQL, filesystem, or playback tools.
- `radio.policy_discovery`: conversational instructions and the trusted chat-input
  boundary for human approval/disabling.
- `ApplicationController`: supplies the capabilities to Codex and applies actual
  user approval commands. `main.py` composes services over the existing catalog
  database and configuration. No frontend redesign or ingestion/audio changes
  were made.

The preexisting Codex implementation rejected all interactive tool requests; no
application tool-calling convention existed to reuse. This change uses the
[official Codex App Server dynamic tool protocol](https://developers.openai.com/codex/app-server):
`initialize` with `experimentalApi`, `thread/start.dynamicTools`, and server
`item/tool/call` requests answered with `contentItems` and `success`. Verified
against local Codex CLI 0.166.0 and its generated experimental schemas. This
protocol surface is experimental and may need adjustment for future CLI changes.

Only functions explicitly registered for that thread can reach its handler.
Other server interactions are rejected. Tool exceptions are returned as errors;
raw traces and secret configuration are not returned to the agent. Application
threads request a read-only sandbox, disable shell/apps/browser features and web
search, and instruct the agent to use only station capabilities. No additional
Python dependencies were added.

## Policy persistence public API

Construct `OperatingPolicyService(database)` using the existing `DatabaseService`.
Initialization is automatic/idempotent; existing catalog records are not changed.

| Operation | Interface |
| --- | --- |
| Propose | `create_proposal(subject, title, instruction, *, kind='default', rationale, basis)` |
| Read one | `get_policy(policy_id)`; absent IDs return `None` |
| Read page | `list_policies(*, limit=20, offset=0)` |
| Revise | `revise(policy_id, expected_revision, *, title, instruction, rationale, basis)` |
| Approve | `approve(policy_id, expected_revision)` — trusted application callers only |
| Disable | `disable(policy_id, expected_revision)` — trusted application callers only |
| Future consumers | `active_policies(*, kind=None)` — only enabled, approved instructions |

`kind` is `default` or `operational`. Special-program instructions are not a
persisted kind in this milestone. Defaults are ordinary preferences that can
yield to explicit temporary instructions; operational policies express reliability
requirements. These distinctions are labels/instructions, not evaluation rules.

A stable lowercase hyphenated `subject` key identifies a policy topic. An identical
proposal for that subject returns the existing policy. Different content for an
existing subject raises `PolicyConflict` and directs the caller to revise it.
An identical revision does not create another revision. Different subjects are
never automatically merged. Semantic duplicates using different subject keys
cannot be reliably detected by this simple service; the agent must inspect
existing subjects before proposing or revising.

Policy records include `id`, `subject`, `kind`, `enabled`, UTC `created_at` and
`modified_at`, `latest_revision`, `approved_revision`, and computed `active`.
Revisions contain exact title/instruction, rationale, motivating basis, approval
status and their creation timestamp. Thus the agent can explain the proposal
without inventing its justification later.

Proposing never approves. Revising appends a proposed revision under the same
policy ID. An approved older revision stays active until the proposed replacement
is explicitly approved. Approval supersedes only the previous approved revision;
disabling retains the approved text but makes it inactive. Stale revision commands
are rejected so an old approval cannot approve new text. There are no default
policy records created at startup.

## Human authority through the policy modal

Open **Policies** beside **Now Playing** to review saved proposals and active
policies. The modal shows the exact title, instruction, status, rationale and
basis. Kern can approve a proposal, decline it, or disable an active policy
there. Approval, decline and disable are application actions and stay out of the
conversation. **Discuss / Edit** returns to chat with the policy identified;
adding a policy also begins as a conversation. The exact commands remain
available for direct chat use when preferred. For example:

```text
Approve policy 1 revision 2
```

To disable that policy:

```text
Disable policy 1 revision 2
```

These exact commands are recognized only when they are the entire actual user
chat input. They are applied directly by the application. Agent output and tool
arguments are never passed through this authority path. No agent tool can
approve or disable a policy; an `approved` argument is rejected. Informal
agreement such as “yes” alone does not activate anything; use the modal button
or an explicit command.

Revisions can be requested in normal language. The agent saves a proposed
revision and asks Kern to approve its new revision number. A trusted human action
starts a fresh agent thread on the next message, ensuring saved approvals/status
are reread. Approved decisions and pending proposals survive process restarts;
free-form temporary discussion is not made into permanent policy. This milestone
does not restore arbitrary chat transcripts across restarts.

## Station reading and tool interfaces

Registered tools:

- `station_overview`: actual catalog size, represented/total artists, unassociated
  songs, the ten largest artist contributions, playback start/completion/
  interruption counts, and existing setup completion. It exposes no credential
  or arbitrary configuration values.
- `station_catalog`: `songs`, `song`, `artists`, or `artist`. Pages are limited to
  20 records (default 10). Song lists omit lyrics; song detail pages return at
  most 4000 lyrics characters with offsets/total length. Songs can be filtered by
  stable artist ID. Unicode names and source metadata are retained.
- `station_playback`: existing history, song statistics, artist statistics,
  rankings, airtime distribution, or style/genre representation. Existing metric
  semantics are unchanged, including UTC half-open start-time ranges and the
  difference between completed counts and interrupted airtime.
- `station_policies`: bounded policy summaries, one exact revision review, or
  currently active instructions. Proposed status and an older approved revision
  are presented separately.
- `propose_station_policy` and `revise_station_policy`: save proposals with exact
  rationale/basis and return human action instructions. Neither activates anything.

`StationInformationService` exposes `overview()`, `songs(artist_id=None,
limit=10, offset=0)`, `song(suno_song_id, lyrics_offset=0, lyrics_limit=3000)`,
and `artists(limit=10, offset=0)` as keyword-paged public interfaces independent
of the agent. The history/metrics objects it receives supply their existing
public APIs.

**Song durations are currently unavailable in persistent catalog metadata.**
Readers report `duration_seconds=None` and explain that playback airtime must not
be treated as a song-duration measurement. No duration field or new ingestion
behavior was invented for this task.

## Schema changes

Two policy-owned tables and one partial unique index are added to the same SQLite
file; no existing catalog/history table is modified:

- `operating_policies`: integer PK `id`; unique `subject`; checked `kind`;
  checked boolean `enabled`; UTC `created_at`, `modified_at`.
- `operating_policy_revisions`: composite PK `(policy_id, revision)`; FK to
  policy ID; `title`, `instruction`, `rationale`, `basis`; checked approval status
  `proposed`, `approved`, or `superseded`; UTC `created_at`.
- Unique approved-revision index on `policy_id` where approval status is
  `approved`, enforcing at most one approved revision per stable policy.

The small revision table preserves the approved instruction while a replacement
is proposed; it is not a rules engine or a collection of executable actions.

## Verification and conversational evaluation

Offline:

```sh
uv run python -m unittest tests.test_policies -v
uv run python -m unittest discover -q
```

The 16 new tests use isolated real SQLite files. They verify proposal/approval/
revision/disable behavior, unapproved exclusion, persistence, Unicode, subject
deduplication, optimistic revision checks, category separation, real catalog and
history/metric reading, lyrics paging, unknown approval-tool rejection, extra
approval-argument rejection, and the actual transport request/response exchange
with a fake protocol peer. Those protocol tests are offline, not real inference.
All 16 new tests passed; the full repository suite passed **133 tests**.

Opt-in real Codex evaluation:

```sh
uv run python -m tests.policy_live_check
```

The evaluation builds an isolated four-song catalog by reusing ingestion and
actual targeted Suno lookups for the supplied Crucible posts. It loads the three
real playback events previously verified in `suno-playback-live-result.json`.
There was no existing application database at the default path to evaluate.
Codex authentication is privately copied into a temporary test home; the original
file's SHA-256 was checked unchanged afterward. Policies/preferences stated by
these evaluation turns are explicitly test fixtures, not Kern's station defaults.
No user catalog, authentication or production policy records are modified.

Real results, using the same application controller and tools as the normal chat:

- Inspected four songs/four artists and correctly described their contrasting
  styles. Retrieved all three history events and distinguished one completed
  play from two interruptions.
- Asked one focused question about stylistic contrast versus consistency, using
  the actual small library. Did not present a questionnaire or propose policies
  before being asked.
- Acknowledged that exact combined duration was unknown; did not estimate it
  from recorded playback airtime.
- Saved exactly one requested test proposal, explaining its instruction and
  motivation. It remained unapproved/inactive.
- Kept an undecided “tonight only” idea out of persistent policies.
- Activated the policy only after the test user supplied the explicit approval
  command, through application code rather than a model tool.
- Recreated the controller and Codex process over the same database, retrieved
  the saved approval, and explained it without asking the same preference again.
- Revisited the policy without duplicating it. Proposed revision 2 under the
  same ID, correctly keeping revision 1 active until a new human approval.
- After explicit approval and disabling, correctly reported revision 2 as
  approved but disabled/inactive. No other policies were created.

Unexpected behavior was retained, not hidden or fixed by rewriting the prompt:
Codex first proposed a subject key with underscores; the service rejected it
and it retried with hyphens. In the initial evaluation it also requested 50
policy records when the maximum was 20, then corrected that request. Bounds and
the existing subject pattern were subsequently made explicit in tool schemas;
the extended run still needed one subject-key correction. Both runs recovered
without changing state incorrectly. The conversational prompt was unchanged.

Full transcripts, tool call arguments, resulting revisions and actual station
summary are in `policy-live-evaluation.json` (extended run) and
`policy-live-evaluation-initial.json` (initial run). Policy names/instructions
there belong only to the isolated evaluation and are not seeded into the app.
A visual browser chat test was not run; the controller and real Codex protocol
were evaluated directly, alongside existing HTTP regression tests.

## Future programming integration

A future application-level programming system should retrieve
`policies.active_policies(kind='default')` and operational policies as context
for its reasoning, while keeping any explicitly requested special broadcast
context separate. It should inspect the actual catalog/metrics through the
existing readers. Nothing in these services chooses tracks, evaluates a policy
as code, or enforces a default as an inflexible constraint.

The separately authorized [live programming milestone](radio-programming.md)
now supplies these approved policies to the existing agent during programming.
The policy service remains independent of track selection and playback, and
does not start programming itself.
