# Kern Radio — responsive browser-window composition

Run KernRadioApp with `uv run main.py`, open its management page, and click
**Start Programming** after setup. The illustrated station window opens
automatically. `http://127.0.0.1:5000/listen/` also opens a display-only view;
additional windows do not duplicate audio. Opening `index.html` directly shows
the idle design without a backend or demo songs.

## Fix

The previous layout fitted a 1672 × 941 composition into every viewport with `Math.min`, leaving exposed gutters or empty space when the browser was not 16:9. This build retains uniform scaling (no squashed mascot or artwork), dynamically widens or tallens the *scene* to fill the browser content area, keeps the rail anchored to the right, and distributes extra horizontal space among the central stage and commentary. The rainforest background and canopy cover the entire adapted scene. Window resizes are handled automatically.

The reference composition is unchanged at exactly 1672 × 941. Larger and smaller windows fill edge to edge without side bars. For very small screens this is still a scaled desktop display, not a fully reflowed mobile UI.

The original structural layout has now been verified in actual Chrome at three
desktop sizes as part of live programming integration. Track data, queue,
artwork, status and timed character dialogue come from the local backend.
Track-list changes give the rail a brief, small alternating rotation. New songs
animate the framed artwork into place; the forest background drifts slowly and
fireflies move, fade and reappear at varied positions. System reduced-motion
preferences disable these effects.
See [programming verification](../docs/radio-programming.md) for screenshots and
real playback evidence. Original graphic assets are retained.
