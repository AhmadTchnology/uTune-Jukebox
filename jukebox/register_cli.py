import os
import sys
import time

# Ensure the working directory is the script directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from config import config
from registry import Registry
from rfid_reader import RFIDReader

def main():
    print("RFID Jukebox Registration CLI")
    print("-----------------------------")
    
    registry = Registry(config.db_path)
    
    uid_captured = [None]
    
    def on_scan(uid):
        uid_captured[0] = uid
    
    reader = RFIDReader(callback=on_scan)
    reader.start()
    
    try:
        while True:
            uid_captured[0] = None
            print("\nPlease scan a card to register (or press Ctrl+C to exit)...")
            
            # Wait for scan
            while uid_captured[0] is None:
                time.sleep(0.1)
                
            scanned_uid = uid_captured[0]
            print(f"\nCard Scanned! UID: {scanned_uid}")
            
            # Check if exists
            existing = registry.get_card(scanned_uid)
            if existing:
                print(f"This card is already registered to: {existing['title']}")
                ans = input("Do you want to overwrite it? (y/N): ")
                if ans.lower() != 'y':
                    continue
            
            url = input("Enter YouTube URL: ").strip()
            title = input("Enter Song Title: ").strip()
            
            if url and title:
                registry.register_card(scanned_uid, title, url)
                print(f"Success! Card {scanned_uid} mapped to '{title}'.")
            else:
                print("Registration cancelled. URL and Title are required.")
                
    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        reader.stop()

if __name__ == "__main__":
    main()
