import subprocess
import threading
import time
import os
import shutil
import tempfile


class Player:
    def __init__(self, mpv_path="mpv", ytdlp_format="bestaudio/best"):
        self.mpv_path = mpv_path
        self.ytdlp_format = ytdlp_format
        self.ytdlp_process = None
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

        if track_info:
            threading.Thread(target=self._fetch_metadata_bg, args=(url, track_info), daemon=True).start()

        ytdlp = self._find_ytdlp()
        ytdlp_cmd = [
            ytdlp, 
            "-f", self.ytdlp_format, 
            "-o", "-",  # output to stdout
            "--no-playlist",
            "--remote-components", "ejs:github"
        ]

        if os.path.exists("deno.exe"):
            ytdlp_cmd.extend(["--js-runtimes", "deno:./deno.exe"])

        cookies_file = getattr(config, "ytdlp_cookies_file", None)
        cookies_browser = getattr(config, "ytdlp_cookies_browser", None)

        if cookies_file and os.path.isfile(cookies_file):
            ytdlp_cmd.extend(["--cookies", cookies_file])
        elif cookies_browser:
            if cookies_browser.lower() == "operagx":
                appdata = os.environ.get('APPDATA', '')
                cookies_browser = f"opera:{appdata}\\Opera Software\\Opera GX Stable"
            ytdlp_cmd.extend(["--cookies-from-browser", cookies_browser])

        ytdlp_cmd.append(url)

        mpv_cmd = [
            self.mpv_path,
            "--no-video",
            "--quiet",
            "--term-playing-msg=PLAYBACK_STARTED",
            "--terminal=yes",
            "-" # Read from stdin
        ]

        # Use temp files to capture errors without deadlocking pipes
        err_file_yt = tempfile.TemporaryFile(mode='w+', encoding='utf-8', errors='ignore')
        
        try:
            if self._stop_requested:
                return
                
            self._report("Starting audio stream...")
            
            # Start yt-dlp writing to stdout
            self.ytdlp_process = subprocess.Popen(
                ytdlp_cmd,
                stdout=subprocess.PIPE,
                stderr=err_file_yt,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            
            # Start mpv reading from yt-dlp's stdout
            self.mpv_process = subprocess.Popen(
                mpv_cmd,
                stdin=self.ytdlp_process.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                text=True,
                encoding='utf-8',
                errors='ignore'
            )
            
            # Close python's handle to stdout so mpv is the only reader
            # If mpv exits, yt-dlp gets SIGPIPE and terminates cleanly
            self.ytdlp_process.stdout.close()
            
            self._report(f"Playing: {title}")
            
            def wait_for_playback():
                if self.mpv_process and self.mpv_process.stdout:
                    for line in iter(self.mpv_process.stdout.readline, ''):
                        if "PLAYBACK_STARTED" in line:
                            with self.lock:
                                self.play_start_time = time.time()
            
            threading.Thread(target=wait_for_playback, daemon=True).start()
            
            # Wait for mpv to finish playing
            self.mpv_process.wait()
            
            # Wait for yt-dlp to exit just in case
            self.ytdlp_process.wait(timeout=5)

            if self.mpv_process.returncode != 0 and not self._stop_requested:
                err_file_yt.seek(0)
                stderr = err_file_yt.read().strip()
                if stderr:
                    self.last_error = stderr[:200]
                else:
                    self.last_error = f"mpv exited with code {self.mpv_process.returncode}"
                    
                self._report(f"Playback error: {self.last_error}")

        except FileNotFoundError:
            self.last_error = f"mpv or yt-dlp not found"
            self._report(self.last_error)
        except Exception as e:
            self.last_error = str(e)
            self._report(f"Playback error: {e}")
        finally:
            err_file_yt.close()
            self._cleanup()

    def _fetch_metadata_bg(self, url, track_info):
        import json
        import urllib.request
        try:
            ytdlp = self._find_ytdlp()
            cmd = [ytdlp, "--dump-json", "--no-playlist", url]
            from config import config
            cookies_file = getattr(config, "ytdlp_cookies_file", None)
            cookies_browser = getattr(config, "ytdlp_cookies_browser", None)
            if cookies_file and os.path.exists(cookies_file):
                cmd.extend(["--cookies", cookies_file])
            elif cookies_browser:
                if cookies_browser.lower() == "operagx":
                    appdata = os.environ.get('APPDATA', '')
                    cookies_browser = f"opera:{appdata}\\Opera Software\\Opera GX Stable"
                cmd.extend(["--cookies-from-browser", cookies_browser])

            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                artist = data.get("artist") or data.get("uploader") or data.get("channel")
                if artist:
                    track_info["artist"] = artist
                
                duration = data.get("duration")
                if duration:
                    track_info["duration"] = duration
                
                thumbnail_url = data.get("thumbnail")
                if thumbnail_url:
                    req = urllib.request.Request(thumbnail_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        track_info["image_bytes"] = resp.read()
                
                self._report("Metadata loaded")
        except Exception as e:
            print("[Player] Metadata fetch error:", e)

    def _cleanup(self):
        with self.lock:
            self.is_playing = False
            self.current_track = None
            self.ytdlp_process = None
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
                    
            if self.ytdlp_process:
                try:
                    self.ytdlp_process.terminate()
                except Exception:
                    pass
                    
            # Try to kill if terminate doesn't work quickly
            if self.mpv_process:
                try:
                    self.mpv_process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.mpv_process.kill()
                    
            if self.ytdlp_process:
                try:
                    self.ytdlp_process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.ytdlp_process.kill()

    def skip(self):
        self._report("Skipping...")
        self.stop()
