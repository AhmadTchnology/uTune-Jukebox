import time
import threading
from config import config

class RFIDReader:
    def __init__(self, callback):
        self.callback = callback
        self.mode = config.rfid_mode
        self.debounce_time = config.rfid_debounce
        self.last_uid = None
        self.last_read_time = 0
        self.running = False
        self.thread = None

    def start(self):
        self.running = True
        if self.mode == "keyboard":
            self.thread = threading.Thread(target=self._keyboard_loop, daemon=True)
            self.thread.start()
        elif self.mode == "serial":
            self.thread = threading.Thread(target=self._serial_loop, daemon=True)
            self.thread.start()
        else:
            print(f"Unsupported RFID mode: {self.mode}")

    def stop(self):
        self.running = False
        # Close stdin to unblock the blocking input() call in keyboard mode
        if self.mode == "keyboard":
            try:
                import sys
                sys.stdin.close()
            except Exception:
                pass

    def _process_uid(self, uid):
        now = time.time()
        # Debounce
        if uid == self.last_uid and (now - self.last_read_time) < self.debounce_time:
            return
            
        self.last_uid = uid
        self.last_read_time = now
        self.callback(uid)

    def _keyboard_loop(self):
        """For USB readers acting as keyboard emulators. Reads from stdin."""
        print("RFID Reader (Keyboard Mode) started. Waiting for input...")
        while self.running:
            try:
                # Note: This will block until enter is pressed. 
                uid = input().strip()
                if uid and self.running:
                    self._process_uid(uid)
            except (EOFError, ValueError):
                break
            except Exception as e:
                if self.running:
                    print(f"Keyboard reader error: {e}")
                time.sleep(1)

    def _serial_loop(self):
        """For USB serial readers."""
        import serial
        print(f"RFID Reader (Serial Mode) started on {config.rfid_serial_port}...")
        try:
            ser = serial.Serial(config.rfid_serial_port, config.rfid_baud_rate, timeout=1)
            while self.running:
                try:
                    if ser.in_waiting > 0:
                        line = ser.readline().decode('utf-8', errors='ignore').strip()
                        if line:
                            self._process_uid(line)
                    else:
                        time.sleep(0.1)
                except serial.SerialException as e:
                    print(f"Serial error: {e}")
                    time.sleep(1)
        except Exception as e:
            print(f"Failed to open serial port: {e}")
