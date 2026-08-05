# Build Prompt: RFID-Powered Raspberry Pi Jukebox (Python)

## Project Overview
Build a Python application for a Raspberry Pi that turns physical RFID cards into "song selectors." Each card is associated with a YouTube URL. Tapping a card on a 13.56MHz RFID reader adds that track to a play queue, which streams audio via yt-dlp/mpv and shows live status (now playing + queue) on a small HDMI screen.

## Hardware Assumptions
- Raspberry Pi (4 or 5 recommended) running Raspberry Pi OS (64-bit)
- RFID reader: MFRC522 module (13.56MHz), connected via USB
- 13.56MHz RFID cards/tags (MIFARE Classic or NTAG-compatible)
- HDMI display (1024x600), no touch required
- Internet connection (Wi-Fi or Ethernet) for YouTube streaming
- Speaker/audio out via Bluetooth speaker

## Core Functional Requirements
1. **RFID reading loop**: continuously poll the MFRC522 for a card UID. Debounce so the same card isn't re-added repeatedly while held on the reader.
2. **Card registry**: maintain a local database (SQLite preferred, JSON acceptable for v1) mapping `card_uid -> {title, youtube_url}`. This is more robust than writing full URLs onto the tiny memory of a card — cards just carry a UID, and the Pi looks up the associated song.
3. **Admin/registration mode**: a simple CLI or minimal on-screen mode to "register" a new card — scan a blank card, then prompt for/paste a YouTube URL and title, save to the registry.
4. **Queue management**: thread-safe FIFO queue. Scanning a registered card enqueues its track (skip if it's already the currently playing track or already sitting in queue).
5. **Playback engine**: pull from the queue one track at a time; use `yt-dlp` to resolve audio and `mpv` (via `python-mpv` or subprocess with `--no-video --ytdl-format=bestaudio`) to play; block until finished, then advance.
6. **Display UI**: fullscreen Pygame (or similar lightweight) interface showing:
   - Now Playing (title, elapsed/total time if available)
   - Upcoming queue (list of titles)
   - Simple status icons: idle / playing / network error / unknown card scanned
7. **Unknown card handling**: if a scanned UID isn't in the registry, show a clear "Unknown card" message on screen rather than failing silently.
8. **Error handling**: network loss, invalid/unavailable YouTube URL, or yt-dlp failure should show an on-screen error and skip to the next queued track rather than crashing.

## Non-Functional Requirements
- Clean modular structure (no single monolithic script)
- Config file (YAML or .env) for adjustable settings: audio device, database path, debounce time, display resolution
- Logging to a rotating log file for debugging
- Runs headless-capable as a `systemd` service that autostarts on boot into the fullscreen UI
- Graceful shutdown (SIGTERM) that stops playback cleanly

## Suggested Module Structure
```
jukebox/
├── main.py              # entry point, wires everything together
├── config.py            # loads settings from config.yaml/.env
├── rfid_reader.py        # MFRC522 polling loop, returns UIDs via callback/queue
├── registry.py            # SQLite-backed card_uid -> song lookup + registration logic
├── queue_manager.py      # thread-safe play queue, dedupe logic
├── player.py              # yt-dlp + mpv playback wrapper, play/skip/stop controls
├── ui.py                  # Pygame display: now playing, queue list, status
├── register_cli.py        # standalone script for admin card registration
└── config.yaml            # user-editable settings
```

## Suggested Data Model (registry.py)
```
Table: cards
  uid TEXT PRIMARY KEY
  title TEXT
  youtube_url TEXT
  date_added TIMESTAMP
```

## Suggested Program Flow
1. `main.py` starts three concurrent components: RFID listener thread, playback worker thread, and the Pygame UI main loop.
2. RFID listener detects a UID → looks up in `registry` → if found, pushes to `queue_manager` → UI updates queue display.
3. Playback worker pulls next track from queue → calls `player.play(url)` → UI updates "Now Playing" → on completion, loop continues.
4. Unknown UID → UI shows a transient "Unknown card" toast instead of enqueueing anything.

## Dependencies
- `mfrc522` or `spidev` + `RPi.GPIO` (for the reader)
- `yt-dlp`
- `mpv` (system package) + `python-mpv` (Python bindings) — or subprocess calls to the `mpv` binary
- `pygame` (UI)
- `sqlite3` (standard library)
- `pyyaml` (config)

## Deliverables Expected From This Prompt
- Fully working Python source matching the module structure above
- A `config.yaml` with sensible defaults
- A `systemd` service file for autostart
- Basic instructions for registering new RFID cards
- Comments explaining the RFID → registry → queue → player → UI data flow
