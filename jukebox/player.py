import json
import subprocess
import threading
import time
import os
import shutil
import tempfile

from platform_utils import is_android, get_subprocess_flags


AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".wma", ".opus"}


class Player:
    def __init__(self):
        self.current_track = None
        self.is_playing = False
        self.is_paused = False
        self._stop_requested = False
        self.lock = threading.Lock()
        self.last_error = None
        self.on_status_change = None
        self.play_start_time = None
        self._backend = None
        self.volume = 68
        self._paused_at = None

    def _report(self, msg):
        print(f"[Player] {msg}")
        if self.on_status_change:
            try:
                self.on_status_change(msg)
            except Exception:
                pass

    # ── mpv control channel ──────────────────────────────────────────────────
    # mpv runs as a subprocess with no stdin we can drive, so pause/volume go
    # over its JSON IPC socket (a named pipe on Windows, a unix socket else).

    def _ipc_path(self):
        if os.name == "nt":
            return r"\\.\pipe\utune-mpv-%d" % os.getpid()
        return os.path.join(tempfile.gettempdir(), "utune-mpv-%d.sock" % os.getpid())

    def _mpv_ipc(self, command):
        payload = json.dumps({"command": command}).encode("utf-8") + b"\n"
        path = self._ipc_path()
        try:
            if os.name == "nt":
                with open(path, "wb", buffering=0) as pipe:
                    pipe.write(payload)
            else:
                import socket

                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                try:
                    sock.connect(path)
                    sock.sendall(payload)
                finally:
                    sock.close()
            return True
        except Exception as e:
            print(f"[Player] mpv ipc failed ({command[0]}): {e}")
            return False

    def _backend_kind(self, backend):
        if backend is None:
            return None
        if is_android():
            return "android"
        if hasattr(backend, "terminate"):
            return "mpv"
        return "kivy"

    # ── volume ───────────────────────────────────────────────────────────────

    def set_volume(self, level):
        """Set output volume, 0-100. Applies live and to subsequent tracks."""
        level = max(0, min(100, int(level)))
        self.volume = level
        self._apply_volume()

    def _apply_volume(self):
        with self.lock:
            backend = self._backend
        kind = self._backend_kind(backend)
        try:
            if kind == "android":
                gain = self.volume / 100.0
                backend.setVolume(gain, gain)
            elif kind == "mpv":
                self._mpv_ipc(["set_property", "volume", self.volume])
            elif kind == "kivy":
                backend.volume = self.volume / 100.0
        except Exception as e:
            print(f"[Player] set volume failed: {e}")

    # ── pause / resume ───────────────────────────────────────────────────────

    def pause(self):
        if not self.is_playing or self.is_paused:
            return
        with self.lock:
            backend = self._backend
        kind = self._backend_kind(backend)
        if kind is None:
            return

        # Raise the flag *before* touching the backend. The per-backend wait
        # loops use is_paused to stay alive, and stopping a Kivy Sound would
        # otherwise race them straight into _cleanup() and drop the track.
        self.is_paused = True
        self._paused_at = time.time()
        try:
            if kind == "android":
                backend.pause()
            elif kind == "mpv":
                if not self._mpv_ipc(["set_property", "pause", True]):
                    raise RuntimeError("mpv ipc unavailable")
            elif kind == "kivy":
                # Kivy's Sound has no pause; remember the position and stop.
                try:
                    self._kivy_pos = backend.get_pos()
                except Exception:
                    self._kivy_pos = 0
                backend.stop()
        except Exception as e:
            self.is_paused = False
            self._paused_at = None
            print(f"[Player] pause failed: {e}")
            return

        self._report("Paused")

    def resume(self):
        if not self.is_paused:
            return
        with self.lock:
            backend = self._backend
        kind = self._backend_kind(backend)
        if kind is None:
            return

        try:
            if kind == "android":
                backend.start()
            elif kind == "mpv":
                if not self._mpv_ipc(["set_property", "pause", False]):
                    return
            elif kind == "kivy":
                backend.play()
                try:
                    backend.seek(getattr(self, "_kivy_pos", 0))
                except Exception:
                    pass
        except Exception as e:
            print(f"[Player] resume failed: {e}")
            return

        # Keep the progress bar honest: discount the time spent paused.
        if self.play_start_time and self._paused_at:
            self.play_start_time += time.time() - self._paused_at
        self.is_paused = False
        self._paused_at = None
        self._report("Resumed")

    def toggle_pause(self):
        if self.is_paused:
            self.resume()
        else:
            self.pause()

    def elapsed(self):
        """Seconds of the current track that have actually been heard.

        Frozen while paused, so the progress bar and clock stop with the audio.
        """
        if not self.play_start_time:
            return 0.0
        end = self._paused_at if (self.is_paused and self._paused_at) else time.time()
        return max(0.0, end - self.play_start_time)

    def play(self, source, track_info=None):
        self.stop()
        with self.lock:
            self._stop_requested = False
            self.current_track = track_info
            self.is_playing = True
            self.is_paused = False
            self._paused_at = None
            self.last_error = None
            self.play_start_time = None

        title = track_info["title"] if track_info else "Unknown"
        self._report(f"Loading: {title}")

        from config import config
        file_path = source
        if not os.path.isabs(file_path):
            file_path = os.path.join(config.music_folder, file_path)

        if not os.path.isfile(file_path):
            self.last_error = f"File not found: {os.path.basename(file_path)}"
            self._report(self.last_error)
            self._cleanup()
            return

        if track_info:
            threading.Thread(
                target=self._fetch_local_metadata, args=(file_path, track_info), daemon=True
            ).start()

        if is_android():
            self._play_android(file_path, title)
        else:
            self._play_desktop(file_path, title)

    def _play_android(self, file_path, title):
        """Play audio using Android's MediaPlayer via pyjnius."""
        try:
            from jnius import autoclass

            MediaPlayer = autoclass("android.media.MediaPlayer")
            FileInputStream = autoclass("java.io.FileInputStream")
            
            mp = MediaPlayer()
            fis = FileInputStream(file_path)
            mp.setDataSource(fis.getFD())
            mp.prepare()
            fis.close()

            with self.lock:
                self._backend = mp
                self.play_start_time = time.time()

            self._apply_volume()
            self._report(f"Playing: {title}")
            mp.start()

            # Wait for completion. A paused MediaPlayer reports isPlaying()
            # False, so the pause flag has to hold the loop open.
            while (mp.isPlaying() or self.is_paused) and not self._stop_requested:
                if not self.current_track.get("duration"):
                    dur = mp.getDuration()
                    if dur > 0:
                        self.current_track["duration"] = dur / 1000.0
                time.sleep(0.5)

            if not self._stop_requested:
                mp.release()
        except Exception as e:
            self.last_error = str(e)
            self._report(f"Android playback error: {e}")
        finally:
            self._cleanup()

    def _play_desktop(self, file_path, title):
        """Play audio using mpv subprocess on desktop."""
        mpv_path = shutil.which("mpv") or "mpv"
        mpv_cmd = [
            mpv_path,
            "--no-video",
            "--quiet",
            "--term-playing-msg=PLAYBACK_STARTED",
            "--terminal=yes",
            f"--input-ipc-server={self._ipc_path()}",
            f"--volume={self.volume}",
            file_path,
        ]

        try:
            if self._stop_requested:
                return

            self._report("Starting audio stream...")
            proc = subprocess.Popen(
                mpv_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="ignore",
                **get_subprocess_flags(),
            )

            with self.lock:
                self._backend = proc

            self._report(f"Playing: {title}")

            def wait_for_playback():
                if proc and proc.stdout:
                    for line in iter(proc.stdout.readline, ""):
                        if "PLAYBACK_STARTED" in line:
                            with self.lock:
                                self.play_start_time = time.time()

            threading.Thread(target=wait_for_playback, daemon=True).start()
            proc.wait()

            if proc.returncode != 0 and not self._stop_requested:
                self.last_error = f"mpv exited with code {proc.returncode}"
                self._report(f"Playback error: {self.last_error}")

        except FileNotFoundError:
            self._report("mpv not found, falling back to Kivy SoundLoader...")
            self._play_kivy(file_path, title)
        except Exception as e:
            self.last_error = str(e)
            self._report(f"Playback error: {e}")
            self._cleanup()

    def _play_kivy(self, file_path, title):
        """Fallback desktop player using Kivy's SoundLoader (ffpyplayer/sdl2)."""
        from kivy.core.audio import SoundLoader
        from kivy.clock import Clock
        
        sound = SoundLoader.load(file_path)
        if not sound:
            self.last_error = "SoundLoader could not load audio."
            self._report(self.last_error)
            self._cleanup()
            return

        with self.lock:
            self._backend = sound
            self.play_start_time = time.time()

        self._apply_volume()
        self._report(f"Playing: {title}")

        # We must play from main thread safely
        def do_play(dt):
            if sound:
                sound.play()
        Clock.schedule_once(do_play, 0)

        # Wait up to 2 seconds for state to transition to 'play'
        for _ in range(20):
            if sound.state == 'play' or self._stop_requested:
                break
            time.sleep(0.1)

        # Wait while playing. Pausing a Kivy Sound means stopping it, so the
        # pause flag has to hold the loop open.
        while (sound.state == 'play' or self.is_paused) and not self._stop_requested:
            if not self.current_track.get("duration"):
                if hasattr(sound, 'length') and sound.length > 0:
                    self.current_track["duration"] = sound.length
            time.sleep(0.2)
            
        Clock.schedule_once(lambda dt: sound.stop(), 0)
        self._cleanup()

    def _fetch_local_metadata(self, file_path, track_info):
        """Extract duration and album art from local audio files (.info.json)."""
        try:
            import json

            base_path = os.path.splitext(file_path)[0]
            json_path = base_path + ".info.json"
            if not os.path.exists(json_path):
                json_path = base_path + ".json"
            
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
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

            self._report("Metadata loaded")
        except Exception as e:
            print("[Player] Local metadata error:", e)

    def _cleanup(self):
        with self.lock:
            self.is_playing = False
            self.is_paused = False
            self._paused_at = None
            self.current_track = None
            self._backend = None
            self.play_start_time = None

    def stop(self):
        with self.lock:
            self._stop_requested = True
            self.is_paused = False
            self._paused_at = None
            backend = self._backend

        if backend is None:
            return

        if is_android():
            try:
                backend.stop()
                backend.release()
            except Exception:
                pass
        else:
            if hasattr(backend, 'terminate'):
                # It's a subprocess (mpv)
                try:
                    backend.terminate()
                except Exception:
                    pass
                try:
                    backend.wait(timeout=1)
                except Exception:
                    try:
                        backend.kill()
                    except Exception:
                        pass
            elif hasattr(backend, 'stop'):
                # It's a Kivy Sound object
                try:
                    from kivy.clock import Clock
                    Clock.schedule_once(lambda dt: backend.stop(), 0)
                except Exception:
                    pass

    def skip(self):
        self._report("Skipping...")
        self.stop()
