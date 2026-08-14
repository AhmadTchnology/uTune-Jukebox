import queue
import threading
import json
import os

class JukeboxQueue:
    def __init__(self):
        self._q = queue.Queue()
        self._items = []
        self._lock = threading.Lock()

    def enqueue(self, uid, title, file_path, artist="", currently_playing_uid=None):
        """Enqueues a song if it's not already in the queue or playing."""
        with self._lock:
            if currently_playing_uid == uid:
                return False

            for item in self._items:
                if item["uid"] == uid:
                    return False

            item = {"uid": uid, "title": title, "artist": artist, "file_path": file_path}
            self._items.append(item)
            self._q.put(item)

            threading.Thread(target=self._fetch_metadata_bg, args=(item,), daemon=True).start()
            return True

    def _fetch_metadata_bg(self, item):
        self._fetch_local_metadata(item)

    def _fetch_local_metadata(self, item):
        try:
            from config import config

            file_path = item["file_path"].replace('\\', '/')
            if not os.path.isabs(file_path) and not file_path.startswith("http"):
                file_path = os.path.join(config.music_folder, file_path)

            json_path = file_path + ".info.json"
            if not os.path.exists(json_path):
                base_path = os.path.splitext(file_path)[0]
                json_path = base_path + ".info.json"
                if not os.path.exists(json_path):
                    json_path = base_path + ".json"
            
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8-sig") as f:
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

            base_path = os.path.splitext(file_path)[0]
            for ext in [".webp", ".jpg", ".png", ".jpeg"]:
                img_path = file_path + ext
                if not os.path.exists(img_path):
                    img_path = base_path + ext
                if os.path.exists(img_path) and "image_bytes" not in item:
                    try:
                        with open(img_path, "rb") as f:
                            item["image_bytes"] = f.read()
                        break
                    except Exception:
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
