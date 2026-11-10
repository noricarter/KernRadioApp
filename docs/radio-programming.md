# Live radio programming and host dialogue

For connection-drop handling, reload position recovery and saved playback errors,
see [browser audio recovery](audio-recovery.md).

Launch as usual:

```sh
uv run main.py
```

Open `http://127.0.0.1:5000`. After connection, catalog review, and approved
defaults are in place, **Start Programming** prepares a real program and opens
the approved illustrated frontend in a normal Chrome/Chromium window with tabs
and an address bar, suitable for selecting in Discord sharing. No launch
flags or additional dependencies are needed beyond the existing playback setup.
Restart a previously running process to load this milestone.

The host greets listeners in an original, cheerful Saturday-morning-cartoon
explorer voice, introduces each song using actual catalog/content information,
and displays a brief outro after the song ends. Songs play through the existing
Suno audio service. The host's speech is animated **text**, with no TTS/voice
generation. Artwork, titles, preferred artist names, and upcoming tracks come
from actual records and the agent's submitted queue; demo tracks are removed.

**Stop Programming** clears pending tracks and requests a graceful finish. It
does not interrupt the current song. The actual audio end event triggers its
outro and the host's thank-you/sign-off; no next song is started. Stopping during
planning or an introduction signs off without starting an unplayed song. Closing
the application is a separate shutdown operation and stops owned audio/resources.

Continue using the management conversation during programming. For example:

- “Play [song] next and keep this song playing.”
- “Take the next few tracks in a different musical direction.”
- “Rewrite that visible host line to say …”
- “Change the current song's outro,” or “Use this sign-off tonight …”

The agent inspects real data, changes the pending queue/scripts, and confirms the
result. An explicitly requested immediate interruption is supported separately;
ordinary next-song requests preserve current playback. Temporary requests stay
in the running conversation/program, rather than becoming permanent policies.

## Component boundaries

| Component | Responsibility and public interface |
| --- | --- |
| `services.dialogue.DialogueService` | Independent timed text pagination: `show(text)`, `advance()`, `snapshot()`. No radio, Flask, or AI dependency. |
| `radio.programming.RadioProgramming` | Application composition/state machine: `start()`, `stop()`, `status()`, `public_state()`, `set_program(...)`, `queue_song(...)`, `say(text)`, `rewrite_script(...)`, `close()`. Receives catalog, playback, dialogue, metrics, agent-request callable, and frontend-open callable. |
| `radio.programming.prompts` | On-air style, grounding, policy use, and internal planning requests. The agent makes musical choices and writes scripts. |
| `ApplicationController.station_task(text)` | Sends internal orchestration turns through the **same** GPT-6 Luna thread, tool dispatcher, and serial executor as human chat. Returns a future or `None` while another turn is busy; internal prompts are not fabricated human messages in the UI. |
| `StationTools` | Bounded agent interfaces for current program inspection, queue changes, and dialogue edits. Existing catalog, metrics and policy tools are reused. |
| `BrowserAudioBackend` | Reused HTML Audio/file/event bridge. Optional `set_display_url(url)` and `open_display()` host the approved frontend around that bridge; audio preparation/history semantics are unchanged. |
| `StationManagement` / Flask | Validate startup prerequisites, present actual state, and adapt Start/Stop HTTP requests. Routes contain no selection, script generation, SQL, or music processing. |
| `kern_radio_windowed` | Existing illustrated assets/layout, live queue/artwork/status, and bounded text animation. It renders backend state and has no autonomous selection or playback controls. |

No database tables, programming rule engine, scoring system, new AI process, or
infrastructure dependency were added. Queue and current-program state are in
memory; a restart begins off air. Catalog, approved policies, and actual playback
history retain their existing persistence.

The [playback evidence and variety follow-up](programming-variety.md) documents
fresh history/counts on every active planning turn, checked replay reasons, and
saved failure diagnostics. Queue tools accept optional `repeat_reason`, required
for a song with recorded starts. Duplicate pending IDs are rejected.

## Agent interfaces and policy authority

`StationTools(..., programming=...)` registers these capabilities only when a
programming controller is actually composed:

- `station_programming`: current phase, revision, current song, pending tracks
  and scripts, last played song, stop request, and failures.
- `set_station_program(expected_revision, tracks, greeting?, signoff?)`: replace
  1–6 upcoming tracks, each containing a real `suno_song_id`, introduction and
  outro. The first program requires greeting and signoff. The current song stays
  separate from the pending queue.
- `queue_station_song(expected_revision, suno_song_id, introduction, outro,
  position='next', interrupt_current=False)`: put a requested song next or append
  it to the upcoming queue. Interruption is reserved
  for Kern's explicit immediate-play instruction.
- `say_on_air(text)`: rewrite the currently visible host message during a run.
- `revise_host_script(expected_revision, target, text)`: rewrite `current_outro`
  or `signoff`, including closing text while a Stop request is draining a song.
- `set_host_mascot(mascot)`: change only the public host image, including an
  off-air preview. This leaves audio, dialogue, queue and policy approval alone.

Revision checks reject stale queue/script edits. Draining runs reject refills.
Tools validate catalog references and script sizes; none can approve a policy or
start an idle station. The existing human policy-approval boundary is preserved.

The agent retrieves active approved policies and relevant song details, including
paged lyrics and a scoped summary of copied Discord submissions. Current saved
policies grant discretion over musical flow and explicitly exclude artist
exposure/airtime balancing as a selection criterion. No selection preference is
hardcoded into the queue driver. The six-track bound and low-queue refill trigger
are runtime buffering limits, not a rotation algorithm.

## Host mascot expressions

The approved variants are `standard`, `cute`, `headphones`, `mischief`,
`surprise`, `sad`, `consider`, `explain`, and `excited`. The public window uses
`mascot_standard.png` initially, at the start of each run, and after sign-off.
These are the supplied images; none were generated or edited by this change.

The agent can set `greeting_mascot` / `signoff_mascot` in `set_station_program`,
and `introduction_mascot`, `playing_mascot`, and `outro_mascot` on each track in
`set_station_program` or `queue_station_song`. Omitted track expressions use
standard. Expression changes follow the actual stage of the existing playback
lifecycle, without another AI request for every change. Refills preserve the
greeting/signoff expressions when those options are omitted.

`say_on_air` and `revise_host_script` also accept optional `mascot`. A direct
`set_host_mascot` switch stays visible until another image command or the next
scripted stage. For example, ask in management chat: “Show the headphones mascot”
or “Use the excited expression for this song's outro.” Restart the application
after installing the change so the agent thread receives the new tool definitions.

Only the listed names are accepted; file paths and external image URLs are
rejected. `/api/broadcast` reports `mascot` and `available_mascots`. The public
frontend loads only the requested image, keeps the previous image until it is
ready, and ignores stale load completions. Invalid display state or a failed
variant load falls back to standard. All variants occupy the same 390 × 508
design-space box with proportional containment and a bottom anchor. Different
PNG canvas dimensions therefore do not move the surrounding interface. No
mascot settings or database columns were added.

Mascot verification: 21 focused dialogue/programming/HTTP tests passed, including
scripted stage transitions, a live-image-only tool change, invalid names, and
completed history during graceful Stop. The browser check loads all nine assets
at three desktop sizes (27 renders), checks stable bounds without the existing
decorative bob animation, refreshes from the real Flask endpoint, and exercises
the real Luna tool. This check uses an isolated empty catalog and private auth
copy; it does not play audio or change production data.

- [Browser check results](mascot-browser-result.json)
- [Agent tool results](mascot-agent-result.json)
- [Headphones render](screenshots/mascot-headphones-1680.png)

To repeat: launch `uv run python -m tests.mascot_live_server`, then run
`node tests/mascot_browser_check.mjs` with its printed URL. Stop the isolated
server with Ctrl+C afterward.

## Frontend transport, pacing, and lifecycle

- `GET /listen/` serves the approved public composition/assets.
- `GET /api/broadcast` supplies current track, pending queue, actual playback
  state/position, dialogue page/timing, and station phase.
- `POST /api/programming/start` and `/stop` retain the existing local request
  token and same-origin protection. Start validates actual setup prerequisites;
  the user's Start action also completes the existing final onboarding flag.
- `/api/status` includes real programming readiness and runtime state for the
  management buttons, badge, and contextual panels.

The public frontend polls once a second and re-renders only changed content.
Text uses one short `requestAnimationFrame` sequence per page, not a timer for
every character. Reading/reveal intervals are owned by the dialogue service.
Scripts aim for 180 characters and are limited to 360; the reusable display
service splits pages at 180 characters. Unicode is retained, long lines wrap,
and text size adapts to the existing box. Reduced-motion preferences reveal the
whole page immediately while preserving its reading interval.

Only the owned station window embeds the existing secret-path local audio
bridge. It uses the already-established Chrome autoplay/HTML Audio method.
Opening an additional `/listen/` window displays the program without starting a
second audio player. Song completion is always based on `ended`, never a catalog
duration or assumed timer. History therefore continues to distinguish completed
and interrupted playback accurately.

The service skips a failed track and reports the failure. Three consecutive audio
startup/playback failures stop the run rather than creating an endless retry
loop. Agent preparation failures are surfaced; a missing submitted plan is not
treated as successful programming. Browser closure remains an actual playback
failure. Starting again can reopen the owned display.

## Verification

The complete repository suite passed **209 tests**. Seventeen new dialogue,
programming-lifecycle, HTTP, and shared-agent tests use real isolated SQLite and
fake agent/audio callbacks. They cover natural transitions, graceful Stop,
Stop during planning/introduction, immediate interruption, next-song edits,
current outro/dialogue edits, stale revisions, repeated Start, failures, approval
boundaries, and reuse of the existing conversation thread. After the final
sign-off/rendering adjustments, focused programming checks were rerun and passed.
The full run initially found one outdated schema-list assertion from YouTube
support; it was corrected and the full rerun passed.

The separate live browser run used **GPT-6 Luna**, the normal Flask endpoints,
a private copy of the actual catalog/policies/config/auth, real Suno resolution,
and real desktop audio output. It verified:

1. The same agent thread read the catalog, lyrics/descriptions, artist identities,
   and active policies, then generated greeting/introduction/outro/signoff scripts.
2. Start opened the illustrated player and played **Mireille’s Lullaby** by
   **GemNoodles**. Desktop audio was active and unmuted; playback position advanced.
3. During music, a real backend chat turn confirmed the requested next track and
   changed the on-air text exactly, without interrupting audio or creating policy.
4. The song reached its natural **148.12-second** end; its outro and the next
   introduction appeared before **I Went To The Pictures Tomorrow** by
   **Sickseven** began.
5. Stop was clicked during that second song. It continued to its natural
   **139.4-second** end, followed by its outro and the generated thank-you/signoff.
   Both history events were **completed**. No third song or restart followed.
6. Luna independently prepared additional upcoming songs from real content while
   audio played; those songs were not played after Stop.

A separate real-Luna tool check used **fake audio callbacks**, clearly recorded
as a tool test rather than an audio test. It changed an actual pending queue
from Rewind to the requested song followed by Rewind, and rewrote the current
outro exactly, without calling audio Stop. This exercised `queue_station_song`
and `revise_host_script` with real model-generated tool calls.

Actual renders were inspected at **1366×900, 1024×768, and 1680×941**, with no
JavaScript exceptions or dialogue/document overflow. An additional 30 render
checks replayed all recorded dialogue plus maximum-length wide Latin/Unicode
pages at those sizes. The final sign-off view was replayed from recorded real
state after fixing a display issue that could show an unplayed upcoming track.
It now keeps the last actually played song and hides pending tracks on Stop.

Evidence:

- [Live audio/agent history](programming-live-result.json)
- [Live browser checks](programming-browser-result.json)
- [Real-model queue/outro mutation check](programming-mutation-live-result.json)
- [Public player](screenshots/programming-public-1366.png)
- [Public player at 1024](screenshots/programming-public-1024.png)
- [Stop requested in management](screenshots/programming-stop-requested-1366.png)
- [Sign-off display](screenshots/programming-signoff-1680.png)

All test station data/authentication were isolated; the original auth file was
unchanged and production playback history/policies were not written by tests.
No application or test player was left broadcasting after verification.

To repeat the live check against an isolated station copy:

```sh
uv run python -m tests.programming_live_server
# In another terminal, use the printed local URL:
node tests/programming_browser_check.mjs http://127.0.0.1:PORT
```

This plays two complete real songs and can be audible. Stop the isolated server
afterward with Ctrl+C to close owned resources and save its evidence report.

## Current limits

The existing audio engine plays Suno songs. YouTube submissions remain ingested
and inspectable, but YouTube audio playback is not implemented. The agent reports
that limitation rather than claiming to play a YouTube entry. Host delivery is
animated text, not synthesized speech. A running queue/conversation is not
restored after an application restart. Programming is local station output, not
a newly implemented internet radio streaming service.
