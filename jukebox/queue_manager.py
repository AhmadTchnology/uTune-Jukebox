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
            return True

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
