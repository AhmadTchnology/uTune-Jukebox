__version__ = "1.0.0"

import os
import threading
import signal
import sys

# Ensure the working directory is the script directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from kivy.app import App
from kivy.core.window import Window
from kivy.clock import Clock
from config import config
from registry import Registry
from queue_manager import JukeboxQueue
from player import Player
from rfid_reader import RFIDReader
from ui import UI
from platform_utils import request_android_permissions, is_android


class JukeboxApp(App):
    def build(self):
        self.title = "uTune Jukebox"
        if config.ui_fullscreen:
            Window.fullscreen = 'auto'
        else:
            Window.size = config.ui_resolution

        print("Starting uTune Jukebox...")

        self.registry = Registry(config.db_path)
        self.queue_mgr = JukeboxQueue()
        self.player = Player()

        def on_rfid_scan(uid):
            print(f"[App] Scanned UID: {uid}")
            card = self.registry.get_card(uid)
            if card:
                # Check what's currently playing to avoid enqueuing the exact same thing
                curr = self.player.current_track['uid'] if self.player.current_track else None
                success = self.queue_mgr.enqueue(uid, card['title'], card['file_path'], currently_playing_uid=curr)
                if success:
                    self.ui.show_toast(f"Added: {card['title']}", 2.0)
                else:
                    self.ui.show_toast("Already playing or in queue", 2.0)
            else:
                self.ui.show_toast(f"Unknown Card: {uid}", 3.0)

        self.ui = UI(config, self.queue_mgr, self.player, on_scan=on_rfid_scan)

        # RFID reader for desktop/serial modes
        self.reader = RFIDReader(callback=self.ui.handle_scan)
        self.reader.start()

        # Keyboard event binding for UI (USB OTG keyboard readers)
        Window.bind(on_key_down=self.ui.handle_key_down)

        # Player worker loop
        def player_worker():
            while True:
                item = self.queue_mgr.dequeue()
                if item is None:
                    break
                # Only pass URL/Path to play
                self.player.play(item['file_path'], track_info=item)

        self.worker_thread = threading.Thread(target=player_worker, daemon=True)
        self.worker_thread.start()

        self.ui.start()

        # Request Android permissions AFTER the activity is fully built
        Clock.schedule_once(self._request_permissions, 1.0)

        return self.ui

    def _request_permissions(self, dt):
        """Request runtime permissions after the Activity is ready."""
        def on_permissions_result(permissions, grant_results):
            print(f"[App] Permissions result: {list(zip(permissions, grant_results))}")
            for perm, granted in zip(permissions, grant_results):
                if not granted:
                    print(f"[App] WARNING: Permission denied: {perm}")

        request_android_permissions(callback=on_permissions_result)

    def on_pause(self):
        if hasattr(self, 'reader'):
            self.reader.disable_nfc_foreground()
        return True

    def on_resume(self):
        if hasattr(self, 'reader'):
            self.reader.enable_nfc_foreground()

    def on_stop(self):
        print("Shutting down...")
        self.reader.stop()
        self.player.stop()
        self.ui.stop_ui()
        self.queue_mgr._q.put(None)


def main():
    JukeboxApp().run()


if __name__ == '__main__':
    main()
