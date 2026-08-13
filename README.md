# uTune Jukebox

uTune is a modern, dark-themed RFID-powered jukebox application built with Python. 
It allows users to link physical NFC/RFID cards to YouTube audio streams or local audio files. When a card is placed on the reader, uTune automatically plays the associated song, displaying beautiful album art and a dynamic glassmorphism UI.

The latest version is fully optimized for **ARM Android Tablets** using **Kivy** and **Buildozer**, while still supporting desktop testing environments.

## Features

- **Cross-Platform**: Runs on Android tablets (via Buildozer/Kivy) and Desktop.
- **RFID/NFC Integration**: Supports USB OTG serial readers and USB keyboard emulators.
- **YouTube Support**: Downloads and extracts audio directly via `yt-dlp`.
- **Local Audio**: Plays local MP3/FLAC/WAV files with embedded album art extraction.
- **Premium Interface**: Deep space background, glass cards, dynamic animations.
- **Queue System**: Up-next queue with visual thumbnails.

## Hardware Requirements (Android Tablet)
- Android Tablet (Android 11+ recommended)
- USB OTG Adapter
- USB RFID Card Reader (e.g. standard 125kHz or 13.56MHz reader that emulates a keyboard or provides a serial interface over USB)
- RFID/NFC Cards or Fobs

## Installation & Packaging (Buildozer)

To package uTune as an Android APK, you must use a Linux environment (or WSL on Windows).

1. Install Buildozer:
   ```bash
   pip install --user buildozer
   ```

2. Initialize (Optional, already provided in repo):
   ```bash
   buildozer init
   ```

3. Build the APK:
   ```bash
   # Make sure you are in the directory with buildozer.spec
   buildozer -v android debug
   ```

4. Deploy to connected Android device:
   ```bash
   buildozer -v android deploy run logcat
   ```

## Local Desktop Testing

You can run uTune locally on your desktop for testing before packaging to Android.

1. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Run the main jukebox UI:
   ```bash
   python jukebox/main.py
   ```

3. Run the card registration UI:
   ```bash
   python jukebox/register_cli.py
   ```

## Configuration

Configuration is managed via `jukebox/config.yaml`.
- **rfid.mode**: Set to `keyboard` for USB readers that emulate keystrokes. Set to `serial` for COM/TTY readers.
- **player.music_folder**: The directory where music is stored (defaults to a `music` folder in the app storage).

## Card Registration

To assign a song to an RFID card:
1. Launch the Registration UI.
2. Scan the card on the reader.
3. Select whether to use a YouTube URL or a Local File.
4. Confirm the details. The card is now linked!
