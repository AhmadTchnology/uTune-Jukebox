import time
import threading
from config import config
from platform_utils import is_android


class RFIDReader:
    """RFID reader supporting built-in Android NFC, USB OTG keyboard, and serial modes.

    On Android tablets, built-in NFC is handled via Intent tracking using pyjnius.
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
        self._current_activity = None
        self._pending_intent = None
        self._intent_filters = None

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
            self.disable_nfc_foreground()

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

    # ── Android built-in NFC ─────────────────────────────────────────────────
    def _setup_android_nfc(self):
        try:
            from jnius import autoclass, cast
            import android
            
            # Needed classes
            NfcAdapter = autoclass('android.nfc.NfcAdapter')
            Intent = autoclass('android.content.Intent')
            PendingIntent = autoclass('android.app.PendingIntent')
            IntentFilter = autoclass('android.content.IntentFilter')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            
            self._current_activity = cast('android.app.Activity', PythonActivity.mActivity)
            self._nfc_adapter = NfcAdapter.getDefaultAdapter(self._current_activity)
            
            if not self._nfc_adapter:
                print("[RFID] No NFC adapter found on this Android device.")
                return
                
            # Create a pending intent
            intent = Intent(self._current_activity, self._current_activity.getClass())
            intent.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP)
            
            # Use FLAG_MUTABLE (33554432) for Android 12+ compatibility if needed, or 0
            # PendingIntent.FLAG_MUTABLE = 33554432
            self._pending_intent = PendingIntent.getActivity(self._current_activity, 0, intent, 33554432)
            
            # Intent filters for NFC tags
            filter_tag = IntentFilter("android.nfc.action.TAG_DISCOVERED")
            filter_ndef = IntentFilter("android.nfc.action.NDEF_DISCOVERED")
            filter_tech = IntentFilter("android.nfc.action.TECH_DISCOVERED")
            
            self._intent_filters = [filter_tag, filter_ndef, filter_tech]
            
            # Bind to Kivy Activity's on_new_intent event
            android.activity.bind(on_new_intent=self._on_new_intent)
            
            print("[RFID] Android NFC adapter initialized successfully.")
            self.enable_nfc_foreground()
            
        except Exception as e:
            print(f"[RFID] Failed to setup Android NFC: {e}")
            
    def _on_new_intent(self, intent):
        try:
            action = intent.getAction()
            print(f"[RFID] Received Android intent: {action}")
            if action in (
                "android.nfc.action.TAG_DISCOVERED",
                "android.nfc.action.NDEF_DISCOVERED",
                "android.nfc.action.TECH_DISCOVERED"
            ):
                from jnius import autoclass, cast
                NfcAdapter = autoclass('android.nfc.NfcAdapter')
                tag = cast('android.nfc.Tag', intent.getParcelableExtra(NfcAdapter.EXTRA_TAG))
                
                if tag:
                    tag_id = tag.getId()
                    # Convert java byte array to hex string
                    uid_hex = "".join([f"{b & 0xFF:02x}" for b in tag_id])
                    print(f"[RFID] Read native NFC tag: {uid_hex}")
                    self._process_uid(uid_hex)
        except Exception as e:
            print(f"[RFID] Error handling new NFC intent: {e}")

    def enable_nfc_foreground(self):
        if self._nfc_adapter and self._current_activity and self._pending_intent:
            try:
                print("[RFID] Enabling NFC foreground dispatch...")
                self._nfc_adapter.enableForegroundDispatch(
                    self._current_activity, 
                    self._pending_intent, 
                    self._intent_filters, 
                    None
                )
            except Exception as e:
                print(f"[RFID] Failed to enable NFC foreground dispatch: {e}")

    def disable_nfc_foreground(self):
        if self._nfc_adapter and self._current_activity:
            try:
                print("[RFID] Disabling NFC foreground dispatch...")
                self._nfc_adapter.disableForegroundDispatch(self._current_activity)
            except Exception as e:
                print(f"[RFID] Failed to disable NFC foreground dispatch: {e}")
