# Suno playback service

The [browser audio recovery follow-up](audio-recovery.md) documents control-drop
recovery, resume on reload, heartbeats and playback error logging.

The service reproduces Crucible Radio's Suno playback method. It is independent
of Flask and the Codex agent and adds no controls to either existing frontend.
It plays one explicitly requested song at a time; there is no playlist or scheduler.

## Read-only reference inspection

Reference project: `/home/nori/PycharmProjects/CrucibleRadio`.
Only source files were read; the reference app was not imported, launched,
installed into, or modified.

The reusable parts were:

- `main.py`, `fetch_suno_track_metadata` near line 3931: fetch the requested song
  page (including short-link redirects) and locate its media URL. Its preferred
  path is M4A, because advertised direct MP3 URLs can reject public playback.
- `main.py`, `cache_decrypted_suno_audio` near line 3858: POST the clip ID to
  Suno's public guest-rights endpoint; derive the guest key with SHA-256; unwrap
  the content key/IV with AES-GCM; decrypt the media with AES-CTR; check the M4A
  header; cache the prepared audio through a temporary file and atomic replace.
- `static/js/public.js`: `new Audio()`, preload, `src`, `load()`, `play()`,
  `pause()`, and playback/buffering/error/ended events. It is browser playback,
  not a Python player or an FFmpeg process.
- `main.py`'s `/assets/audio/…` route serves the prepared file for browser playback.

Crucible's radio programming state, segment selection, scene rendering, database
queries, TTS, YouTube fallback, artist normalization, Flask routes, and global
asset directory were application-specific and were not copied.

Its actual `pyproject.toml` declares `cryptography`, Flask and yt-dlp. Its
`requirements.txt` lists only Flask and does not fully describe its Suno path.
Kern adds only `cryptography`; neither yt-dlp nor FFmpeg is used here.

## Organization and public API

`services.suno_playback` provides:

| Interface | Responsibility |
| --- | --- |
| `SunoAudioResolver.resolve(song_id_or_url)` | Resolve a UUID/canonical/share URL and prepare `ResolvedAudio(suno_song_id, source)`. Uses the proven M4A/guest-rights/AES method, with the reference's direct-MP3 fallback. |
| `SunoAudioResolver.close()` | Delete its private temporary audio cache. |
| `BrowserAudioBackend.play(audio, session, callback)` | Start the prepared source through Chrome/Chromium HTML Audio. |
| `BrowserAudioBackend.stop()` / `close()` | Request browser stop, or release the owned browser and loopback bridge. |
| `SunoPlaybackService(catalog, history, *, resolver=None, backend=None)` | Compose audio preparation, browser lifecycle, existing catalog references, and history notifications. |
| `play(song_id_or_url, *, startup_timeout=20)` | Prepare audio, wait for an actual `playing` event, record its start, and return status. Raises `PlaybackError` on failure. |
| `status()` | Return a detached status dictionary. |
| `wait(timeout=None)` | Wait for a terminal result; raises `TimeoutError` if still active at the requested timeout. This never infers completion. |
| `stop(*, timeout=10)` | Stop audio, await browser acknowledgement, and record interruption. |
| `close()` | Stop active audio and release browser, HTTP bridge, browser profile, and temporary media. Idempotent; also provided through context-manager exit. |

Status keys: `state`, `suno_song_id`, opaque `session`, `event_id`,
`position_seconds`, and `error`. States are `idle`, `resolving`, `starting`,
`playing`, `buffering`, `completed`, `stopped`, and `failed`.

```python
from pathlib import Path
from services.catalog import MusicCatalogService
from services.database import DatabaseService
from services.playback import PlaybackHistoryService
from services.suno_playback import SunoPlaybackService

database = DatabaseService(Path('data/catalog.sqlite3'))
catalog = MusicCatalogService(database)
history = PlaybackHistoryService(database, catalog)

with SunoPlaybackService(catalog, history) as player:
    player.play('https://suno.com/s/VctrMTtdiC6NYQZq')
    # player.status() reports actual playback state; player.stop() interrupts it.
    result = player.wait()  # Completes only on the browser's ended event.
```

The history service requires a catalog song reference. If absent, this service
creates only a minimal record with Suno ID and submitted URL. It does not fetch
or infer catalog metadata and never overwrites an existing title or artist.

## Browser bridge and lifecycle

The browser is still the audio engine, as in Crucible Radio. A standard-library
loopback HTTP bridge replaces only the reference's Flask transport: it serves
prepared media with byte-range support, exposes one playback command, and
receives browser events. It has no frontend player controls or application
business logic. The bridge binds to `127.0.0.1` on an automatic port and uses an
unguessable per-instance URL prefix. Cross-origin event submissions are rejected.

The service owns a normal Chrome window with tabs and an address bar, using a
temporary browser profile. This keeps the station accessible as a browser window
for Discord sharing; it no longer launches in Chrome app mode.
It does not attach to existing browser windows or reuse a user's profile.
It launches with Chrome's documented local autoplay development switch so a
new frontend button is not required:
[Chrome autoplay policy and developer switches](https://developer.chrome.com/blog/autoplay).
No browser sandbox is disabled and no automation framework is required.

Only one active playback is permitted per service instance. Stop the current
one before explicitly requesting another. Terminal instances can play again
unless the owned browser has closed; after a browser-process failure, close
that service and create a new instance.

History is based on observed events:

- `playing`: create one `PlaybackHistoryService.start_playback` event with
  current UTC time. Loading, autoplay rejection, and buffering before actual
  playing create no playback history.
- `ended`: call `finish_playback` with the actual event receipt time.
- Confirmed stop, audio error, or unexpected owned-browser exit: call
  `interrupt_playback` if a start was recorded.

Repeated `playing` callbacks do not duplicate starts. Events from an older
session are ignored. No metadata duration, timer, media seek, accelerated
playback, or scheduled end determines completion. History write failures report
failure and request audio stop rather than claiming an unrecorded success. If
storage fails during finalization, the reported error identifies that the
pending event needs review; the service does not fabricate a completed record.

If browser stop acknowledgement times out, the owned browser is terminated and
the active event is interrupted after that process has stopped. Abruptly killing
the entire Python process cannot provide the same orderly shutdown guarantee;
normal callers must use `close()` or the context manager.

M4A media is downloaded/prepared before playback, matching the reference, not
stream-decrypted. It is cached only for this resolver's lifetime and deleted
on close. The direct MP3 fallback streams through the browser and may still
fail if Suno rejects it. Guest-rights values are used only in memory; they are
not printed or persisted. Suno can change its public media or rights behavior;
those failures are surfaced rather than replaced by another playback method.

Metric definitions are unchanged: recorded airtime remains UTC end-minus-start,
including any buffering time between those observations.

## Dependencies and local testing

Requirements:

- The project's Python 3.13+ environment; `uv run` installs the locked dependencies.
- `cryptography>=50.0.1` (verified with 50.0.2), matching the reference's crypto library.
- Google Chrome or Chromium on PATH (`google-chrome`, `chromium`, or `chromium-browser`).
- A working desktop session and audio output. Linux PipeWire/PulseAudio was used
  for verification. This service is not configured for headless/silent playback.

From the project root, play a song and let it finish; Ctrl+C stops it:

```sh
uv run python -m services.suno_playback https://suno.com/s/VctrMTtdiC6NYQZq
```

This command uses a temporary isolated catalog/history by default. To persist
playback in the application's catalog, explicitly supply its path:

```sh
uv run python -m services.suno_playback https://suno.com/s/VctrMTtdiC6NYQZq --database ~/.config/kernradio/catalog.sqlite3
```

No Flask server or agent is launched. A small audio-only browser window is
expected. Keep it open during playback; closing it is reported as an interruption.

## Actual verification

Offline regression checks:

```sh
uv run python -m unittest tests.test_suno_playback -v
uv run python -m unittest discover -q
```

All **117 tests passed**, including 18 new offline playback tests. They use
real isolated SQLite and real cryptographic transforms, but fake HTTP responses
and browser events. They do not establish audible playback by themselves.
JavaScript syntax was separately checked with Node.

The opt-in actual desktop test is:

```sh
uv run python -m tests.suno_playback_live_check https://suno.com/s/VctrMTtdiC6NYQZq
```

Executed successfully with the actual Suno song **Mireille’s Lullaby**, UUID
`0746f5e7-4d09-4d6b-9129-733c341f23eb`, using the reference rights/decryption and
browser method:

1. Resolved the real short URL, prepared the real M4A, and started real audio.
2. Browser position advanced beyond five seconds. PipeWire/PulseAudio reported
   an active, unmuted Chrome output (`corked=false`, `mute=false`). This was real
   desktop audio output, not a mocked browser, null sink, or simulated player.
3. Stopped it and verified an interrupted event.
4. Replayed the cached media without changing playback speed or seeking and
   waited through the **148.12-second** song to the real `ended` event.
5. Started another attempt and deliberately terminated only the test-owned
   Chrome browser. The service reported its failure and recorded interruption.
6. Verified existing metrics: **3 starts, 1 completed, 2 interrupted, 0 unfinished,
   153.751159 recorded airtime seconds** across all three attempts.

Full status, event timestamps, audio-output evidence, and metrics are recorded
in `suno-playback-live-result.json`. Tests used an isolated temporary database
and temporary browser/media directories; user catalog data, Codex authentication,
and Crucible Radio files were not modified. The successful test did not depend
on human confirmation of listening; it verified browser progress, the real
unmuted desktop output stream, natural end, and persisted lifecycle events.
