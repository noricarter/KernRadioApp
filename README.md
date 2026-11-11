# Kern Radio

A local Flask application assembled from reusable configuration, web hosting,
and Codex services. It provides guided ChatGPT connection, a basic agent chat,
a SQLite music catalog, and manual Discord-text ingestion with targeted Suno
metadata retrieval. A standalone Suno playback service is available for explicit
single-song requests. GPT-6 Luna can now prepare and run local radio programming
through the illustrated frontend, with brief animated host dialogue and graceful Stop.

## Fresh install

The application requires the official Codex CLI. The Windows launcher below
installs Codex CLI, uv, Python 3.13, and the app packages. On macOS and Linux,
install uv and Codex CLI separately as described below. No API key is needed.

On Windows 10 or 11, download the [Kern Radio ZIP](https://github.com/noricarter/KernRadioApp/archive/refs/heads/main.zip)
and choose **Extract All**. Open the extracted `KernRadioApp-main` folder and
double-click **Start Kern Radio.bat**. That launcher installs uv, Codex CLI,
Python 3.13, and the app packages as needed, then starts Kern Radio. Git and a
separate Python installation are not needed. The first setup can take several
minutes and requires an internet connection. Keep the black window open while
the station is running; press **Ctrl+C** there to stop it. The app opens a
browser window. Choose **Connect ChatGPT** and sign in when asked.

If Windows asks whether to allow the app to run, choose **Run anyway** only if
you downloaded the ZIP from this Kern Radio GitHub repository. The launcher
downloads uv and Codex CLI installers from their official providers. If setup
fails, it leaves the window open and displays the error instead of closing.

On macOS or Linux, install [uv](https://docs.astral.sh/uv/getting-started/installation/)
and the [Codex CLI](https://github.com/openai/codex#quickstart) using their
official instructions. Git is required for this setup route. Then run:

```bash
git clone https://github.com/noricarter/KernRadioApp.git
cd KernRadioApp
uv python install 3.13
uv sync --locked
codex --version
uv run main.py
```

`uv sync --locked` creates the project environment and installs the exact
dependencies recorded in `uv.lock`; it fails if the lockfile and `pyproject.toml`
disagree. `uv run main.py`
also synchronizes the environment, so the explicit sync step is useful as an
early install check and can be skipped for the shortest launch:

```sh
uv run main.py
```

The terminal prints the app URL. Open that URL, choose **Connect ChatGPT**, sign
in on the official page, and return to the app. If the browser blocks the new
tab, use **Open sign-in page**. Existing Codex authentication is detected
automatically; choose **Reconnect** if the account needs to be connected again.
Stop the app with **Ctrl+C** in its terminal. No application API key or separate
app login is needed.

Starting station playback also requires Google Chrome or Chromium. On Linux the
browser must be discoverable as `google-chrome`, `chromium`, or
`chromium-browser` on PATH. On macOS the standard Google Chrome application is
detected; on Windows Chrome in its standard Program Files or per-user location
is detected. Without a supported browser, setup and the management app still
work, but playback cannot open its audio player. `uv sync` installs Python
dependencies; the SQLite database file and its tables are created automatically
when the app first starts. No separate database server or manual schema step is
needed. Settings, catalog, workspace, artwork, and logs go under
`%LOCALAPPDATA%\kernradio` on Windows, `~/.config/kernradio` on Linux/macOS, or
the directory in `XDG_CONFIG_HOME` when set.

The clean install and launch were exercised on Ubuntu 24.04 x86_64 with Python
3.13, including a fresh uv environment and empty app data directory. The Windows
one-click launcher has not yet been exercised on a Windows machine. Kern Radio
starts Codex CLI as a local helper process; its startup errors appear in the
launcher window and are written to the app error log.

The service listens only on this computer. It uses port 5000 when available and
automatically selects a free loopback port when that port is occupied; use the
URL printed in the terminal. If Codex is unavailable, the web app still opens
with a reconnect action; install/fix Codex, ensure `codex` is on PATH, then
choose **Reconnect**.

## Components and interfaces

| Component | Public interface | Dependencies |
| --- | --- | --- |
| `services.configuration.ConfigurationService` | `get`, `update`, `setup_completed` | Python standard library; JSON path supplied by caller |
| `services.codex.CodexService` | `start`, `connected`, `account`, `begin_login`, `login_status`, `start_conversation`, `send_message`, `close` | Codex CLI; private stdio JSON-RPC transport; Python standard library |
| `services.web.FlaskWebService` | `start`, `wait`, `stop`, `url` | Flask/Werkzeug; assembled Flask app supplied by caller |
| `radio.controller.ApplicationController` | `start`, `status`, `connect`, `send`, `close` | Configuration and Codex service instances |
| `radio.web.create_app` | Flask application factory; thin HTTP adapters | Application controller; templates and static assets |
| `main.py` | Composition and process entry point | Above services and adapters |

The Codex service handles `initialize`/`initialized`, managed browser login,
account inspection, thread creation, turns, completion events, timeouts,
process loss, and cleanup. Kern Radio explicitly selects **GPT-6 Luna**
(`gpt-6-luna`) for its host conversations. The reusable Codex service accepts an
optional `model` argument; other applications can retain Codex's default. Conversation
threads remain in memory for the current app session; chat history is not
restored after an app restart. Only one turn runs at a time.

Setup completion is stored atomically at
`~/.config/kernradio/settings.json` (or under `XDG_CONFIG_HOME` when set).
The app's empty agent workspace is alongside this file. Application configuration
contains no tokens or account credentials. Codex owns its existing authentication
storage, including any configured `CODEX_HOME` or credential store. Reconnecting
uses Codex's supported login, never a custom credential form or logout.

Chat threads use a read-only sandbox and instructions to avoid tools. Interactive
server requests are rejected because this milestone has no tool approval UI.
Local browser writes require a per-process token, trusted host and same-origin
requests. The local Flask/Werkzeug host is intended for this development milestone.

## Verification

Unit/component tests (fake agent and protocol peer; no authentication touched):

```sh
uv run python -m unittest discover -v
```

Opt-in real integration check, using a private temporary Codex home:

```sh
uv run python -m tests.integration_check
```

This starts the real helper, checks signed-out state, initiates the official
browser authentication flow and cancels it. It does not sign you out or change
your existing login.

To also verify actual inference and restart with an existing **file-backed**
Codex ChatGPT login:

```sh
uv run python -m tests.integration_check --reuse-auth
```

This copies Codex's auth file into a private temporary test home, uses it only
there, and deletes the directory afterward. It does not print tokens or write
the original authentication file. A real turn uses the account's normal usage
allowance. Keyring-only credentials require an interactive login in an isolated
test home instead; the test does not extract credential-store secrets.

Verified locally: 21 automated tests; `uv run main.py` startup with isolated fresh
configuration and graceful shutdown; real helper initialization; browser login
initiation and cancellation; real HTTP chat returning “Radio connection
confirmed”; setup/authentication reuse after restart; reconnect feedback after
actual process termination. The original auth file's SHA-256 was unchanged.
JavaScript syntax was checked with Node.

Not verified: a person completing a new browser OAuth login from end to end,
visual browser interaction, or a live expired-token scenario. Login completion
and expired-auth handling have mocked coverage. Existing successful authentication
and inference were verified against the real service.

Protocol reference: [official Codex App Server documentation](https://developers.openai.com/codex/app-server).

## Database and music catalog

Reusable SQLite persistence and catalog operations are documented in
[services/catalog/README.md](services/catalog/README.md), including public
interfaces, schema, composition example, and isolated tests. This layer adds
no dependencies. The management import flow now consumes it through application
orchestration; the agent has no catalog tools yet.

## Music ingestion

The management page now includes **Import music**. Its parser, targeted Suno
lookup, application orchestration, and live verification are documented in
[docs/music-ingestion.md](docs/music-ingestion.md). The parser follows the four
supplied Crucible posts, including Unicode authors and short Suno share links.
Their real HTTP-to-Suno-to-SQLite import and reimport passed in an isolated
database; full extracted fields and stored-record summaries are documented there.
The imported-songs list in **Browse & edit music** can be searched by song title,
artist name, or artist alias. While a program is live, open a song to **Play next**
or **Append to queue**; the host prepares its introduction and outro.

The [catalog review and artwork follow-up](docs/catalog-review-artwork.md)
highlights saved failed imports and specific questioned fields in the review
window. New imports cache the largest available song image locally; artwork
has also been backfilled for all 79 existing catalog songs. Originals are kept
for later thumbnail generation.

## Playback history and metrics

[Playback data services](docs/playback-history.md) provide callable start,
finish, interruption, history, and calculated song/artist/library statistics.
They reuse the SQLite database and catalog, with one additional event table.
The history/metrics package is independent of the separate Suno playback service.
Live programming uses these recorded events to advance only after actual song endings.

## Suno playback

The [Suno playback service](docs/suno-playback.md) reuses Crucible Radio's
guest-rights/M4A preparation and browser HTML Audio method. Chrome/Chromium and
the locked `cryptography` dependency are required. Test one song locally:

```sh
uv run python -m services.suno_playback https://suno.com/s/VctrMTtdiC6NYQZq
```

Ctrl+C stops playback. The test command uses temporary history unless a
`--database` path is supplied. The management application's programming controller
now composes this same playback service with the host agent and public frontend.

## Operating policy discovery

The existing agent chat can inspect scoped catalog/playback information,
propose station policies, and retrieve previously approved decisions. Manage
policies in the **Policies** button beside **Now Playing**; discuss additions or
revisions with the host in chat, then approve, decline, or disable them in the
modal. The agent has no approval or disabling tool. [Interfaces, authority
boundary, schema, and real Codex
conversation evaluation](docs/operating-policies.md) are documented separately.
No default policies are seeded. Live programming reads Kern's approved policies;
the agent owns selection decisions and the existing human approval boundary remains.

## Kern Radio management interface

The approved brown/gold/cream/green management layout is integrated with the
existing services: the approved explorer mascot, central conversation, policy
modal, import results, contextual onboarding, catalog editing, and actual
station information. Start/Stop Programming now control an actual local run.

After setup, click **Start Programming** to open the illustrated station window.
The host greets listeners, introduces queued songs, and delivers brief outros.
Request programming/dialogue changes through the same backend chat. **Stop
Programming** finishes the current song, signs off, and leaves the station idle.
The host speaks through animated text; the existing Suno player provides audio.
Now Playing shows only the artwork thumbnail, song title, and artist. Song
Details follows the track that is currently playing, with its style and playback
history; the edit button edits that current song.
Dismiss unwanted host or system replies with the × beside a message; dismissals
are remembered in that browser and do not remove your own messages.

[Programming interfaces, operation, real audio verification and screenshots](docs/radio-programming.md).

Each active programming turn receives fresh playback history and song counts.
Queue tools check recorded starts and require a reason for a replay. See
[varied programming and failure diagnostics](docs/programming-variety.md).
Errors are saved across restarts in `~/.config/kernradio/logs/errors.log`;
routine web-request traffic is quiet.

[Implementation, onboarding rules, browser verification and screenshots](docs/backend-ui.md)
