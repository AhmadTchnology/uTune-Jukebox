import time
import threading
from config import config
from platform_utils import is_android


class RFIDReader:
    """RFID reader supporting built-in Android NFC, USB OTG keyboard, and serial modes.

    On Android tablets, built-in NFC is handled via enableReaderMode (API 19+)
    with a PythonJavaClass callback — no Intents needed.
    Keyboard emulation mode captures keystrokes in the Kivy UI layer.
    """

    def __init__(self, callback):
        self.callback = callback
        self.mode = config.rfid_mode
        self.debounce_time = config.rfid_debounce
        self.last_uid = None
        self.last_read_time = 0
        self.running = False
        self.thread = None

        # For Android NFC
        self._nfc_adapter = None
        self._activity = None
        self._reader_callback = None

    def start(self):
        self.running = True
        print(f"[RFID] Starting reader in '{self.mode}' mode...")
        if self.mode == "serial":
            self.thread = threading.Thread(target=self._serial_loop, daemon=True)
            self.thread.start()
        elif self.mode == "nfc_android" and is_android():
            self._setup_android_nfc()
        # "keyboard" mode is handled by the UI layer (Kivy Window key events)

    def stop(self):
        self.running = False
        if self.mode == "nfc_android" and is_android():
            self.disable_nfc_reader_mode()

    def on_uid_scanned(self, uid):
        """Called by the UI layer when a keyboard-emulated UID is received."""
        if uid:
            self._process_uid(uid.strip())

    def _process_uid(self, uid):
        now = time.time()
        if uid == self.last_uid and (now - self.last_read_time) < self.debounce_time:
            return
        self.last_uid = uid
        self.last_read_time = now
        self.callback(uid)

    def _serial_loop(self):
        """For USB OTG serial readers."""
        import serial
        print(f"[RFID] Serial mode on {config.rfid_serial_port}...")
        try:
            ser = serial.Serial(config.rfid_serial_port, config.rfid_baud_rate, timeout=1)
            while self.running:
                try:
                    if ser.in_waiting > 0:
                        line = ser.readline().decode("utf-8", errors="ignore").strip()
                        if line:
                            self._process_uid(line)
                    else:
                        time.sleep(0.1)
                except Exception as e:
                    print(f"[RFID] Serial error: {e}")
                    time.sleep(1)
        except Exception as e:
            print(f"[RFID] Failed to open serial port: {e}")

    # ── Android built-in NFC (Reader Mode API) ──────────────────────────────
    def _setup_android_nfc(self):
        """Use enableReaderMode — the modern, reliable NFC API.

        Unlike enableForegroundDispatch (Intent-based), this directly invokes
        a callback when a tag is discovered. Works reliably on Kivy/p4a.
        """
        try:
            from jnius import autoclass, PythonJavaClass, java_method
            from kivy.clock import Clock

            NfcAdapter = autoclass('android.nfc.NfcAdapter')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')

            self._activity = PythonActivity.mActivity
            self._nfc_adapter = NfcAdapter.getDefaultAdapter(self._activity)

            if not self._nfc_adapter:
                print("[RFID] No NFC adapter found on this device.")
                return

            if not self._nfc_adapter.isEnabled():
                print("[RFID] NFC adapter is disabled. Please enable NFC in Settings.")
                return

            # Create the ReaderCallback as a PythonJavaClass
            reader = self
            
            class NfcReaderCallback(PythonJavaClass):
                __javainterfaces__ = ['android/nfc/NfcAdapter$ReaderCallback']
                __javacontext__ = 'app'

                @java_method('(Landroid/nfc/Tag;)V')
                def onTagDiscovered(self, tag):
                    try:
                        tag_id = tag.getId()
                        # Convert Java byte[] to hex string
                        uid_hex = "".join([f"{b & 0xFF:02x}" for b in tag_id])
                        print(f"[RFID] NFC tag discovered: {uid_hex}")
                        # Schedule on main thread for thread safety
                        Clock.schedule_once(lambda dt: reader._process_uid(uid_hex), 0)
                    except Exception as e:
                        print(f"[RFID] Error reading NFC tag: {e}")

            self._reader_callback = NfcReaderCallback()

            print("[RFID] Android NFC adapter initialized. Enabling reader mode...")
            self.enable_nfc_reader_mode()

        except Exception as e:
            print(f"[RFID] Failed to setup Android NFC: {e}")
            import traceback
            traceback.print_exc()

    def enable_nfc_reader_mode(self):
        """Enable NFC Reader Mode to detect all tag types."""
        if not self._nfc_adapter or not self._activity or not self._reader_callback:
            return
        try:
            from jnius import autoclass
            NfcAdapter = autoclass('android.nfc.NfcAdapter')

            # Combine flags for all common tag types + skip NDEF parsing
            flags = (
                NfcAdapter.FLAG_READER_NFC_A
                | NfcAdapter.FLAG_READER_NFC_B
                | NfcAdapter.FLAG_READER_NFC_F
                | NfcAdapter.FLAG_READER_NFC_V
                | NfcAdapter.FLAG_READER_NFC_BARCODE
                | NfcAdapter.FLAG_READER_SKIP_NDEF_CHECK
                | NfcAdapter.FLAG_READER_NO_PLATFORM_SOUNDS
            )

            self._nfc_adapter.enableReaderMode(
                self._activity,
                self._reader_callback,
                flags,
                None,  # No extras Bundle needed
            )
            print("[RFID] NFC reader mode enabled successfully.")
        except Exception as e:
            print(f"[RFID] Failed to enable NFC reader mode: {e}")

    def disable_nfc_reader_mode(self):
        """Disable NFC Reader Mode (call on pause/stop)."""
        if not self._nfc_adapter or not self._activity:
            return
        try:
            self._nfc_adapter.disableReaderMode(self._activity)
            print("[RFID] NFC reader mode disabled.")
        except Exception as e:
            print(f"[RFID] Failed to disable NFC reader mode: {e}")

    # Keep legacy names for main.py on_pause/on_resume compatibility
    def enable_nfc_foreground(self):
        self.enable_nfc_reader_mode()

    def disable_nfc_foreground(self):
        self.disable_nfc_reader_mode()
