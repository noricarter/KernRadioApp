# Playback evidence, varied selections, and failure diagnostics

Restart with `uv run main.py` to load these changes. A running station process
does not hot-reload Python modules or its agent tool definitions. The change does
not stop or alter a currently running production show while files are edited.

## Playback evidence on every active turn

`StationInformationService.playback_context(song_ids=(), limit=20)` provides
bounded read-only evidence assembled from the existing catalog, history, and
metrics services:

- The newest 20 playback starts, including completed, interrupted and unfinished
  events, with actual titles, artist names, styles and UTC timestamps.
- Catalog size, total recorded starts and the number of songs never started.
- Up to 20 unplayed or least-recently-played candidates with full-history counts
  and last-played times. These are data for reasoning, not an automatically
  selected playlist or rotation score.
- Full-history statistics for the current and pending songs, including starts,
  completed plays, interrupted plays and last-played time.

`StationTools.conversation_context()` attaches this evidence on **every** agent
turn while programming is running. This covers the initial plan, automatic
refills, and Kern's chat requests on the existing shared thread. Counts refresh
from SQLite rather than relying on conversation memory. Large catalogs/history
remain accessible through paged tools; the sample is explicitly not the entire
library. Absence from a recent page does not mean a song has never played.

`PlaybackHistoryService.list_events(..., order='newest')` and the
`station_playback` history tool now support newest-first reads. The existing
default `order='oldest'` preserves chronological callers and date-range semantics.
Tied timestamps use event ID consistently in the chosen direction.

## Queue validation and replay requests

Kern requested different songs. Host instructions now require considering
playback evidence before selecting tracks and exploring beyond the first catalog
page. They retain her existing policy excluding artist-airtime balancing.

Evidence alone was insufficient in the first real evaluation: Luna selected
**Little Bad Miracle**, which appeared in the supplied recent history, and
incorrectly claimed it had zero recorded starts. That failure is preserved in
[the first evaluation](programming-variety-first-evaluation.json).

The application now also checks actual metrics when accepting a queue:

- `RadioProgramming(..., metrics=...)` receives the existing metrics service as
  an explicit dependency. Generic persistence/metrics services do not choose songs.
- `set_station_program` rejects duplicate song IDs in the pending queue.
- Both `set_station_program` and `queue_station_song` check full recorded history.
  A previously started song requires a nonempty `repeat_reason`, at most 360
  characters, explaining Kern's explicit replay request or why repetition is
  necessary for that program.
- A rejected choice returns its actual starts, completed count and last-start
  timestamp. The queue/revision remain unchanged; the agent can correct its
  selection. Interrupted or unfinished starts are not mislabeled as unheard.
- A reason accompanies the retained program state, so it can be inspected and
  discussed through `station_programming`. Explicit replay requests remain
  possible. This is not a fixed cooldown, automatic replacement algorithm,
  permanent policy approval, or a guarantee the agent's stated reason is sound.

No schema/statistic definitions were changed and no new operating policy was
approved or inserted by this work.

## Failure diagnostics and recovery

The cause of Kern's reported server failure remains **unconfirmed**. Its terminal
trace had scrolled away; there was no persisted application traceback to recover.
The restarted application was responsive during inspection. No matching kernel
OOM/segfault entry was found in the inspected time range; that does not establish
the cause or rule out a failure elsewhere.

`radio.diagnostics.start_diagnostics(path)` is composed in `main.py`. It saves
error tracebacks to `~/.config/kernradio/logs/errors.log` (under `XDG_CONFIG_HOME`
when overridden). Logs append across restarts and rotate at 1 MiB with two
backups. Routine successful web-request messages are suppressed; warnings and
errors remain visible. Application failures, Flask errors and uncaught background
thread exceptions are retained. It does not log chat bodies, song data, or RPC
traffic. This cannot capture an uncatchable process kill or power loss.

A separate verified recovery defect was corrected: `ApplicationController`
now clears the conversation thread after a failed Codex turn for both human and
internal programming requests. The next request can create a fresh thread rather
than repeatedly targeting the failed one. This does not establish that the
original server failure was a Codex error, and is not an automatic server restart.

## Verification

**68 focused tests passed**: playback metrics/history, programming lifecycle and
HTTP, policies, new selection context, error diagnostics, and actual application
startup. These use isolated SQLite files and mock Codex/audio where appropriate.
The main-composition check starts real Flask HTTP, forces an isolated web error,
verifies its saved traceback, and confirms subsequent status requests still work.

The separate [real Luna evaluation](programming-variety-live-result.json) used a
private snapshot of the actual catalog, history and approved policies, and a
private authentication copy. It used **fake audio callbacks**, not audible music:

1. Luna selected **Oh How I Love Halloween**, **We Are Humans, Not Gender Roles**,
   and **Nothing Stays Still**. All had zero starts in the snapshot.
2. The test inserted one isolated completed-history event for the pending
   **We Are Humans, Not Gender Roles**, simulating a newly heard selection.
3. A refill on the same real agent thread received updated history/counts,
   replaced that now-heard track with **The Ground Lets Go**, and kept the still
   unheard **Nothing Stays Still**.
4. All resulting pending choices had zero recorded starts. Original authentication
   was unchanged. Production catalog/history/policies were not modified.

Repeat the focused checks with:

```sh
uv run python -m unittest tests.test_programming_context tests.test_diagnostics tests.test_playback tests.test_programming tests.test_programming_web tests.test_policies tests.test_startup -q
uv run python -m tests.programming_variety_live_check
```

The original full-suite result is documented separately; this change was checked
with the focused set above rather than claiming another full-suite run.
