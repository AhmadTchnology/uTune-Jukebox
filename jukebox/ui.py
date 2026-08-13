"""uTune Jukebox UI — Kivy implementation.

Recreates the premium dark-space design from the original Pygame version:
  - Deep space background with radial gradient blobs
  - Scanline + grid overlay
  - Glass-morphism cards with cyan/violet accents
  - Album art with rounded corners
  - Progress bar, toast notifications, NFC tap flash
  - Queue panel with drag-and-drop reordering
"""
import math
import time as _time
import io

import os
import threading
from kivy.uix.textinput import TextInput
from player import AUDIO_EXTENSIONS
from platform_utils import get_subprocess_flags

from kivy.uix.floatlayout import FloatLayout
from kivy.uix.widget import Widget
from kivy.uix.label import Label
from kivy.graphics import (
    Color, Rectangle, RoundedRectangle, Ellipse, Line,
)
from kivy.graphics.texture import Texture
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.metrics import sp, dp
from kivy.properties import (
    BooleanProperty, StringProperty, NumericProperty, ListProperty,
)


# ── Colour palette (Kivy 0‑1 floats) ────────────────────────────────────────
def _c(r, g, b, a=255):
    return (r / 255, g / 255, b / 255, a / 255)



# Registration States
REG_STATE_HOME         = "home"
REG_STATE_WAITING_SCAN = "waiting_scan"
REG_STATE_PICK_SOURCE  = "pick_source"
REG_STATE_INPUT_URL    = "input_url"
REG_STATE_DOWNLOADING  = "downloading"
REG_STATE_PICK_FILE    = "pick_file"
REG_STATE_INPUT_TITLE  = "input_title"
REG_STATE_CONFIRM      = "confirm"
REG_STATE_DONE         = "done"
REG_STATE_LIST         = "list"

class C:
    BG          = _c(6, 8, 16)
    BG_INDIGO   = _c(18, 16, 58)
    BG_VIOLET   = _c(26, 11, 46)
    CYAN        = _c(34, 211, 238)
    VIOLET      = _c(139, 92, 246)
    VIOLET_DIM  = _c(109, 40, 217)
    GLASS_BG    = _c(30, 27, 75)
    TEXT        = _c(241, 245, 249)
    TEXT_SEC     = _c(148, 163, 184)
    TEXT_MUTED   = _c(71, 85, 105)
    TEXT_SLATE   = _c(226, 232, 240)
    TEXT_DIM     = _c(100, 116, 139)


# ── Helpers ──────────────────────────────────────────────────────────────────
def _rgba(c, alpha=None):
    if alpha is not None:
        return (c[0], c[1], c[2], alpha)
    return c


def _make_gradient_texture(w, h, color_top, color_bot):
    """Create a vertical gradient texture."""
    buf = bytearray(w * h * 4)
    for row in range(h):
        t = row / max(h - 1, 1)
        r = int((color_top[0] * (1 - t) + color_bot[0] * t) * 255)
        g = int((color_top[1] * (1 - t) + color_bot[1] * t) * 255)
        b = int((color_top[2] * (1 - t) + color_bot[2] * t) * 255)
        a = int((color_top[3] * (1 - t) + color_bot[3] * t) * 255)
        for col in range(w):
            idx = (row * w + col) * 4
            buf[idx] = r
            buf[idx + 1] = g
            buf[idx + 2] = b
            buf[idx + 3] = a
    tex = Texture.create(size=(w, h), colorfmt="rgba")
    tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
    tex.flip_vertical()
    return tex


def _load_image_bytes(raw_bytes, size):
    """Load raw image bytes into a Kivy Texture, cropped square & scaled."""
    try:
        cimg = CoreImage(io.BytesIO(raw_bytes), ext="jpg")
        tex = cimg.texture
        if tex is None:
            # Try png
            cimg = CoreImage(io.BytesIO(raw_bytes), ext="png")
            tex = cimg.texture
        return tex
    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════════════════
#  Main UI Widget
# ═════════════════════════════════════════════════════════════════════════════
class UI(FloatLayout):
    """Root widget for the jukebox interface."""

    toast_text = StringProperty("")
    toast_alpha = NumericProperty(0)

    def __init__(self, config, queue_mgr, player, on_scan=None, **kwargs):
        super().__init__(**kwargs)
        self.config = config
        self.queue_mgr = queue_mgr
        self.player = player
        self.on_scan = on_scan
        self.running = False

        self._key_buffer = ""
        self.pulse_phase = 0.0
        self.idle_bob = 0.0
        self.tap_count = 0

        self.toast_message = ""
        self.toast_end = 0

        self.flash_active = False
        self.flash_start = 0
        self.flash_duration = 1.4

        self.status_message = ""
        self.status_end = 0

        self._art_cache_key = None
        self._art_texture = None
        self._mini_cache = {}

        self.player.on_status_change = self._on_player_status

        # Merged page state
        self.page = "player"  # "player" or "register"
        self.reg_state = REG_STATE_HOME
        
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

        # Text input widget (added/removed dynamically)
        self._text_input = None
        self._input_label = ""


        # Pre-build background textures once
        self._bg_tex = None
        self._scanline_tex = None
        self.bind(size=self._on_resize)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self):
        self.running = True
        fps = self.config.ui_fps
        Clock.schedule_interval(self._tick, 1.0 / fps)

    def stop_ui(self):
        self.running = False
        Clock.unschedule(self._tick)

    def _on_resize(self, *_args):
        self._bg_tex = None  # Force rebuild

    def _tick(self, dt):
        if not self.running:
            return
        self.pulse_phase += dt * 2.0
        self.idle_bob += dt * 0.8
        now = _time.time()

        # Toast fade
        if now < self.toast_end:
            self.toast_alpha = min(1.0, self.toast_alpha + dt * 3)
        else:
            self.toast_alpha = max(0, self.toast_alpha - dt * 2)

        # Flash timeout
        if self.flash_active and (now - self.flash_start) > self.flash_duration:
            self.flash_active = False

        self._redraw()

    # ── public API ───────────────────────────────────────────────────────────
    def show_toast(self, message, duration=3.0):
        self.toast_message = message
        self.toast_end = _time.time() + duration
        self.toast_alpha = 1.0
        self.flash_active = True
        self.flash_start = _time.time()
        self.tap_count += 1

    def _on_player_status(self, msg):
        self.status_message = msg
        self.status_end = _time.time() + 5


    def handle_scan(self, uid):
        if self.page == 'player':
            if self.on_scan:
                self.on_scan(uid)
        elif self.page == 'register':
            if self.reg_state == REG_STATE_WAITING_SCAN:
                self.scanned_uid = str(uid)
                from registry import Registry
                r = Registry(self.config.db_path)
                self.existing_card = r.get_card(self.scanned_uid)
                self.reg_state = REG_STATE_PICK_SOURCE

    # ── keyboard (RFID via USB OTG keyboard emulation) ───────────────────────

    def handle_key_down(self, window, key, scancode, codepoint, modifiers):
        """Called from the Kivy App's on_key_down."""
        if self.page == "player":
            if key == 27:  # ESC
                return False  # Let App handle quit
            if key == 114 or codepoint == 'r':  # 'r' - switch to register
                self.page = "register"
                self.reg_state = REG_STATE_HOME
                self._remove_text_input()
                return True
            if key == 115 or codepoint == 's':  # 's' — skip
                self.player.skip()
                return True
            if codepoint and codepoint.isdigit():
                self._key_buffer += codepoint
                return True
            if key in (13, 271):  # Enter / Numpad Enter
                if self._key_buffer and self.on_scan:
                    self.on_scan(self._key_buffer)
                self._key_buffer = ""
                return True
            return False
            
        elif self.page == "register":
            if key == 27:  # ESC
                if self.reg_state in (REG_STATE_HOME, REG_STATE_DONE):
                    self.page = "player"  # Go back to player instead of exit
                    self._remove_text_input()
                    return True
                self._reset_reg()
                self.reg_state = REG_STATE_HOME
                self._remove_text_input()
                return True

            if self.reg_state == REG_STATE_HOME:
                if codepoint and codepoint.isdigit():
                    self._key_buffer = codepoint
                    self.reg_state = REG_STATE_WAITING_SCAN
                return True

            if self.reg_state == REG_STATE_WAITING_SCAN:
                if key in (13, 271) and self._key_buffer.strip():
                    if self.on_scan:
                        self.on_scan(self._key_buffer.strip())
                    self._key_buffer = ""
                elif codepoint and codepoint.isdigit():
                    self._key_buffer += codepoint
                return True

            if self.reg_state == REG_STATE_PICK_SOURCE:
                if codepoint == "1":
                    self.selected_source = "youtube"
                    self.reg_state = REG_STATE_INPUT_URL
                    self._show_text_input("Enter YouTube URL:")
                elif codepoint == "2":
                    self.selected_source = "local"
                    self._scan_local_files()
                    self.reg_state = REG_STATE_PICK_FILE
                return True

            if self.reg_state == REG_STATE_INPUT_URL:
                if key in (13, 271):
                    text = self._get_input_text()
                    if text.strip():
                        url = text.strip()
                        self._remove_text_input()
                        self.reg_state = REG_STATE_DOWNLOADING
                        threading.Thread(target=self._download_youtube, args=(url,), daemon=True).start()
                return True

            if self.reg_state == REG_STATE_PICK_FILE:
                if key == 273 and self.file_selected > 0:  # UP
                    self.file_selected -= 1
                elif key == 274 and self.file_selected < len(self.local_files) - 1:  # DOWN
                    self.file_selected += 1
                elif key in (13, 271) and 0 <= self.file_selected < len(self.local_files):
                    filename = self.local_files[self.file_selected]
                    self.reg_url = filename
                    title = self._get_title_from_file(filename)
                    preset = title if title else os.path.splitext(filename)[0]
                    self.reg_state = REG_STATE_INPUT_TITLE
                    self._show_text_input("Enter Song Title:", preset)
                return True

            if self.reg_state == REG_STATE_INPUT_TITLE:
                if key in (13, 271):
                    text = self._get_input_text()
                    if text.strip():
                        self.reg_title = text.strip()
                        self._remove_text_input()
                        self.reg_state = REG_STATE_CONFIRM
                return True

            if self.reg_state == REG_STATE_CONFIRM:
                if key in (13, 271) or codepoint == "y":
                    # Register!
                    # Need to get registry instance - config.db_path
                    from registry import Registry
                    registry = Registry(self.config.db_path)
                    registry.register_card(self.scanned_uid, self.reg_title, self.reg_url)
                    self.show_toast(f"Registered: {self.reg_title}")
                    self.reg_state = REG_STATE_DONE
                elif codepoint == "n":
                    self._reset_reg()
                    self.reg_state = REG_STATE_HOME
                return True

            if self.reg_state == REG_STATE_DONE:
                if key in (13, 271):
                    self._reset_reg()
                    self.reg_state = REG_STATE_HOME
                return True

            return False
    # ── drawing ──────────────────────────────────────────────────────────────
    def _redraw(self):
        self.canvas.clear()
        w, h = self.width, self.height
        if w < 10 or h < 10:
            return

        pad = int(dp(20))
        right_w = int(dp(240))
        divider_x = w - right_w - pad
        left_w = divider_x - pad

        with self.canvas:
            # 1 — Background
            Color(*C.BG)
            Rectangle(pos=self.pos, size=self.size)
            self._draw_bg_effects(w, h)

            # 2 — Flash overlay
            if self.flash_active:
                self._draw_flash(w, h)

            if self.page == "player":
                # 3 — Left panel: Now Playing
                self._draw_now_playing(pad, pad, left_w, h)

                # 4 — Divider
                Color(1, 1, 1, 0.06)
                Rectangle(pos=(divider_x, pad), size=(1, h - pad * 2 - int(dp(36))))

                # 5 — Right panel: Queue
                self._draw_queue(divider_x + int(dp(18)), pad, right_w - int(dp(18)), h)

                # 6 — Bottom bar
                self._draw_bottom_bar(w, h)
            else:
                self._draw_reg_header(w, h)
                if self.reg_state == REG_STATE_HOME:
                    self._draw_reg_home(w, h)
                elif self.reg_state == REG_STATE_WAITING_SCAN:
                    self._draw_reg_waiting_scan(w, h)
                elif self.reg_state == REG_STATE_PICK_SOURCE:
                    self._draw_reg_pick_source(w, h)
                elif self.reg_state == REG_STATE_INPUT_URL:
                    self._draw_reg_input_screen(w, h, "Enter YouTube URL:")
                elif self.reg_state == REG_STATE_DOWNLOADING:
                    self._draw_reg_downloading(w, h)
                elif self.reg_state == REG_STATE_PICK_FILE:
                    self._draw_reg_pick_file(w, h)
                elif self.reg_state == REG_STATE_INPUT_TITLE:
                    self._draw_reg_input_screen(w, h, "Enter Song Title:")
                elif self.reg_state == REG_STATE_CONFIRM:
                    self._draw_reg_confirm(w, h)
                elif self.reg_state == REG_STATE_DONE:
                    self._draw_reg_done(w, h)
                elif self.reg_state == REG_STATE_LIST:
                    self._draw_reg_cards_list(w, h)

            # 7 — Toast
            if self.toast_alpha > 0.01:
                self._draw_toast(w, h)

    # ── background effects ───────────────────────────────────────────────────
    def _draw_bg_effects(self, w, h):
        # Indigo blob at 20%, 50%
        cx, cy = int(w * 0.2), int(h * 0.5)
        for r_step in range(0, min(w, h) // 2, 20):
            r = min(w, h) // 2 - r_step
            alpha = max(0, 0.14 * (1 - r_step / (min(w, h) // 2)))
            Color(*C.BG_INDIGO[:3], alpha)
            Ellipse(pos=(cx - r, cy - r), size=(r * 2, r * 2))

        # Violet blob at 5%, 55%
        cx2, cy2 = int(w * 0.05), int(h * 0.45)
        for r_step in range(0, min(w, h) // 3, 25):
            r = min(w, h) // 3 - r_step
            alpha = max(0, 0.10 * (1 - r_step / (min(w, h) // 3)))
            Color(*C.BG_VIOLET[:3], alpha)
            Ellipse(pos=(cx2 - r, cy2 - r), size=(r * 2, r * 2))

        # Scanlines (horizontal lines every 4px)
        Color(0, 0, 0, 0.12)
        for y in range(0, int(h), 4):
            Rectangle(pos=(0, y), size=(w, 1))

    def _draw_flash(self, w, h):
        elapsed = _time.time() - self.flash_start
        progress = min(1.0, elapsed / self.flash_duration)
        if progress < 0.15:
            alpha = progress / 0.15
        elif progress < 0.60:
            alpha = 1.0 - (progress - 0.15) / 0.45 * 0.6
        else:
            alpha = 0.4 * (1.0 - (progress - 0.60) / 0.40)
        alpha = max(0, min(1.0, alpha)) * 0.15
        if alpha > 0.005:
            Color(*C.CYAN[:3], alpha)
            Rectangle(pos=(0, 0), size=(w, h // 2))

    # ── now playing ──────────────────────────────────────────────────────────
    def _draw_now_playing(self, x, y_pad, w, h):
        track = self.player.current_track
        top = h - y_pad

        # "NOW PLAYING" label with dot
        label_y = top - int(dp(16))
        Color(*C.CYAN[:3], 0.75)
        Ellipse(pos=(x, label_y - 2), size=(int(dp(6)), int(dp(6))))
        self._text(
            "NOW PLAYING", x + int(dp(12)), label_y,
            font_size=sp(18), color=_rgba(C.CYAN, 0.75), bold=True,
        )

        if track:
            self._draw_playing(x, label_y - int(dp(32)), w, track)
        else:
            self._draw_idle(x, y_pad, w, h)

    def _draw_playing(self, x, top_y, w, track):
        art_size = int(dp(160))
        art_x = x
        art_y = top_y - art_size

        # Album art
        self._draw_album_art(art_x, art_y, art_size, track)

        # Track info to the right of art
        info_x = art_x + art_size + int(dp(24))

        title = track.get("title", "Unknown Track")
        if len(title) > 24:
            title = title[:21] + "..."
        self._text(title, info_x, top_y - int(dp(8)), font_size=sp(48), color=C.TEXT, bold=True)

        artist = track.get("artist", "Unknown Artist")
        self._text(artist, info_x, top_y - int(dp(52)), font_size=sp(28), color=C.TEXT_SEC)

        album = track.get("album", "")
        offset = int(dp(68))
        if album:
            self._text(album.upper(), info_x, top_y - offset, font_size=sp(18), color=C.TEXT_MUTED)
            offset += int(dp(24))

        # Progress bar
        prog_w = w - (info_x - x) - int(dp(16))
        self._draw_progress_bar(info_x, top_y - offset - int(dp(8)), prog_w)

    def _draw_album_art(self, x, y, size, track):
        cache_key = f"{track.get('title', '?')}_{'img' if track.get('image_bytes') else 'no'}"
        if self._art_cache_key != cache_key:
            self._art_cache_key = cache_key
            img_bytes = track.get("image_bytes")
            if img_bytes:
                self._art_texture = _load_image_bytes(img_bytes, size)
            else:
                self._art_texture = None

        if self._art_texture:
            Color(1, 1, 1, 1)
            RoundedRectangle(
                texture=self._art_texture, pos=(x, y), size=(size, size), radius=[12],
            )
        else:
            # Gradient fallback with initial letter
            Color(*C.BG_INDIGO[:3], 0.9)
            RoundedRectangle(pos=(x, y), size=(size, size), radius=[12])
            Color(*C.VIOLET_DIM[:3], 0.3)
            RoundedRectangle(pos=(x, y), size=(size, size // 2), radius=[0, 0, 12, 12])
            initial = (track.get("title", "?"))[0].upper()
            self._text(
                initial, x + size // 2 - int(dp(16)), y + size // 2 - int(dp(24)),
                font_size=sp(80), color=(1, 1, 1, 0.2), bold=True,
            )

        # Border
        Color(*C.CYAN[:3], 0.3)
        Line(rounded_rectangle=(x, y, size, size, 12), width=1)

        # Glow pulse when playing
        if self.player.is_playing:
            glow_a = 0.15 + 0.1 * math.sin(self.pulse_phase * 2.0)
            Color(*C.CYAN[:3], glow_a)
            Line(rounded_rectangle=(x - 4, y - 4, size + 8, size + 8, 16), width=3.0)

    def _draw_progress_bar(self, x, y, w):
        track = self.player.current_track
        if not track:
            return

        bar_h = int(dp(4))

        if not self.player.play_start_time:
            # Loading indicator — pulsing bar
            Color(1, 1, 1, 0.1)
            RoundedRectangle(pos=(x, y), size=(w, bar_h), radius=[2])
            pulse_w = int(dp(48))
            pulse_x = int((w - pulse_w) * ((math.sin(self.pulse_phase * 4) + 1) / 2))
            Color(*C.CYAN)
            RoundedRectangle(pos=(x + pulse_x, y), size=(pulse_w, bar_h), radius=[2])
            self._text("Loading...", x, y - int(dp(24)), font_size=sp(24), color=C.CYAN)
            return

        elapsed = _time.time() - self.player.play_start_time
        duration = track.get("duration")
        pct = min(1.0, elapsed / float(duration)) if duration else 1.0

        # Background
        Color(1, 1, 1, 0.1)
        RoundedRectangle(pos=(x, y), size=(w, bar_h), radius=[2])
        # Fill
        fill_w = int(w * pct)
        if fill_w > 0:
            Color(*C.CYAN)
            RoundedRectangle(pos=(x, y), size=(fill_w, bar_h), radius=[2])
            # Playhead dot
            Color(1, 1, 1, 1)
            dot_sz = int(dp(10))
            Ellipse(pos=(x + fill_w - dot_sz // 2, y + bar_h // 2 - dot_sz // 2), size=(dot_sz, dot_sz))

        # Time labels
        mins, secs = int(elapsed) // 60, int(elapsed) % 60
        self._text(
            f"{mins}:{secs:02d}", x, y - int(dp(24)),
            font_size=sp(24), color=_rgba(C.CYAN, 0.8),
        )
        if duration:
            dur = float(duration)
            dm, ds = int(dur) // 60, int(dur) % 60
            self._text(
                f"{dm}:{ds:02d}", x + w - int(dp(40)), y - int(dp(20)),
                font_size=sp(22), color=C.TEXT_MUTED,
            )

    def _draw_idle(self, x, y_pad, w, h):
        cx = x + w // 2
        cy = h // 2

        # NFC card icon
        card_w, card_h_icon = int(dp(40)), int(dp(48))
        card_x, card_y = cx - card_w // 2, cy + int(dp(16))
        Color(*C.CYAN[:3], 0.16)
        RoundedRectangle(pos=(card_x, card_y), size=(card_w, card_h_icon), radius=[6])
        Color(*C.CYAN[:3], 0.55)
        Line(rounded_rectangle=(card_x, card_y, card_w, card_h_icon, 6), width=1.5)

        # Chip
        Color(*C.CYAN[:3], 0.4)
        RoundedRectangle(pos=(card_x + int(dp(6)), card_y + card_h_icon - int(dp(14))), size=(int(dp(14)), int(dp(8))), radius=[2])

        # Signal arcs
        for i, (radius, alpha) in enumerate([(int(dp(5)), 0.8), (int(dp(10)), 0.5), (int(dp(15)), 0.3)]):
            Color(*C.CYAN[:3], alpha)
            Line(
                circle=(card_x + card_w - int(dp(8)), card_y + int(dp(24)), radius, 315, 405),
                width=1.5,
            )

        # Text with bob
        bob = math.sin(self.idle_bob) * 3
        self._text(
            "Tap a card to play", cx - int(dp(90)), cy - int(dp(24)) + bob,
            font_size=sp(34), color=C.TEXT_SEC,
        )
        self._text(
            "Place an NFC card on the reader", cx - int(dp(140)), cy - int(dp(52)) + bob,
            font_size=sp(22), color=C.TEXT_MUTED,
        )

        # Error display
        if self.player.last_error:
            err = self.player.last_error[:60]
            self._text(err, cx - int(dp(120)), cy - int(dp(80)), font_size=sp(20), color=(1, 0.37, 0.37, 1))

    # ── queue panel ──────────────────────────────────────────────────────────
    def _draw_queue(self, x, y_pad, w, h):
        top = h - y_pad
        Color(*C.VIOLET[:3], 0.75)
        self._text("UP NEXT", x, top - int(dp(16)), font_size=sp(18), color=_rgba(C.VIOLET, 0.75), bold=True)

        upcoming = self.queue_mgr.get_upcoming()

        if not upcoming:
            self._text("Queue is empty", x + w // 2 - int(dp(48)), top - int(dp(56)), font_size=sp(22), color=C.TEXT_MUTED)
            self._text("Scan cards to add songs", x + w // 2 - int(dp(72)), top - int(dp(80)), font_size=sp(18), color=C.TEXT_MUTED)
            return

        card_h_next = int(dp(64))
        card_h_normal = int(dp(54))
        gap = int(dp(12))
        max_visible = min(len(upcoming), 6)
        iy = top - int(dp(44))

        for i in range(max_visible):
            item = upcoming[i]
            is_next = i == 0
            ch = card_h_next if is_next else card_h_normal

            # Glass card background
            bg_alpha = 0.55 if is_next else 0.40
            Color(*C.GLASS_BG[:3], bg_alpha)
            RoundedRectangle(pos=(x, iy - ch), size=(w, ch), radius=[14])

            # Border
            border_c = C.CYAN if is_next else C.VIOLET
            border_a = 0.45 if is_next else 0.25
            Color(*border_c[:3], border_a)
            Line(rounded_rectangle=(x, iy - ch, w, ch, 14), width=1.5 if is_next else 1)

            # Highlight bar for "next" card
            if is_next:
                Color(*C.CYAN[:3], 0.3)
                Rectangle(pos=(x, iy - 2), size=(w, 2))

            # Mini album art
            thumb_size = int(dp(44)) if is_next else int(dp(36))
            thumb_x = x + int(dp(10))
            thumb_y = iy - ch + (ch - thumb_size) // 2
            accent = C.CYAN if i % 2 == 0 else C.VIOLET
            Color(*accent[:3], 0.3)
            RoundedRectangle(pos=(thumb_x, thumb_y), size=(thumb_size, thumb_size), radius=[8])

            img_bytes = item.get("image_bytes")
            if img_bytes:
                uid = item.get("uid", f"idx_{i}")
                cache_key = f"{uid}_{thumb_size}"
                if cache_key not in self._mini_cache:
                    tex = _load_image_bytes(img_bytes, thumb_size)
                    self._mini_cache[cache_key] = tex
                tex = self._mini_cache.get(cache_key)
                if tex:
                    Color(1, 1, 1, 1)
                    RoundedRectangle(texture=tex, pos=(thumb_x, thumb_y), size=(thumb_size, thumb_size), radius=[8])

            Color(*accent[:3], 0.4)
            Line(rounded_rectangle=(thumb_x, thumb_y, thumb_size, thumb_size, 8), width=1.5)

            # Title
            text_x = thumb_x + thumb_size + int(dp(12))
            title = item.get("title", "Unknown")
            if len(title) > 26:
                title = title[:23] + "..."
            self._text(title, text_x, iy - ch // 2 + int(dp(4)), font_size=sp(22 if is_next else 20), color=C.TEXT_SLATE, bold=True)

            artist = item.get("artist", "")
            if artist:
                self._text(artist, text_x, iy - ch // 2 - int(dp(14)), font_size=sp(18 if is_next else 16), color=C.TEXT_DIM)

            iy -= ch + gap

        if len(upcoming) > max_visible:
            more = len(upcoming) - max_visible
            self._text(f"+ {more} more...", x + int(dp(8)), iy - int(dp(4)), font_size=sp(18), color=C.TEXT_MUTED)

    # ── bottom bar ───────────────────────────────────────────────────────────
    def _draw_bottom_bar(self, w, h):
        bar_h = int(dp(40))
        bar_y = 0

        # Top border
        Color(1, 1, 1, 0.06)
        Rectangle(pos=(0, bar_h), size=(w, 1))

        # Buttons
        self._draw_reg_button(int(dp(60)), bar_h // 2, int(dp(80)), int(dp(30)), "Skip", C.TEXT_SEC)
        self._draw_reg_button(int(dp(160)), bar_h // 2, int(dp(100)), int(dp(30)), "Register", C.CYAN)
        self._draw_reg_button(int(dp(260)), bar_h // 2, int(dp(80)), int(dp(30)), "Quit", C.TEXT_SEC)

        # LIVE badge (right side)
        live_w, live_h = int(dp(48)), int(dp(20))
        live_x = w - int(dp(24)) - live_w
        live_y = bar_h // 2 - live_h // 2

        Color(*C.CYAN[:3], 0.04)
        RoundedRectangle(pos=(live_x, live_y), size=(live_w, live_h), radius=[11])
        Color(*C.CYAN[:3], 0.2)
        Line(rounded_rectangle=(live_x, live_y, live_w, live_h, 11), width=1)

        # Pulsing dot
        dot_a = 0.8 + 0.2 * math.sin(self.pulse_phase * 3)
        Color(*C.CYAN[:3], dot_a)
        dot_sz = int(dp(5))
        Ellipse(pos=(live_x + int(dp(6)), live_y + live_h // 2 - dot_sz // 2), size=(dot_sz, dot_sz))

        self._text("LIVE", live_x + int(dp(16)), live_y + int(dp(3)), font_size=sp(14), color=_rgba(C.CYAN, 0.7), bold=True)

        # Tap count
        count_str = f"{self.tap_count} cards tapped"
        self._text(
            count_str, live_x - int(dp(120)), bar_h // 2 - int(dp(5)),
            font_size=sp(17), color=C.TEXT_MUTED,
        )

    # ── toast ────────────────────────────────────────────────────────────────
    def _draw_toast(self, w, h):
        alpha = min(self.toast_alpha, 1.0)
        if alpha < 0.01:
            return

        toast_y = int(dp(64))
        text = self.toast_message
        tw = len(text) * int(dp(8)) + int(dp(40))
        tx = (w - tw) // 2
        th = int(dp(40))

        Color(*C.GLASS_BG[:3], alpha * 0.8)
        RoundedRectangle(pos=(tx, toast_y), size=(tw, th), radius=[12])
        Color(*C.CYAN[:3], alpha * 0.3)
        Line(rounded_rectangle=(tx, toast_y, tw, th, 12), width=1)
        self._text(text, tx + int(dp(20)), toast_y + int(dp(10)), font_size=sp(22), color=_rgba(C.TEXT, alpha))

    # ── text helper ──────────────────────────────────────────────────────────
    def _text(self, text, x, y, font_size=sp(20), color=None, bold=False):
        """Draw text using Kivy CoreLabel on canvas (retained-mode friendly)."""
        from kivy.core.text import Label as CoreLabel

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

    # ── registration UI & logic ──

    def on_touch_down(self, touch):
        # Convert to local coordinates just in case
        mx, my = self.to_local(touch.x, touch.y)
        
        if self.page == "player":
            bar_h = int(dp(40))
            cy = bar_h // 2
            
            # Use hit_btn with enlarged hitboxes for easier touch
            hit_h = int(dp(60))
            if self._hit_btn(int(dp(60)), cy, int(dp(90)), hit_h, mx, my):
                self.player.skip()
                return True
            elif self._hit_btn(int(dp(160)), cy, int(dp(110)), hit_h, mx, my):
                self.page = "register"
                self.reg_state = REG_STATE_HOME
                self._remove_text_input()
                return True
            elif self._hit_btn(int(dp(260)), cy, int(dp(90)), hit_h, mx, my):
                from kivy.app import App
                App.get_running_app().stop()
                return True
                
            return super().on_touch_down(touch)
            
        w, h = self.width, self.height
        cx = w // 2

        if self.reg_state == REG_STATE_HOME:
            if self._hit_btn(cx, h - 280, 260, 50, mx, my):
                self.reg_state = REG_STATE_WAITING_SCAN
                return True
            if self._hit_btn(cx, h - 350, 260, 50, mx, my):
                self._load_cards_list()
                self.reg_state = REG_STATE_LIST
                return True

        elif self.reg_state == REG_STATE_PICK_SOURCE:
            if self._hit_btn(cx, h - 300, 300, 50, mx, my):
                self.selected_source = "youtube"
                self.reg_state = REG_STATE_INPUT_URL
                self._show_text_input("Enter YouTube URL:")
                return True
            if self._hit_btn(cx, h - 370, 300, 50, mx, my):
                self.selected_source = "local"
                self._scan_local_files()
                self.reg_state = REG_STATE_PICK_FILE
                return True

        elif self.reg_state == REG_STATE_PICK_FILE:
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
                    self.reg_state = REG_STATE_INPUT_TITLE
                    self._show_text_input("Enter Song Title:", preset)
                    return True

        elif self.reg_state == REG_STATE_LIST:
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
                    from registry import Registry
                    r = Registry(self.config.db_path)
                    r.delete_card(uid)
                    self.show_toast(f"Deleted card {uid[:6]}...")
                    self._load_cards_list()
                    return True

        return super().on_touch_down(touch)

    def handle_scroll(self, direction):
        if self.page != "register":
            return
        if self.reg_state == REG_STATE_PICK_FILE:
            self.file_scroll = max(0, self.file_scroll - direction * 3)
            self.file_scroll = min(self.file_scroll, max(0, len(self.local_files) - 10))
        elif self.reg_state == REG_STATE_LIST:
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
            font_size=sp(24),
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

    def _reset_reg(self):
        self.scanned_uid = None
        self.existing_card = None
        self.selected_source = None
        self.reg_title = ""
        self.reg_url = ""
        self._remove_text_input()

    def _draw_reg_header(self, w, h):
        self._text("uTune", 28, h - 50, font_size=sp(38), color=C.CYAN, bold=True)
        self._text("CARD REGISTRATION", 160, h - 42, font_size=sp(18), color=_rgba(C.VIOLET, 0.7), bold=True)
        Color(1, 1, 1, 0.08)
        Rectangle(pos=(28, h - 70), size=(w - 56, 1))

    def _draw_reg_home(self, w, h):
        cx = w // 2
        self._text("Card Manager", cx - 70, h - 140, font_size=sp(28), color=C.TEXT, bold=True)
        self._text(f"Cards registered: {self._card_count()}", cx - 80, h - 175, font_size=sp(22), color=C.TEXT_SEC)
        self._draw_reg_button(cx, h - 280, 260, 50, "Register New Card", C.CYAN)
        self._draw_reg_button(cx, h - 350, 260, 50, "View All Cards", C.VIOLET)

    def _draw_reg_waiting_scan(self, w, h):
        cx = w // 2
        cy = h // 2 + 20

        # Pulsing ring
        radius = 50 + int(5 * math.sin(self.pulse_phase * 2))
        ring_a = 0.47 + 0.24 * math.sin(self.pulse_phase * 3)
        Color(*C.CYAN[:3], ring_a)
        Line(circle=(cx, cy, radius), width=2)

        inner_a = 0.16 + 0.08 * math.sin(self.pulse_phase * 2 + 1)
        Color(*C.CYAN[:3], inner_a)
        Ellipse(pos=(cx - 28, cy - 28), size=(56, 56))

        self._text("Scan RFID Card", cx - 80, cy - radius - 40, font_size=sp(28), color=C.TEXT, bold=True)
        self._text("Place card on the reader...", cx - 110, cy - radius - 70, font_size=sp(22), color=C.TEXT_MUTED)

    def _draw_reg_pick_source(self, w, h):
        cx = w // 2
        self._text(f"Card UID: {self.scanned_uid}", cx - 100, h - 130, font_size=sp(20), color=C.CYAN)

        if self.existing_card:
            self._text(
                f"Already registered: {self.existing_card['title']}", cx - 160, h - 160,
                font_size=sp(22), color=C.ORANGE,
            )
            self._text("Continuing will overwrite", cx - 90, h - 185, font_size=sp(18), color=C.TEXT_MUTED)

        self._text("Choose Audio Source", cx - 100, h - 230, font_size=sp(28), color=C.TEXT, bold=True)
        self._draw_reg_button(cx, h - 300, 300, 50, "[1] YouTube URL", C.CYAN)
        self._draw_reg_button(cx, h - 370, 300, 50, "[2] Local File", C.VIOLET)

    def _draw_reg_input_screen(self, w, h, label):
        cx = w // 2
        self._text(label, cx - 100, h - 170, font_size=sp(28), color=C.TEXT, bold=True)
        self._text("Press ENTER to confirm  •  ESC to cancel", cx - 170, h // 2 - 60, font_size=sp(18), color=C.TEXT_MUTED)

    def _draw_reg_downloading(self, w, h):
        cx = w // 2
        cy = h // 2

        # Spinner
        radius = 40
        for i in range(20):
            a = (self.pulse_phase * 5 + (math.pi * 2 * i / 20)) % (math.pi * 2)
            sx = cx + int(radius * math.cos(a))
            sy = cy + 40 + int(radius * math.sin(a))
            dot_a = 0.3 + 0.7 * (i / 20)
            Color(*C.CYAN[:3], dot_a)
            Ellipse(pos=(sx - 3, sy - 3), size=(6, 6))

        self._text("Downloading Audio...", cx - 100, cy - 30, font_size=sp(28), color=C.TEXT, bold=True)
        self._text(self.download_progress, cx - 150, cy - 60, font_size=sp(22), color=C.TEXT_SEC)
        self._text("Please wait...", cx - 50, cy - 90, font_size=sp(18), color=C.TEXT_MUTED)

    def _draw_reg_pick_file(self, w, h):
        cx = w // 2
        self._text("Select Audio File", cx - 90, h - 130, font_size=sp(28), color=C.TEXT, bold=True)
        self._text(f"Folder: {config.music_folder}", cx - 180, h - 160, font_size=sp(18), color=C.TEXT_MUTED)

        if not self.local_files:
            self._text("No audio files found in music folder", cx - 150, h // 2, font_size=sp(22), color=C.RED)
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
            self._text(ext, list_x + 10, iy - item_h + 14, font_size=sp(18), color=C.CYAN if is_sel else C.TEXT_MUTED)

            fname = self.local_files[idx]
            if len(fname) > 40:
                fname = fname[:37] + "..."
            self._text(fname, list_x + 60, iy - item_h + 14, font_size=sp(19), color=C.TEXT if is_sel else C.TEXT_SEC)

        self._text("↑↓ Navigate  •  ENTER select  •  Click to pick", cx - 180, list_y - visible * item_h - 10, font_size=sp(18), color=C.TEXT_MUTED)

    def _draw_reg_confirm(self, w, h):
        cx = w // 2
        self._text("Confirm Registration", cx - 100, h - 160, font_size=sp(28), color=C.TEXT, bold=True)

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
            self._text(label, card_x + 20, fy, font_size=sp(18), color=C.VIOLET, bold=True)
            self._text(value, card_x + 100, fy, font_size=sp(22), color=C.TEXT)
            fy -= 34

        self._text("Press Y or ENTER to confirm  •  N to cancel", cx - 180, card_y - 30, font_size=sp(22), color=C.TEXT_SEC)

    def _draw_reg_done(self, w, h):
        cx = w // 2
        cy = h // 2 + 20

        Color(*C.GREEN[:3], 0.16)
        Ellipse(pos=(cx - 38, cy - 38), size=(76, 76))
        Color(*C.GREEN[:3], 0.7)
        Line(circle=(cx, cy, 38), width=2)

        # Checkmark (two line segments)
        Color(*C.GREEN)
        Line(points=[cx - 12, cy - 2, cx - 2, cy - 12, cx + 16, cy + 10], width=2)

        self._text("Card Registered!", cx - 85, cy - 60, font_size=sp(28), color=C.GREEN, bold=True)
        self._text(self.reg_title, cx - 80, cy - 90, font_size=sp(22), color=C.TEXT)
        self._text("Press ENTER to register another  •  ESC to exit", cx - 200, cy - 130, font_size=sp(18), color=C.TEXT_MUTED)

    def _draw_reg_cards_list(self, w, h):
        cx = w // 2
        self._text("Registered Cards", cx - 85, h - 120, font_size=sp(28), color=C.TEXT, bold=True)
        self._text(f"{len(self.cards_list)} cards", cx - 30, h - 148, font_size=sp(18), color=C.TEXT_MUTED)

        if not self.cards_list:
            self._text("No cards registered yet", cx - 90, h // 2, font_size=sp(22), color=C.TEXT_MUTED)
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

            self._text(card["uid"][:12], list_x + 10, iy - item_h + 16, font_size=sp(18), color=C.CYAN)

            title_text = card["title"]
            if len(title_text) > 28:
                title_text = title_text[:25] + "..."
            self._text(title_text, list_x + 140, iy - item_h + 16, font_size=sp(22), color=C.TEXT)

            is_url = card["url"].startswith("http")
            src = "YT" if is_url else "LOCAL"
            src_c = C.CYAN if is_url else C.VIOLET
            self._text(src, list_x + 460, iy - item_h + 16, font_size=sp(18), color=src_c)

            # Delete button
            dx = list_x + 520
            dy = iy - item_h + 10
            Color(*C.RED[:3], 0.2)
            RoundedRectangle(pos=(dx, dy), size=(32, 28), radius=[4])
            Color(*C.RED[:3], 0.6)
            Line(rounded_rectangle=(dx, dy, 32, 28, 4), width=1)
            self._text("X", dx + 10, dy + 5, font_size=sp(20), color=C.RED, bold=True)

    def _draw_reg_button(self, cx, cy, bw, bh, text, color):
        bx = cx - bw // 2
        by = cy - bh // 2
        Color(*C.GLASS_BG[:3], 0.55)
        RoundedRectangle(pos=(bx, by), size=(bw, bh), radius=[10])
        Color(*color[:3], 0.4)
        Line(rounded_rectangle=(bx, by, bw, bh, 10), width=1)
        # Top highlight
        Color(*color[:3], 0.15)
        Rectangle(pos=(bx, by + bh - 1), size=(bw, 1))
        self._text(text, cx - len(text) * 5, cy - 8, font_size=sp(20), color=color, bold=True)

    def _hit_btn(self, cx, cy, bw, bh, mx, my):
        bx = cx - bw // 2
        by = cy - bh // 2
        return bx < mx < bx + bw and by < my < by + bh

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
                self.reg_state = REG_STATE_INPUT_TITLE
                Clock.schedule_once(lambda dt: self._show_text_input("Enter Song Title:", preset), 0)
            else:
                self.toast_message = "Download failed — no audio file found."
                self.toast_end = _time.time() + 3
                self.reg_state = REG_STATE_PICK_SOURCE
        except Exception as e:
            self.toast_message = f"Error: {e}"
            self.toast_end = _time.time() + 3
            self.reg_state = REG_STATE_PICK_SOURCE
