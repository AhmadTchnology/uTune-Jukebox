import queue
import threading
import json
import subprocess
import shutil
import os
import tempfile

from platform_utils import get_subprocess_flags


class JukeboxQueue:
    def __init__(self):
        self._q = queue.Queue()
        self._items = []
        self._lock = threading.Lock()

    def enqueue(self, uid, title, youtube_url, currently_playing_uid=None):
        """Enqueues a song if it's not already in the queue or playing."""
        with self._lock:
            if currently_playing_uid == uid:
                return False

            for item in self._items:
                if item["uid"] == uid:
                    return False

            item = {"uid": uid, "title": title, "youtube_url": youtube_url}
            self._items.append(item)
            self._q.put(item)

            threading.Thread(target=self._fetch_metadata_bg, args=(item,), daemon=True).start()
            return True

    def _fetch_metadata_bg(self, item):
        self._fetch_local_metadata(item)

    def _fetch_local_metadata(self, item):
        try:
            from config import config

            file_path = item["youtube_url"]
            if not os.path.isabs(file_path):
                file_path = os.path.join(config.music_folder, file_path)

            base_path = os.path.splitext(file_path)[0]
            json_path = base_path + ".info.json"
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    artist = data.get("artist") or data.get("uploader") or data.get("channel")
                    if artist:
                        item["artist"] = artist
                    if data.get("duration"):
                        item["duration"] = data["duration"]
                    if data.get("title") and not item.get("title"):
                        item["title"] = data["title"]
                except Exception:
                    pass

            for ext in [".webp", ".jpg", ".png", ".jpeg"]:
                img_path = base_path + ext
                if os.path.exists(img_path) and "image_bytes" not in item:
                    try:
                        with open(img_path, "rb") as f:
                            item["image_bytes"] = f.read()
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
                **get_subprocess_flags(),
            )
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                fmt = data.get("format", {})
                tags_raw = fmt.get("tags", {})
                tags = {k.lower(): v for k, v in tags_raw.items()}

                duration = fmt.get("duration")
                if duration:
                    item["duration"] = float(duration)

                artist = tags.get("artist") or tags.get("album_artist")
                if artist:
                    item["artist"] = artist

                title = tags.get("title")
                if title:
                    item["title"] = title

            # Extract embedded cover art
            ffmpeg = shutil.which("ffmpeg")
            if ffmpeg and "image_bytes" not in item:
                tmp_cover = os.path.join(tempfile.gettempdir(), f"utune_q_{id(item)}.jpg")
                extract_cmd = [
                    ffmpeg, "-y", "-i", file_path,
                    "-an", "-vcodec", "mjpeg", "-frames:v", "1",
                    tmp_cover,
                ]
                cover_proc = subprocess.run(
                    extract_cmd, capture_output=True,
                    **get_subprocess_flags(),
                )
                if cover_proc.returncode == 0 and os.path.isfile(tmp_cover):
                    with open(tmp_cover, "rb") as f:
                        cover_data = f.read()
                    if len(cover_data) > 100:
                        item["image_bytes"] = cover_data
                    try:
                        os.remove(tmp_cover)
                    except OSError:
                        pass
        except Exception as e:
            print("[Queue] Local metadata error:", e)

    def dequeue(self):
        """Blocks until a track is available, returns it."""
        item = self._q.get()
        with self._lock:
            if item in self._items:
                self._items.remove(item)
        return item

    def get_upcoming(self):
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

    def reorder(self, from_idx, to_idx):
        with self._lock:
            if from_idx < 0 or from_idx >= len(self._items):
                return
            if to_idx < 0 or to_idx >= len(self._items):
                return
            if from_idx == to_idx:
                return
            item = self._items.pop(from_idx)
            self._items.insert(to_idx, item)
            while not self._q.empty():
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    break
            for it in self._items:
                self._q.put(it)
