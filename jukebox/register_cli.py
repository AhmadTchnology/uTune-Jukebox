"""uTune Card Registration UI — Kivy implementation.

State machine:
  HOME → WAITING_SCAN → PICK_SOURCE → INPUT_URL / PICK_FILE
       → INPUT_TITLE → CONFIRM → DONE → (loop)
"""
import os
import math
import threading
import time as _time

from kivy.app import App
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.textinput import TextInput
from kivy.graphics import Color, Rectangle, RoundedRectangle, Ellipse, Line
from kivy.clock import Clock
from kivy.core.text import Label as CoreLabel

from config import config
from registry import Registry
from rfid_reader import RFIDReader
from player import AUDIO_EXTENSIONS
from platform_utils import get_subprocess_flags, is_android


# ── Colour palette ───────────────────────────────────────────────────────────
def _c(r, g, b, a=255):
    return (r / 255, g / 255, b / 255, a / 255)


class C:
    BG         = _c(6, 8, 16)
    BG_INDIGO  = _c(18, 16, 58)
    BG_VIOLET  = _c(26, 11, 46)
    CYAN       = _c(34, 211, 238)
    VIOLET     = _c(139, 92, 246)
    GLASS_BG   = _c(30, 27, 75)
    TEXT       = _c(241, 245, 249)
    TEXT_SEC    = _c(148, 163, 184)
    TEXT_MUTED  = _c(71, 85, 105)
    GREEN      = _c(74, 222, 128)
    RED        = _c(248, 113, 113)
    ORANGE     = _c(251, 191, 36)


def _rgba(c, a=None):
    if a is not None:
        return (c[0], c[1], c[2], a)
    return c


# States
STATE_HOME         = "home"
STATE_WAITING_SCAN = "waiting_scan"
STATE_PICK_SOURCE  = "pick_source"
STATE_INPUT_URL    = "input_url"
STATE_DOWNLOADING  = "downloading"
STATE_PICK_FILE    = "pick_file"
STATE_INPUT_TITLE  = "input_title"
STATE_CONFIRM      = "confirm"
STATE_DONE         = "done"
STATE_LIST         = "list"


class RegisterUI(FloatLayout):
    """Card registration screen — Kivy version."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.registry = Registry(config.db_path)
        self.state = STATE_HOME
        self.pulse = 0.0

        # Registration data
        self.scanned_uid = None
        self.existing_card = None
        self.selected_source = None
        self.reg_title = ""
        self.reg_url = ""
        self.download_progress = ""

        # File picker
        self.local_files = []
        self.file_scroll = 0
        self.file_selected = -1

        # Cards list
        self.cards_list = []
        self.cards_scroll = 0

        # Toast
        self.toast_msg = ""
        self.toast_end = 0

        # Text input widget (added/removed dynamically)
        self._text_input = None
        self._input_label = ""

        # RFID reader
        self._uid_buffer = None
        self.reader = RFIDReader(callback=self._on_rfid_scan)
        self.reader.start()

        self._key_buffer = ""

    def _on_rfid_scan(self, uid):
        self._uid_buffer = uid

    def start(self):
        Clock.schedule_interval(self._tick, 1.0 / config.ui_fps)

    def stop_ui(self):
        Clock.unschedule(self._tick)
        self.reader.stop()

    # ── main loop ────────────────────────────────────────────────────────────
    def _tick(self, dt):
        self.pulse += dt * 2.0

        # Check for scanned UID
        if self._uid_buffer and self.state == STATE_WAITING_SCAN:
            self.scanned_uid = str(self._uid_buffer)
            self._uid_buffer = None
            self.existing_card = self.registry.get_card(self.scanned_uid)
            self.state = STATE_PICK_SOURCE

        self._redraw()

    # ── keyboard handling ────────────────────────────────────────────────────
    def handle_key_down(self, window, key, scancode, codepoint, modifiers):
        if key == 27:  # ESC
            if self.state in (STATE_HOME, STATE_DONE):
                return False  # Let app quit
            self._reset()
            self.state = STATE_HOME
            self._remove_text_input()
            return True

        if self.state == STATE_HOME:
            if codepoint and codepoint.isdigit():
                self._uid_buffer = None
                self.state = STATE_WAITING_SCAN
                self._key_buffer = codepoint
            return True

        if self.state == STATE_WAITING_SCAN:
            if key in (13, 271) and self._key_buffer.strip():
                self._uid_buffer = self._key_buffer.strip()
                self._key_buffer = ""
            elif codepoint and codepoint.isdigit():
                self._key_buffer += codepoint
            return True

        if self.state == STATE_PICK_SOURCE:
            if codepoint == "1":
                self.selected_source = "youtube"
                self.state = STATE_INPUT_URL
                self._show_text_input("Enter YouTube URL:")
            elif codepoint == "2":
                self.selected_source = "local"
                self._scan_local_files()
                self.state = STATE_PICK_FILE
            return True

        if self.state == STATE_INPUT_URL:
            if key in (13, 271):
                text = self._get_input_text()
                if text.strip():
                    url = text.strip()
                    self._remove_text_input()
                    self.state = STATE_DOWNLOADING
                    threading.Thread(target=self._download_youtube, args=(url,), daemon=True).start()
            return True

        if self.state == STATE_PICK_FILE:
            if key == 273 and self.file_selected > 0:  # UP
                self.file_selected -= 1
            elif key == 274 and self.file_selected < len(self.local_files) - 1:  # DOWN
                self.file_selected += 1
            elif key in (13, 271) and 0 <= self.file_selected < len(self.local_files):
                filename = self.local_files[self.file_selected]
                self.reg_url = filename
                title = self._get_title_from_file(filename)
                preset = title if title else os.path.splitext(filename)[0]
                self.state = STATE_INPUT_TITLE
                self._show_text_input("Enter Song Title:", preset)
            return True

        if self.state == STATE_INPUT_TITLE:
            if key in (13, 271):
                text = self._get_input_text()
                if text.strip():
                    self.reg_title = text.strip()
                    self._remove_text_input()
                    self.state = STATE_CONFIRM
            return True

        if self.state == STATE_CONFIRM:
            if key in (13, 271) or codepoint == "y":
                self.registry.register_card(self.scanned_uid, self.reg_title, self.reg_url)
                self.toast_msg = f"Registered: {self.reg_title}"
                self.toast_end = _time.time() + 3
                self.state = STATE_DONE
            elif codepoint == "n":
                self._reset()
                self.state = STATE_HOME
            return True

        if self.state == STATE_DONE:
            if key in (13, 271):
                self._reset()
                self.state = STATE_HOME
            return True

        return False

    def handle_touch_down(self, touch):
        mx, my = touch.pos
        w, h = self.width, self.height
        cx = w // 2

        if self.state == STATE_HOME:
            if self._hit_btn(cx, h - 280, 260, 50, mx, my):
                self.state = STATE_WAITING_SCAN
                return True
            if self._hit_btn(cx, h - 350, 260, 50, mx, my):
                self._load_cards_list()
                self.state = STATE_LIST
                return True

        elif self.state == STATE_PICK_SOURCE:
            if self._hit_btn(cx, h - 300, 300, 50, mx, my):
                self.selected_source = "youtube"
                self.state = STATE_INPUT_URL
                self._show_text_input("Enter YouTube URL:")
                return True
            if self._hit_btn(cx, h - 370, 300, 50, mx, my):
                self.selected_source = "local"
                self._scan_local_files()
                self.state = STATE_PICK_FILE
                return True

        elif self.state == STATE_PICK_FILE:
            list_x = cx - 220
            list_y_start = h - 200
            item_h = 40
            visible = min(10, len(self.local_files) - self.file_scroll)
            for i in range(visible):
                idx = self.file_scroll + i
                iy = list_y_start - i * item_h
                if list_x < mx < list_x + 440 and iy - item_h < my < iy:
                    self.file_selected = idx
                    filename = self.local_files[idx]
                    self.reg_url = filename
                    title = self._get_title_from_file(filename)
                    preset = title if title else os.path.splitext(filename)[0]
                    self.state = STATE_INPUT_TITLE
                    self._show_text_input("Enter Song Title:", preset)
                    return True

        elif self.state == STATE_LIST:
            list_x = cx - 280
            list_y_start = self.height - 180
            item_h = 48
            visible = min(9, len(self.cards_list) - self.cards_scroll)
            for i in range(visible):
                idx = self.cards_scroll + i
                if idx >= len(self.cards_list):
                    break
                iy = list_y_start - i * item_h
                del_x = list_x + 520
                if del_x < mx < del_x + 40 and iy - item_h < my < iy:
                    uid = self.cards_list[idx]["uid"]
                    self.registry.delete_card(uid)
                    self.toast_msg = f"Deleted card {uid[:6]}..."
                    self.toast_end = _time.time() + 3
                    self._load_cards_list()
                    return True

        return False

    def handle_scroll(self, direction):
        if self.state == STATE_PICK_FILE:
            self.file_scroll = max(0, self.file_scroll - direction * 3)
            self.file_scroll = min(self.file_scroll, max(0, len(self.local_files) - 10))
        elif self.state == STATE_LIST:
            self.cards_scroll = max(0, self.cards_scroll - direction * 3)
            self.cards_scroll = min(self.cards_scroll, max(0, len(self.cards_list) - 9))

    # ── text input management ────────────────────────────────────────────────
    def _show_text_input(self, label, preset=""):
        self._remove_text_input()
        self._input_label = label
        ti = TextInput(
            text=preset,
            multiline=False,
            size_hint=(None, None),
            size=(min(600, self.width - 100), 44),
            pos=(self.width // 2 - min(300, (self.width - 100) // 2), self.height // 2 - 22),
            font_size=20,
            background_color=(C.GLASS_BG[0], C.GLASS_BG[1], C.GLASS_BG[2], 0.8),
            foreground_color=C.TEXT,
            cursor_color=C.CYAN,
            padding=[14, 10],
        )
        self._text_input = ti
        self.add_widget(ti)
        ti.focus = True

    def _get_input_text(self):
        if self._text_input:
            return self._text_input.text
        return ""

    def _remove_text_input(self):
        if self._text_input:
            self.remove_widget(self._text_input)
            self._text_input = None

    # ── helpers ──────────────────────────────────────────────────────────────
    def _hit_btn(self, cx, cy, bw, bh, mx, my):
        bx = cx - bw // 2
        by = cy - bh // 2
        return bx < mx < bx + bw and by < my < by + bh

    def _reset(self):
        self.scanned_uid = None
        self.existing_card = None
        self.selected_source = None
        self.reg_title = ""
        self.reg_url = ""
        self._uid_buffer = None
        self._key_buffer = ""
        self._remove_text_input()

    def _scan_local_files(self):
        folder = config.music_folder
        if not os.path.isdir(folder):
            self.local_files = []
            return
        self.local_files = sorted(
            f for f in os.listdir(folder)
            if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
        )
        self.file_scroll = 0
        self.file_selected = 0 if self.local_files else -1

    def _load_cards_list(self):
        import sqlite3
        try:
            with sqlite3.connect(self.registry.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT uid, title, youtube_url FROM cards ORDER BY date_added DESC")
                self.cards_list = [
                    {"uid": r[0], "title": r[1], "url": r[2]} for r in cursor.fetchall()
                ]
        except Exception:
            self.cards_list = []
        self.cards_scroll = 0

    def _card_count(self):
        import sqlite3
        try:
            with sqlite3.connect(self.registry.db_path) as conn:
                return conn.cursor().execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        except Exception:
            return 0

    def _get_title_from_file(self, filename):
        import subprocess
        import shutil
        import json

        file_path = os.path.join(config.music_folder, filename)
        base_path = os.path.splitext(file_path)[0]
        json_path = base_path + ".info.json"
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                title = data.get("title")
                artist = data.get("artist") or data.get("uploader") or data.get("channel")
                if title and artist:
                    return f"{artist} - {title}"
                elif title:
                    return title
            except Exception:
                pass

        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return None
        cmd = [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", file_path]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, **get_subprocess_flags())
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                tags = {k.lower(): v for k, v in data.get("format", {}).get("tags", {}).items()}
                title = tags.get("title")
                artist = tags.get("artist")
                if title and artist:
                    return f"{artist} - {title}"
                elif title:
                    return title
        except Exception:
            pass
        return None

    def _download_youtube(self, url):
        import subprocess
        import shutil

        self.download_progress = "Starting download..."
        ytdlp = shutil.which("yt-dlp") or "yt-dlp"

        music_dir = config.music_folder
        os.makedirs(music_dir, exist_ok=True)
        before = set(os.listdir(music_dir))

        cmd = [
            ytdlp,
            "-f", "bestaudio/best",
            "--no-playlist",
            "--write-thumbnail",
            "--write-info-json",
            "-o", os.path.join(music_dir, "%(title)s.%(ext)s"),
            url,
        ]

        cookies_file = config.ytdlp_cookies_file
        cookies_browser = config.ytdlp_cookies_browser
        if cookies_file and os.path.exists(cookies_file):
            cmd.extend(["--cookies", cookies_file])
        elif cookies_browser:
            cmd.extend(["--cookies-from-browser", cookies_browser])

        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                **get_subprocess_flags(),
            )
            for line in iter(proc.stdout.readline, ""):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("[download]"):
                    self.download_progress = line[:60]
                elif line.startswith("[info]"):
                    self.download_progress = "Fetching metadata..."
                else:
                    self.download_progress = "Processing..."

            proc.wait()

            after = set(os.listdir(music_dir))
            new_files = after - before
            audio_exts = {".webm", ".mp3", ".m4a", ".opus", ".ogg", ".wav", ".flac", ".aac", ".wma", ".mp4"}
            audio_file = None
            for f in new_files:
                if os.path.splitext(f)[1].lower() in audio_exts:
                    audio_file = f
                    break

            if not audio_file:
                candidates = []
                for f in os.listdir(music_dir):
                    if os.path.splitext(f)[1].lower() in audio_exts:
                        full = os.path.join(music_dir, f)
                        candidates.append((os.path.getmtime(full), f))
                if candidates:
                    candidates.sort(reverse=True)
                    audio_file = candidates[0][1]

            if audio_file:
                self.reg_url = audio_file
                title = self._get_title_from_file(audio_file)
                preset = title if title else os.path.splitext(audio_file)[0]
                self.state = STATE_INPUT_TITLE
                Clock.schedule_once(lambda dt: self._show_text_input("Enter Song Title:", preset), 0)
            else:
                self.toast_msg = "Download failed — no audio file found."
                self.toast_end = _time.time() + 3
                self.state = STATE_PICK_SOURCE
        except Exception as e:
            self.toast_msg = f"Error: {e}"
            self.toast_end = _time.time() + 3
            self.state = STATE_PICK_SOURCE

    # ── drawing ──────────────────────────────────────────────────────────────
    def _redraw(self):
        self.canvas.before.clear()
        w, h = self.width, self.height
        if w < 10 or h < 10:
            return

        with self.canvas.before:
            # Background
            Color(*C.BG)
            Rectangle(pos=self.pos, size=self.size)

            # Gradient blobs
            cx, cy = int(w * 0.2), int(h * 0.5)
            for r_step in range(0, min(w, h) // 2, 20):
                r = min(w, h) // 2 - r_step
                alpha = max(0, 0.14 * (1 - r_step / (min(w, h) // 2)))
                Color(*C.BG_INDIGO[:3], alpha)
                Ellipse(pos=(cx - r, cy - r), size=(r * 2, r * 2))

            cx2, cy2 = int(w * 0.85), int(h * 0.35)
            for r_step in range(0, min(w, h) // 3, 25):
                r = min(w, h) // 3 - r_step
                alpha = max(0, 0.10 * (1 - r_step / (min(w, h) // 3)))
                Color(*C.BG_VIOLET[:3], alpha)
                Ellipse(pos=(cx2 - r, cy2 - r), size=(r * 2, r * 2))

            # Scanlines
            Color(0, 0, 0, 0.12)
            for y in range(0, int(h), 4):
                Rectangle(pos=(0, y), size=(w, 1))

            # Header
            self._draw_header(w, h)

            # State content
            if self.state == STATE_HOME:
                self._draw_home(w, h)
            elif self.state == STATE_WAITING_SCAN:
                self._draw_waiting_scan(w, h)
            elif self.state == STATE_PICK_SOURCE:
                self._draw_pick_source(w, h)
            elif self.state == STATE_INPUT_URL:
                self._draw_input_screen(w, h, "Enter YouTube URL:")
            elif self.state == STATE_DOWNLOADING:
                self._draw_downloading(w, h)
            elif self.state == STATE_PICK_FILE:
                self._draw_pick_file(w, h)
            elif self.state == STATE_INPUT_TITLE:
                self._draw_input_screen(w, h, "Enter Song Title:")
            elif self.state == STATE_CONFIRM:
                self._draw_confirm(w, h)
            elif self.state == STATE_DONE:
                self._draw_done(w, h)
            elif self.state == STATE_LIST:
                self._draw_cards_list(w, h)

            # Toast
            now = _time.time()
            if now < self.toast_end and self.toast_msg:
                alpha = min(1.0, (self.toast_end - now) / 0.5)
                tw = len(self.toast_msg) * 10 + 48
                tx = (w - tw) // 2
                Color(*C.GLASS_BG[:3], alpha * 0.8)
                RoundedRectangle(pos=(tx, 60), size=(tw, 44), radius=[12])
                Color(*C.GREEN[:3], alpha * 0.3)
                Line(rounded_rectangle=(tx, 60, tw, 44, 12), width=1)
                self._text(self.toast_msg, tx + 24, 72, font_size=18, color=_rgba(C.TEXT, alpha))

            # Bottom hint
            Color(1, 1, 1, 0.06)
            Rectangle(pos=(0, 40), size=(w, 1))
            self._text("[ESC] Back / Exit", 28, 12, font_size=14, color=C.TEXT_MUTED)

    def _draw_header(self, w, h):
        self._text("uTune", 28, h - 50, font_size=34, color=C.CYAN, bold=True)
        self._text("CARD REGISTRATION", 160, h - 42, font_size=14, color=_rgba(C.VIOLET, 0.7), bold=True)
        Color(1, 1, 1, 0.08)
        Rectangle(pos=(28, h - 70), size=(w - 56, 1))

    def _draw_home(self, w, h):
        cx = w // 2
        self._text("Card Manager", cx - 70, h - 140, font_size=24, color=C.TEXT, bold=True)
        self._text(f"Cards registered: {self._card_count()}", cx - 80, h - 175, font_size=18, color=C.TEXT_SEC)
        self._draw_button(cx, h - 280, 260, 50, "Register New Card", C.CYAN)
        self._draw_button(cx, h - 350, 260, 50, "View All Cards", C.VIOLET)

    def _draw_waiting_scan(self, w, h):
        cx = w // 2
        cy = h // 2 + 20

        # Pulsing ring
        radius = 50 + int(5 * math.sin(self.pulse * 2))
        ring_a = 0.47 + 0.24 * math.sin(self.pulse * 3)
        Color(*C.CYAN[:3], ring_a)
        Line(circle=(cx, cy, radius), width=2)

        inner_a = 0.16 + 0.08 * math.sin(self.pulse * 2 + 1)
        Color(*C.CYAN[:3], inner_a)
        Ellipse(pos=(cx - 28, cy - 28), size=(56, 56))

        self._text("Scan RFID Card", cx - 80, cy - radius - 40, font_size=24, color=C.TEXT, bold=True)
        self._text("Place card on the reader...", cx - 110, cy - radius - 70, font_size=18, color=C.TEXT_MUTED)

    def _draw_pick_source(self, w, h):
        cx = w // 2
        self._text(f"Card UID: {self.scanned_uid}", cx - 100, h - 130, font_size=16, color=C.CYAN)

        if self.existing_card:
            self._text(
                f"Already registered: {self.existing_card['title']}", cx - 160, h - 160,
                font_size=18, color=C.ORANGE,
            )
            self._text("Continuing will overwrite", cx - 90, h - 185, font_size=14, color=C.TEXT_MUTED)

        self._text("Choose Audio Source", cx - 100, h - 230, font_size=24, color=C.TEXT, bold=True)
        self._draw_button(cx, h - 300, 300, 50, "[1] YouTube URL", C.CYAN)
        self._draw_button(cx, h - 370, 300, 50, "[2] Local File", C.VIOLET)

    def _draw_input_screen(self, w, h, label):
        cx = w // 2
        self._text(label, cx - 100, h - 170, font_size=24, color=C.TEXT, bold=True)
        self._text("Press ENTER to confirm  •  ESC to cancel", cx - 170, h // 2 - 60, font_size=14, color=C.TEXT_MUTED)

    def _draw_downloading(self, w, h):
        cx = w // 2
        cy = h // 2

        # Spinner
        radius = 40
        for i in range(20):
            a = (self.pulse * 5 + (math.pi * 2 * i / 20)) % (math.pi * 2)
            sx = cx + int(radius * math.cos(a))
            sy = cy + 40 + int(radius * math.sin(a))
            dot_a = 0.3 + 0.7 * (i / 20)
            Color(*C.CYAN[:3], dot_a)
            Ellipse(pos=(sx - 3, sy - 3), size=(6, 6))

        self._text("Downloading Audio...", cx - 100, cy - 30, font_size=24, color=C.TEXT, bold=True)
        self._text(self.download_progress, cx - 150, cy - 60, font_size=18, color=C.TEXT_SEC)
        self._text("Please wait...", cx - 50, cy - 90, font_size=14, color=C.TEXT_MUTED)

    def _draw_pick_file(self, w, h):
        cx = w // 2
        self._text("Select Audio File", cx - 90, h - 130, font_size=24, color=C.TEXT, bold=True)
        self._text(f"Folder: {config.music_folder}", cx - 180, h - 160, font_size=14, color=C.TEXT_MUTED)

        if not self.local_files:
            self._text("No audio files found in music folder", cx - 150, h // 2, font_size=18, color=C.RED)
            return

        list_x = cx - 220
        list_y = h - 200
        item_h = 40
        visible = min(10, len(self.local_files) - self.file_scroll)

        for i in range(visible):
            idx = self.file_scroll + i
            iy = list_y - i * item_h
            is_sel = idx == self.file_selected

            bg_a = 0.55 if is_sel else 0.24
            Color(*C.GLASS_BG[:3], bg_a)
            RoundedRectangle(pos=(list_x, iy - item_h + 4), size=(440, item_h - 4), radius=[6])

            border_c = C.CYAN if is_sel else C.VIOLET
            border_a = 0.3 if is_sel else 0.12
            Color(*border_c[:3], border_a)
            Line(rounded_rectangle=(list_x, iy - item_h + 4, 440, item_h - 4, 6), width=1)

            ext = os.path.splitext(self.local_files[idx])[1].upper()
            self._text(ext, list_x + 10, iy - item_h + 14, font_size=13, color=C.CYAN if is_sel else C.TEXT_MUTED)

            fname = self.local_files[idx]
            if len(fname) > 40:
                fname = fname[:37] + "..."
            self._text(fname, list_x + 60, iy - item_h + 14, font_size=15, color=C.TEXT if is_sel else C.TEXT_SEC)

        self._text("↑↓ Navigate  •  ENTER select  •  Click to pick", cx - 180, list_y - visible * item_h - 10, font_size=14, color=C.TEXT_MUTED)

    def _draw_confirm(self, w, h):
        cx = w // 2
        self._text("Confirm Registration", cx - 100, h - 160, font_size=24, color=C.TEXT, bold=True)

        card_w = min(500, w - 80)
        card_x = cx - card_w // 2
        card_y = h // 2 - 40

        Color(*C.GLASS_BG[:3], 0.55)
        RoundedRectangle(pos=(card_x, card_y), size=(card_w, 160), radius=[12])
        Color(*C.CYAN[:3], 0.2)
        Line(rounded_rectangle=(card_x, card_y, card_w, 160, 12), width=1)

        fields = [
            ("UID", self.scanned_uid or ""),
            ("TITLE", self.reg_title),
            ("SOURCE", self.reg_url[:50]),
            ("TYPE", (self.selected_source or "").upper()),
        ]
        fy = card_y + 125
        for label, value in fields:
            self._text(label, card_x + 20, fy, font_size=13, color=C.VIOLET, bold=True)
            self._text(value, card_x + 100, fy, font_size=18, color=C.TEXT)
            fy -= 34

        self._text("Press Y or ENTER to confirm  •  N to cancel", cx - 180, card_y - 30, font_size=18, color=C.TEXT_SEC)

    def _draw_done(self, w, h):
        cx = w // 2
        cy = h // 2 + 20

        Color(*C.GREEN[:3], 0.16)
        Ellipse(pos=(cx - 38, cy - 38), size=(76, 76))
        Color(*C.GREEN[:3], 0.7)
        Line(circle=(cx, cy, 38), width=2)

        # Checkmark (two line segments)
        Color(*C.GREEN)
        Line(points=[cx - 12, cy - 2, cx - 2, cy - 12, cx + 16, cy + 10], width=2)

        self._text("Card Registered!", cx - 85, cy - 60, font_size=24, color=C.GREEN, bold=True)
        self._text(self.reg_title, cx - 80, cy - 90, font_size=18, color=C.TEXT)
        self._text("Press ENTER to register another  •  ESC to exit", cx - 200, cy - 130, font_size=14, color=C.TEXT_MUTED)

    def _draw_cards_list(self, w, h):
        cx = w // 2
        self._text("Registered Cards", cx - 85, h - 120, font_size=24, color=C.TEXT, bold=True)
        self._text(f"{len(self.cards_list)} cards", cx - 30, h - 148, font_size=14, color=C.TEXT_MUTED)

        if not self.cards_list:
            self._text("No cards registered yet", cx - 90, h // 2, font_size=18, color=C.TEXT_MUTED)
            return

        list_x = cx - 280
        list_y = h - 180
        item_h = 48
        visible = min(9, len(self.cards_list) - self.cards_scroll)

        for i in range(visible):
            idx = self.cards_scroll + i
            if idx >= len(self.cards_list):
                break
            card = self.cards_list[idx]
            iy = list_y - i * item_h

            Color(*C.GLASS_BG[:3], 0.31)
            RoundedRectangle(pos=(list_x, iy - item_h + 4), size=(560, item_h - 4), radius=[6])
            Color(*C.VIOLET[:3], 0.1)
            Line(rounded_rectangle=(list_x, iy - item_h + 4, 560, item_h - 4, 6), width=1)

            self._text(card["uid"][:12], list_x + 10, iy - item_h + 16, font_size=13, color=C.CYAN)

            title_text = card["title"]
            if len(title_text) > 28:
                title_text = title_text[:25] + "..."
            self._text(title_text, list_x + 140, iy - item_h + 16, font_size=18, color=C.TEXT)

            is_url = card["url"].startswith("http")
            src = "YT" if is_url else "LOCAL"
            src_c = C.CYAN if is_url else C.VIOLET
            self._text(src, list_x + 460, iy - item_h + 16, font_size=13, color=src_c)

            # Delete button
            dx = list_x + 520
            dy = iy - item_h + 10
            Color(*C.RED[:3], 0.2)
            RoundedRectangle(pos=(dx, dy), size=(32, 28), radius=[4])
            Color(*C.RED[:3], 0.6)
            Line(rounded_rectangle=(dx, dy, 32, 28, 4), width=1)
            self._text("X", dx + 10, dy + 5, font_size=16, color=C.RED, bold=True)

    # ── drawing helpers ──────────────────────────────────────────────────────
    def _draw_button(self, cx, cy, bw, bh, text, color):
        bx = cx - bw // 2
        by = cy - bh // 2
        Color(*C.GLASS_BG[:3], 0.55)
        RoundedRectangle(pos=(bx, by), size=(bw, bh), radius=[10])
        Color(*color[:3], 0.4)
        Line(rounded_rectangle=(bx, by, bw, bh, 10), width=1)
        # Top highlight
        Color(*color[:3], 0.15)
        Rectangle(pos=(bx, by + bh - 1), size=(bw, 1))
        self._text(text, cx - len(text) * 5, cy - 8, font_size=16, color=color, bold=True)

    def _text(self, text, x, y, font_size=16, color=None, bold=False):
        if color is None:
            color = C.TEXT
        cl = CoreLabel(
            text=str(text), font_size=font_size, bold=bold,
            color=color,
        )
        cl.refresh()
        tex = cl.texture
        if tex:
            Color(1, 1, 1, color[3] if len(color) > 3 else 1)
            Rectangle(texture=tex, pos=(int(x), int(y)), size=tex.size)


class RegisterApp(App):
    def build(self):
        from kivy.core.window import Window
        Window.size = (1024, 600)
        self.title = "uTune Card Registry"
        
        self.ui = RegisterUI()
        Window.bind(on_key_down=self.ui.handle_key_down)
        Window.bind(on_touch_down=self.ui.handle_touch_down)
        
        # Scroll wheel handling
        def on_scroll(window, key, *args):
            if key == 273: # up
                self.ui.handle_scroll(-1)
            elif key == 274: # down
                self.ui.handle_scroll(1)
        Window.bind(on_key_down=on_scroll)
        
        self.ui.start()
        return self.ui

    def on_pause(self):
        if hasattr(self.ui, 'reader'):
            self.ui.reader.disable_nfc_foreground()
        return True

    def on_resume(self):
        if hasattr(self.ui, 'reader'):
            self.ui.reader.enable_nfc_foreground()

    def on_stop(self):
        self.ui.stop_ui()


def main():
    RegisterApp().run()


if __name__ == '__main__':
    main()
