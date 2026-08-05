import subprocess
import threading
import time
import os
import shutil
import tempfile


AUDIO_EXTENSIONS = {'.mp3', '.flac', '.wav', '.ogg', '.m4a', '.aac', '.wma', '.opus'}


class Player:
    def __init__(self, mpv_path="mpv"):
        self.mpv_path = mpv_path
        self.mpv_process = None
        self.current_track = None
        self.is_playing = False
        self._stop_requested = False
        self.lock = threading.Lock()
        self.last_error = None
        self.on_status_change = None  # Callback: fn(status_str)
        self.play_start_time = None

    def _report(self, msg):
        print(f"[Player] {msg}")
        if self.on_status_change:
            try:
                self.on_status_change(msg)
            except Exception:
                pass

    def _find_ytdlp(self):
        return shutil.which("yt-dlp") or "yt-dlp"

    def play(self, url, track_info=None):
        self._play_local(url, track_info)

    def _play_local(self, source, track_info=None):
        from config import config

        self.stop()
        with self.lock:
            self._stop_requested = False
            self.current_track = track_info
            self.is_playing = True
            self.last_error = None
            self.play_start_time = None

        title = track_info["title"] if track_info else "Unknown"
        self._report(f"Loading: {title}")

        # Resolve the file path
        file_path = source
        if not os.path.isabs(file_path):
            file_path = os.path.join(config.music_folder, file_path)

        if not os.path.isfile(file_path):
            self.last_error = f"File not found: {os.path.basename(file_path)}"
            self._report(self.last_error)
            self._cleanup()
            return

        # Fetch local metadata in background
        if track_info:
            threading.Thread(
                target=self._fetch_local_metadata, args=(file_path, track_info), daemon=True
            ).start()

        mpv_cmd = [
            self.mpv_path,
            "--no-video",
            "--quiet",
            "--term-playing-msg=PLAYBACK_STARTED",
            "--terminal=yes",
            file_path,
        ]

        try:
            if self._stop_requested:
                return

            self._report("Starting audio stream...")

            self.mpv_process = subprocess.Popen(
                mpv_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                text=True,
                encoding='utf-8',
                errors='ignore',
            )

            self._report(f"Playing: {title}")

            def wait_for_playback():
                if self.mpv_process and self.mpv_process.stdout:
                    for line in iter(self.mpv_process.stdout.readline, ''):
                        if "PLAYBACK_STARTED" in line:
                            with self.lock:
                                self.play_start_time = time.time()

            threading.Thread(target=wait_for_playback, daemon=True).start()

            self.mpv_process.wait()

            if self.mpv_process.returncode != 0 and not self._stop_requested:
                self.last_error = f"mpv exited with code {self.mpv_process.returncode}"
                self._report(f"Playback error: {self.last_error}")

        except FileNotFoundError:
            self.last_error = "mpv not found"
            self._report(self.last_error)
        except Exception as e:
            self.last_error = str(e)
            self._report(f"Playback error: {e}")
        finally:
            self._cleanup()

    def _fetch_local_metadata(self, file_path, track_info):
        """Extract duration and album art from local audio files or yt-dlp sidecars."""
        try:
            import json
            import os
            
            base_path = os.path.splitext(file_path)[0]
            json_path = base_path + ".info.json"
            if os.path.exists(json_path):
                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    artist = data.get("artist") or data.get("uploader") or data.get("channel")
                    if artist:
                        track_info["artist"] = artist
                    if data.get("duration"):
                        track_info["duration"] = float(data["duration"])
                    if data.get("title") and not track_info.get("title"):
                        track_info["title"] = data["title"]
                except Exception:
                    pass
            
            for ext in [".webp", ".jpg", ".png", ".jpeg"]:
                img_path = base_path + ext
                if os.path.exists(img_path) and "image_bytes" not in track_info:
                    try:
                        with open(img_path, "rb") as f:
                            track_info["image_bytes"] = f.read()
                        break
                    except Exception:
                        pass

            ffprobe = shutil.which("ffprobe")
            if not ffprobe:
                return

            cmd = [
                ffprobe, "-v", "quiet",
                "-print_format", "json",
                "-show_format", "-show_streams",
                file_path,
            ]
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                fmt = data.get("format", {})
                tags = fmt.get("tags", {})

                duration = fmt.get("duration")
                if duration:
                    track_info["duration"] = float(duration)

                artist = tags.get("artist") or tags.get("album_artist")
                if artist:
                    track_info["artist"] = artist

                album = tags.get("album")
                if album:
                    track_info["album"] = album

            # Try to extract embedded cover art
            cover_cmd = [
                ffprobe if ffprobe else "ffprobe",
                "-v", "quiet", file_path,
            ]
            # Use ffmpeg to extract cover
            ffmpeg = shutil.which("ffmpeg")
            if ffmpeg:
                tmp_cover = os.path.join(tempfile.gettempdir(), "utune_cover.jpg")
                extract_cmd = [
                    ffmpeg, "-y", "-i", file_path,
                    "-an", "-vcodec", "mjpeg", "-frames:v", "1",
                    tmp_cover,
                ]
                cover_proc = subprocess.run(
                    extract_cmd, capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                if cover_proc.returncode == 0 and os.path.isfile(tmp_cover):
                    with open(tmp_cover, "rb") as f:
                        cover_data = f.read()
                    if len(cover_data) > 100:  # sanity check
                        track_info["image_bytes"] = cover_data
                    try:
                        os.remove(tmp_cover)
                    except OSError:
                        pass

            self._report("Metadata loaded")
        except Exception as e:
            print("[Player] Local metadata error:", e)



    def _cleanup(self):
        with self.lock:
            self.is_playing = False
            self.current_track = None
            self.mpv_process = None
            self.play_start_time = None

    def stop(self):
        with self.lock:
            self._stop_requested = True
            
            if self.mpv_process:
                try:
                    self.mpv_process.terminate()
                except Exception:
                    pass
                    
            # Try to kill if terminate doesn't work quickly
            if self.mpv_process:
                try:
                    self.mpv_process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.mpv_process.kill()

    def skip(self):
        self._report("Skipping...")
        self.stop()
