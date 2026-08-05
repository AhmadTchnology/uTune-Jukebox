import queue
import threading

class JukeboxQueue:
    def __init__(self):
        self._q = queue.Queue()
        self._items = []
        self._lock = threading.Lock()

    def enqueue(self, uid, title, youtube_url, currently_playing_uid=None):
        """Enqueues a song if it's not already in the queue or playing."""
        with self._lock:
            if currently_playing_uid == uid:
                return False # Already playing

            # Check if already in queue
            for item in self._items:
                if item['uid'] == uid:
                    return False # Already in queue

            item = {'uid': uid, 'title': title, 'youtube_url': youtube_url}
            self._items.append(item)
            self._q.put(item)
            
            # Start async metadata fetch for the queue item
            threading.Thread(target=self._fetch_metadata_bg, args=(item,), daemon=True).start()
            return True

    def _fetch_metadata_bg(self, item):
        import json
        import urllib.request
        import subprocess
        import shutil
        import os
        try:
            ytdlp = shutil.which("yt-dlp") or "yt-dlp"
            cmd = [ytdlp, "--dump-json", "--no-playlist", item['youtube_url']]
            
            # Use cookies if available
            try:
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
            except ImportError:
                pass

            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                artist = data.get("artist") or data.get("uploader") or data.get("channel")
                if artist:
                    item['artist'] = artist
                
                duration = data.get("duration")
                if duration:
                    item["duration"] = duration
                
                thumbnail_url = data.get("thumbnail")
                if thumbnail_url:
                    req = urllib.request.Request(thumbnail_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        item["image_bytes"] = resp.read()
        except Exception as e:
            print("[Queue] Metadata fetch error:", e)

    def dequeue(self):
        """Blocks until a track is available, returns it."""
        item = self._q.get()
        with self._lock:
            if item in self._items:
                self._items.remove(item)
        return item

    def get_upcoming(self):
        """Returns a list of upcoming items."""
        with self._lock:
            return list(self._items)
        
    def clear(self):
        with self._lock:
            while not self._q.empty():
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    break
            self._items.clear()
