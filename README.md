# uTune Jukebox

uTune is a dark-themed RFID/NFC jukebox built with Python and Kivy. Physical cards map to local audio files. When a card is scanned, the track is queued and played, with album art and a fullscreen jukebox UI.

Optimized for **ARM Android tablets** (Kivy + Buildozer) and usable on desktop for testing.

Based on [uTune-Jukebox](https://github.com/AhmadTchnology/uTune-Jukebox) by [AhmadTchnology](https://github.com/AhmadTchnology), used with permission.

## Features

- **Cross-platform**: Android tablets (Buildozer/Kivy) and desktop
- **RFID/NFC**: USB OTG serial readers, USB keyboard-emulating readers, and built-in Android NFC
- **Local audio**: MP3/FLAC/WAV and other common formats, with album art when available
- **Queue**: up-next list with thumbnails
- **Fullscreen UI**: landscape 1920×1200 (Nexus 7 2013 and similar tablets)

## Hardware (Android tablet)

- Android tablet (Android 11+ recommended)
- USB OTG adapter (for USB readers)
- USB RFID reader (keyboard-emulating or serial) **or** built-in NFC
- RFID/NFC cards or fobs

## Desktop testing

```bash
pip install -r requirements.txt
python jukebox/main.py
```

## Android packaging (Buildozer)

Use Linux or WSL. From the directory that contains `buildozer.spec`:

```bash
pip install --user buildozer
buildozer -v android debug
buildozer -v android deploy run logcat
```

## Configuration

Edit `jukebox/config.yaml`:

- **rfid.mode**: `keyboard` (USB HID), `serial` (COM/TTY), or `nfc_android`
- **player.music_folder**: folder for local audio (defaults to a `music` folder in app storage)

Local audio files belong in `jukebox/music/` (see that folder’s README). The database (`jukebox.db`) is created at runtime and is not committed.

## License

This project currently has no license file. Ask the original author before redistributing beyond this personal copy.
