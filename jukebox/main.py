import time
import threading
import signal
import sys
import os

# Ensure the working directory is the script directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from config import config
from registry import Registry
from queue_manager import JukeboxQueue
from player import Player
from rfid_reader import RFIDReader
from ui import UI

def main():
    print("Starting RFID Jukebox...")
    registry = Registry(config.db_path)
    queue_mgr = JukeboxQueue()
    player = Player(mpv_path=config.mpv_path)
    
    def on_rfid_scan(uid):
        print(f"Scanned UID: {uid}")
        card = registry.get_card(uid)
        if card:
            # Check what's currently playing to avoid enqueuing the exact same thing
            curr = player.current_track['uid'] if player.current_track else None
            success = queue_mgr.enqueue(uid, card['title'], card['youtube_url'], currently_playing_uid=curr)
            if success:
                ui.show_toast(f"Added: {card['title']}", 2.0)
            else:
                ui.show_toast(f"Already playing or in queue", 2.0)
        else:
            ui.show_toast(f"Unknown Card: {uid}", 3.0)

    # We create the UI instance but it has to run in the main thread
    ui = UI(config, queue_mgr, player, on_scan=on_rfid_scan)

    reader = RFIDReader(callback=on_rfid_scan)
    
    # Player worker loop
    def player_worker():
        while True:
            # this will block until an item is available
            item = queue_mgr.dequeue()
            if item is None: # Sentinel for shutdown
                break
            player.play(item['youtube_url'], track_info=item)

    worker_thread = threading.Thread(target=player_worker, daemon=True)
    worker_thread.start()
    
    reader.start()
    
    def shutdown(signum, frame):
        print("Shutting down...")
        reader.stop()
        player.stop()
        ui.running = False
        # Push sentinel to queue to unblock player
        queue_mgr._q.put(None)
        
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        # Run UI in main thread (blocks until window closed)
        ui.run()
    except Exception as e:
        print(f"UI Error: {e}")
    finally:
        shutdown(None, None)

if __name__ == '__main__':
    main()
