# Browser audio interruption and reload recovery

The reported interruption recovered when Kern reloaded `/listen/`. The backend
remained responsive. The exact failed request from that incident was not
captured, so its precise trigger remains unconfirmed.

A matching failure was found in the audio client: its catch block paused audio
after **any** failed command poll. If the connection recovered with the same
command revision, it never called `play()` again. The backend could continue
reporting “playing” because neither a terminal event nor a pause report arrived.
Event-reporting failures were also silently discarded.

## Changes

The existing Chrome/HTML Audio, Suno resolution/decryption, and SQLite history
approach remain in use. No playback or scheduling architecture was replaced.

- Brief command failures leave audio playing. Requests have a two-second
  timeout. After approximately five seconds of repeated failures, audio pauses
  and reports the problem. When control returns, the same element resumes at
  its position only if that song is still the active command. A Stop wins.
- Event posts check HTTP acknowledgement and retry pending notifications.
  Repeated progress is coalesced, and a failed request no longer discards EOF.
- The bridge retains the last reported position. Reloading the station window
  seeks near it instead of restarting at zero, without another history start.
- Terminal events idle the bridge command so a reload does not replay a song
  that already ended.
- The browser sends a heartbeat each second. After 15 seconds without reports
  for an active session, the bridge stops it, logs the missing contact and
  notifies playback of the failure. A missing/frozen audio frame therefore
  cannot leave the backend claiming ordinary playback indefinitely.
- Media errors, unexpected pauses, control loss, browser exit, missing contact,
  startup failures and history-write exceptions now reach Python logging with
  song/session/position context. Client reporting failures also produce a browser
  console diagnostic. Neither media URLs nor authentication are logged.
- Management shows the actual playback error. During a control pause it reports
  reconnecting audio; the public view shows reconnecting/buffering appropriately.

Normal Stop Programming still lets the current song finish. Completion follows
the actual `ended` event; a contact timeout records interruption, not completion.

## Loading the update and finding logs

The inspected production process started at **14:48**, before saved-error
logging was added. Those Python changes were not active there, and the expected
default log directory did not yet exist.

After the current show signs off, restart with `uv run main.py`, then use
**Start Programming** to open its station window. A browser reload updates the
client script but does **not** reload Python services or install backend logging
and heartbeat changes.

Errors append to `~/.config/kernradio/logs/errors.log` (or the corresponding
`XDG_CONFIG_HOME` location), with rotation. A killed process or power loss cannot
write its own final traceback. This does not guarantee recovery from every
possible audio or operating-system failure.

## Verification

- **56 focused Python tests passed**, covering playback, history, programming,
  management, diagnostics and actual main startup.
- **Seven deterministic JavaScript checks passed** against the real client
  source with fake Audio/network/clock: transient loss, sustained pause/resume,
  Stop on reconnect, failed EOF delivery, reload position, unexpected pause,
  and coalesced progress/heartbeats.
- A separate **real Suno/Chrome integration check passed** using isolated SQLite
  and muted headless Chrome. It verified real decoding/progress, brief and
  sustained injected command failures, reload recovery, retried EOF, no replay
  after completion, and contact timeout with saved logs. History contained one
  completed and one interrupted event. The reload resumed at **7.99 seconds**
  after a saved position of **8.00 seconds**. This was not a human audibility
  test; the EOF check sought near the end instead of playing the whole song.
- Production playback, catalog, policies and authentication were untouched.
  The isolated browser and server were closed afterward.

[Actual browser, history and log evidence](audio-recovery-live-result.json).

```sh
node tests/audio_player_check.mjs
uv run python -m tests.audio_recovery_live_check
```
