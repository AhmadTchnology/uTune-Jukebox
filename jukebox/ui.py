"""uTune Jukebox UI — Kivy implementation.

Recreates the premium dark-space design from the original Pygame version:
  - Deep space background with radial gradient blobs
  - Scanline + grid overlay
  - Glass-morphism cards with cyan/violet accents
  - Album art with rounded corners
  - Progress bar, toast notifications, NFC tap flash
  - Queue panel with drag-and-drop reordering

Performance optimizations:
  - TextCache: LRU cache for CoreLabel textures
  - Layered canvases: canvas.before (static background), canvas (dynamic UI), canvas.after (overlays)
  - Dirty-flag redraw: only redrawing canvas when animated or state changes
"""
import math
import time as _time
import io
import os
import threading
import collections
import re

from kivy.uix.textinput import TextInput
from player import AUDIO_EXTENSIONS
from platform_utils import get_subprocess_flags

from kivy.uix.floatlayout import FloatLayout
from kivy.graphics import (
    Color, Rectangle, RoundedRectangle, Ellipse, Line
)
from kivy.graphics.texture import Texture
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.metrics import sp, dp
from kivy.properties import (
    StringProperty, NumericProperty
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
    GLASS_BG    = _c(15, 23, 42)
    TEXT        = _c(241, 245, 249)
    TEXT_SEC     = _c(148, 163, 184)
    TEXT_MUTED   = _c(71, 85, 105)
    TEXT_SLATE   = _c(226, 232, 240)
    TEXT_DIM     = _c(100, 116, 139)
    RED          = _c(239, 68, 68)
    GREEN        = _c(34, 197, 94)
    ORANGE       = _c(251, 146, 60)


# ── Helpers ──────────────────────────────────────────────────────────────────
def _rgba(c, alpha=None):
    if alpha is not None:
        return (c[0], c[1], c[2], alpha)
    return c


def _load_image_bytes(raw_bytes, size):
    """Load raw image bytes into a Kivy Texture, cropped square & scaled."""
    try:
        cimg = CoreImage(io.BytesIO(raw_bytes), ext="jpg")
        tex = cimg.texture
        if tex is None:
            cimg = CoreImage(io.BytesIO(raw_bytes), ext="png")
            tex = cimg.texture
        return tex
    except Exception:
        return None


class TextCache:
    """LRU cache for Kivy CoreLabel textures to avoid recreating them every frame."""
    def __init__(self, max_size=128):
        self.cache = collections.OrderedDict()
        self.max_size = max_size

    def get(self, text, font_size, bold, color):
        from kivy.core.text import Label as CoreLabel
        key = (text, font_size, bold, tuple(color))
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        
        font_name = 'jukebox/NotoSans-Bold.ttf' if bold else 'jukebox/NotoSans-Regular.ttf'
        if not os.path.exists(font_name):
            font_name = 'Roboto' # fallback
            
        cl = CoreLabel(text=str(text), font_size=font_size, bold=bold, color=color, font_name=font_name)
        cl.refresh()
        tex = cl.texture
        self.cache[key] = tex
        
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)
        return tex


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
        self._text_cache = TextCache()

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

        self.bind(size=self._on_resize)
        self.dirty = True
        self._bg_size = (0, 0)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self):
        self.running = True
        fps = self.config.ui_fps
        Clock.schedule_interval(self._tick, 1.0 / fps)

    def stop_ui(self):
        self.running = False
        Clock.unschedule(self._tick)

    def _on_resize(self, *_args):
        self.dirty = True

    def _tick(self, dt):
        if not self.running:
            return
            
        needs_redraw = False
        
        # Always animate phase variables
        self.pulse_phase += dt * 2.0
        self.idle_bob += dt * 0.8
        now = _time.time()

        # Check toast
        old_toast_alpha = self.toast_alpha
        if now < self.toast_end:
            self.toast_alpha = min(1.0, self.toast_alpha + dt * 3)
        else:
            self.toast_alpha = max(0, self.toast_alpha - dt * 2)
        if abs(self.toast_alpha - old_toast_alpha) > 0.01:
            needs_redraw = True

        # Check flash
        if self.flash_active:
            needs_redraw = True
            if (now - self.flash_start) > self.flash_duration:
                self.flash_active = False

        # If playing, progress bar needs redraw
        if self.player.is_playing or self.reg_state in (REG_STATE_WAITING_SCAN, REG_STATE_DOWNLOADING):
            needs_redraw = True

        if self.page == "player" and not self.player.is_playing:
            needs_redraw = True # Idle bob animation

        if self.dirty or needs_redraw:
            self._redraw()
            self.dirty = False

    # ── public API ───────────────────────────────────────────────────────────
    def show_toast(self, message, duration=3.0):
        self.toast_message = message
        self.toast_end = _time.time() + duration
        self.toast_alpha = 1.0
        self.flash_active = True
        self.flash_start = _time.time()
        self.tap_count += 1
        self.dirty = True

    def _on_player_status(self, msg):
        self.status_message = msg
        self.status_end = _time.time() + 5
        self.dirty = True


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
                self.selected_source = "local"
                self._scan_local_files()
                self.reg_state = REG_STATE_PICK_FILE
        self.dirty = True

    # ── keyboard (RFID via USB OTG keyboard emulation) ───────────────────────

    def handle_key_down(self, window, key, scancode, codepoint, modifiers):
        """Called from the Kivy App's on_key_down."""
        self.dirty = True
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
                    self.handle_scan(self._key_buffer.strip())
                    self._key_buffer = ""
                elif codepoint and codepoint.isdigit():
                    self._key_buffer += codepoint
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
                    self._do_confirm_register()
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
            
    # ── background rendering ──────────────────────────────────────────────────
    def _build_background(self, w, h):
        """Build the static background in canvas.before once."""
        if self._bg_size == (w, h):
            return
        
        self.canvas.before.clear()
        with self.canvas.before:
            Color(*C.BG)
            Rectangle(pos=self.pos, size=(w, h))

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

            # Tiled Scanlines
            buf = bytearray([0, 0, 0, 30] * 2 * w + [0, 0, 0, 0] * 2 * w)
            tex = Texture.create(size=(w, 4), colorfmt="rgba")
            tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
            tex.wrap = 'repeat'
            tex.uvsize = (1, h / 4)
            Color(1, 1, 1, 1)
            Rectangle(pos=self.pos, size=(w, h), texture=tex)
            
        self._bg_size = (w, h)

    # ── drawing ──────────────────────────────────────────────────────────────
    def _redraw(self):
        w, h = self.width, self.height
        if w < 10 or h < 10:
            return

        self._build_background(w, h)
        
        self.canvas.clear()
        self.canvas.after.clear()
        
        pad = int(dp(32))
        bar_h = int(dp(72))

        # Dynamic content on main canvas
        with self.canvas:
            if self.page == "player":
                # STRICTLY LANDSCAPE LAYOUT
                right_w = max(int(dp(360)), int(w * 0.35))
                divider_x = w - right_w - pad
                left_w = divider_x - pad
                
                self._draw_now_playing(pad, bar_h, left_w, h)
                
                # Vertical Divider
                Color(1, 1, 1, 0.08)
                Rectangle(pos=(divider_x, bar_h + pad), size=(1, h - bar_h - pad*2))
                
                self._draw_queue(divider_x + pad, bar_h, right_w - pad, h)
                
                # Bottom bar
                self._draw_bottom_bar(w, bar_h)
            else:
                self._draw_reg_header(w, h)
                if self.reg_state == REG_STATE_HOME:
                    self._draw_reg_home(w, h)
                elif self.reg_state == REG_STATE_WAITING_SCAN:
                    self._draw_reg_waiting_scan(w, h)
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

        # Overlays on canvas.after
        with self.canvas.after:
            if self.flash_active:
                self._draw_flash(w, h)
            if self.toast_alpha > 0.01:
                self._draw_toast(w, h)

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
            Rectangle(pos=(0, 0), size=(w, h))

    # ── now playing ──────────────────────────────────────────────────────────
    def _draw_now_playing(self, x, y_pad, w, h):
        track = self.player.current_track
        top = h - y_pad

        # "NOW PLAYING" label with dot
        label_y = top - int(dp(20))
        Color(*C.CYAN[:3], 0.8)
        Ellipse(pos=(x, label_y - 2), size=(int(dp(6)), int(dp(6))))
        self._text(
            "NOW PLAYING", x + int(dp(14)), label_y,
            font_size=sp(16), color=_rgba(C.CYAN, 0.8), bold=True,
        )

        if track:
            self._draw_playing(x, label_y - int(dp(40)), w, h, track)
        else:
            self._draw_idle(x, y_pad, w, h)

    def _draw_playing(self, x, top_y, w, h, track):
        # Scale album art nicely in the available space
        max_art = int(dp(480))
        art_size = min(max_art, int(w * 0.4))
        
        # Center vertically if plenty of space
        art_y = top_y - art_size
        if art_y < h // 2 - art_size // 2:
            art_y = h // 2 - art_size // 2 + int(dp(20))

        # Album art
        self._draw_album_art(x, art_y, art_size, track)

        # Track info to the right of art
        info_x = x + art_size + int(dp(32))
        info_w = w - (info_x - x)

        title = track.get("title", "Unknown Track")
        
        # Calculate dynamic max chars based on available width
        max_chars = max(28, int(info_w / sp(18)))
        
        # word wrap title manually
        words = title.split(' ')
        lines = []
        cur_line = ""
        for w in words:
            if len(cur_line) + len(w) > max_chars:
                lines.append(cur_line.strip())
                cur_line = w + " "
            else:
                cur_line += w + " "
        lines.append(cur_line.strip())
        
        # Max 2 lines
        lines = lines[:2]
        if len(lines) > 1 and len(lines[1]) > max_chars:
            lines[1] = lines[1][:max_chars-3] + "..."
            
        # Vertically align info with the art
        info_top = art_y + art_size - int(dp(16))
        
        for i, line in enumerate(lines):
            self._text(line, info_x, info_top - i * int(dp(32)), font_size=sp(24), color=C.TEXT, bold=True)
        
        artist = track.get("artist", "Unknown Artist")
        self._text(artist, info_x, info_top - len(lines) * int(dp(32)) - int(dp(8)), font_size=sp(18), color=C.TEXT_SEC)

        album = track.get("album", "")
        offset = len(lines) * int(dp(32)) + int(dp(52))
        if album:
            self._text(album.upper(), info_x, info_top - offset, font_size=sp(16), color=C.TEXT_MUTED)
            offset += int(dp(24))

        # Progress bar
        prog_w = info_w - int(dp(16))
        self._draw_progress_bar(info_x, art_y + int(dp(16)), prog_w)

    def _draw_album_art(self, x, y, size, track):
        cache_key = f"{track.get('title', '?')}_{'img' if track.get('image_bytes') else 'no'}"
        if self._art_cache_key != cache_key:
            self._art_cache_key = cache_key
            img_bytes = track.get("image_bytes")
            if img_bytes:
                self._art_texture = _load_image_bytes(img_bytes, size)
            else:
                self._art_texture = None

        # Shadow
        Color(0, 0, 0, 0.4)
        RoundedRectangle(pos=(x - 4, y - 8), size=(size + 8, size + 8), radius=[18])

        if self._art_texture:
            Color(1, 1, 1, 1)
            RoundedRectangle(texture=self._art_texture, pos=(x, y), size=(size, size), radius=[16])
        else:
            # Gradient fallback with initial letter
            Color(*C.BG_INDIGO[:3], 0.9)
            RoundedRectangle(pos=(x, y), size=(size, size), radius=[16])
            Color(*C.VIOLET_DIM[:3], 0.3)
            RoundedRectangle(pos=(x, y), size=(size, size // 2), radius=[0, 0, 16, 16])
            initial = (track.get("title", "?"))[0].upper()
            self._text(
                initial, x + size // 2 - int(dp(20)), y + size // 2 - int(dp(30)),
                font_size=sp(80), color=(1, 1, 1, 0.2), bold=True,
            )

        # Border
        Color(*C.CYAN[:3], 0.3)
        Line(rounded_rectangle=(x, y, size, size, 16), width=1)

        # Glow pulse when playing
        if self.player.is_playing:
            glow_a = 0.15 + 0.1 * math.sin(self.pulse_phase * 2.0)
            Color(*C.CYAN[:3], glow_a)
            Line(rounded_rectangle=(x - 2, y - 2, size + 4, size + 4, 18), width=2.0)

    def _draw_progress_bar(self, x, y, w):
        track = self.player.current_track
        if not track:
            return

        bar_h = int(dp(6))
        time_w = int(dp(45))
        bar_x = x + time_w
        bar_w = w - time_w * 2

        if not self.player.play_start_time:
            # Loading indicator — pulsing bar
            Color(1, 1, 1, 0.1)
            RoundedRectangle(pos=(bar_x, y), size=(bar_w, bar_h), radius=[3])
            pulse_w = int(dp(64))
            pulse_x = int((bar_w - pulse_w) * ((math.sin(self.pulse_phase * 4) + 1) / 2))
            Color(*C.CYAN)
            RoundedRectangle(pos=(bar_x + pulse_x, y), size=(pulse_w, bar_h), radius=[3])
            self._text("Loading stream...", bar_x, y + int(dp(16)), font_size=sp(16), color=C.CYAN)
            return

        elapsed = _time.time() - self.player.play_start_time
        duration = track.get("duration")
        pct = min(1.0, elapsed / float(duration)) if duration else 1.0

        # Draw start time
        mins, secs = int(elapsed) // 60, int(elapsed) % 60
        self._text(f"{mins}:{secs:02d}", x, y - int(dp(6)), font_size=sp(14), color=_rgba(C.CYAN, 0.8))

        # Background
        Color(1, 1, 1, 0.1)
        RoundedRectangle(pos=(bar_x, y), size=(bar_w, bar_h), radius=[3])
        
        # Fill
        fill_w = int(bar_w * pct)
        if fill_w > 0:
            Color(*C.CYAN)
            RoundedRectangle(pos=(bar_x, y), size=(fill_w, bar_h), radius=[3])
            # Playhead dot
            Color(1, 1, 1, 1)
            dot_sz = int(dp(12))
            Ellipse(pos=(bar_x + fill_w - dot_sz // 2, y + bar_h // 2 - dot_sz // 2), size=(dot_sz, dot_sz))
            # Playhead glow
            Color(*C.CYAN[:3], 0.4)
            Ellipse(pos=(bar_x + fill_w - dot_sz, y + bar_h // 2 - dot_sz), size=(dot_sz*2, dot_sz*2))

        # Draw end time
        if duration:
            dur = float(duration)
            dm, ds = int(dur) // 60, int(dur) % 60
            # Right align duration
            self._text(f"{dm}:{ds:02d}", x + w - int(dp(35)), y - int(dp(6)), font_size=sp(14), color=C.TEXT_MUTED)

    def _draw_idle(self, x, y_pad, w, h):
        cx = x + w // 2
        cy = h // 2

        # NFC card icon
        card_w, card_h_icon = int(dp(64)), int(dp(80))
        card_x, card_y = cx - card_w // 2, cy + int(dp(24))
        
        Color(*C.CYAN[:3], 0.16)
        RoundedRectangle(pos=(card_x, card_y), size=(card_w, card_h_icon), radius=[10])
        Color(*C.CYAN[:3], 0.6)
        Line(rounded_rectangle=(card_x, card_y, card_w, card_h_icon, 10), width=2)

        # Chip
        Color(*C.CYAN[:3], 0.5)
        RoundedRectangle(pos=(card_x + int(dp(10)), card_y + card_h_icon - int(dp(22))), size=(int(dp(20)), int(dp(14))), radius=[3])

        # Breathing glow
        glow = 0.4 + 0.3 * math.sin(self.idle_bob)
        Color(*C.CYAN[:3], glow * 0.3)
        Line(rounded_rectangle=(card_x - 10, card_y - 10, card_w + 20, card_h_icon + 20, 14), width=1.5)

        bob = math.sin(self.idle_bob) * 4
        self._text(
            "Ready to Play", cx - int(dp(80)), cy - int(dp(30)) + bob,
            font_size=sp(32), color=C.TEXT_SEC, bold=True
        )
        self._text(
            "Place an NFC card on the reader", cx - int(dp(110)), cy - int(dp(60)) + bob,
            font_size=sp(18), color=C.TEXT_MUTED,
        )

        # Error display
        if self.player.last_error:
            err = self.player.last_error[:60]
            self._text(err, cx - int(dp(150)), cy - int(dp(100)), font_size=sp(18), color=(1, 0.37, 0.37, 1))

    # ── queue panel ──────────────────────────────────────────────────────────
    def _draw_queue(self, x, y_pad, w, h):
        top = h - y_pad
        Color(*C.VIOLET[:3], 0.8)
        self._text("UP NEXT", x, top - int(dp(20)), font_size=sp(16), color=_rgba(C.VIOLET, 0.8), bold=True)

        upcoming = self.queue_mgr.get_upcoming()

        if not upcoming:
            self._text("Queue is empty", x + int(dp(20)), top - int(dp(60)), font_size=sp(20), color=C.TEXT_MUTED)
            return

        card_h_next = int(dp(72))
        card_h_normal = int(dp(60))
        gap = int(dp(12))
        max_visible = min(len(upcoming), 6)
        iy = top - int(dp(44))

        for i in range(max_visible):
            item = upcoming[i]
            is_next = i == 0
            ch = card_h_next if is_next else card_h_normal

            # Glass card background
            bg_alpha = 0.6 if is_next else 0.4
            Color(*C.GLASS_BG[:3], bg_alpha)
            RoundedRectangle(pos=(x, iy - ch), size=(w, ch), radius=[12])

            # Border
            border_c = C.CYAN if is_next else C.VIOLET
            border_a = 0.5 if is_next else 0.2
            Color(*border_c[:3], border_a)
            Line(rounded_rectangle=(x, iy - ch, w, ch, 12), width=1.5 if is_next else 1)

            # Highlight bar for "next" card
            if is_next:
                Color(*C.CYAN[:3], 0.4)
                Rectangle(pos=(x, iy - 2), size=(w, 2))

            # Mini album art
            thumb_size = int(dp(48)) if is_next else int(dp(40))
            thumb_x = x + int(dp(12))
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
            Line(rounded_rectangle=(thumb_x, thumb_y, thumb_size, thumb_size, 8), width=1)

            # Title
            text_x = thumb_x + thumb_size + int(dp(16))
            title = item.get("title", "Unknown")
            if len(title) > 28:
                title = title[:25] + "..."
            self._text(
                title, text_x, iy - ch // 2 + int(dp(4)),
                font_size=sp(20 if is_next else 18), color=C.TEXT_SLATE, bold=True
            )

            artist = item.get("artist", "")
            if artist:
                self._text(
                    artist, text_x, iy - ch // 2 - int(dp(14)),
                    font_size=sp(16 if is_next else 14), color=C.TEXT_DIM
                )

            iy -= ch + gap

        if len(upcoming) > max_visible:
            more = len(upcoming) - max_visible
            self._text(f"+ {more} more...", x + int(dp(12)), iy - int(dp(4)), font_size=sp(16), color=C.TEXT_MUTED)

    # ── bottom bar ───────────────────────────────────────────────────────────
    def _draw_bottom_bar(self, w, h):
        bar_h = h
        bar_y = 0

        # Top border
        Color(1, 1, 1, 0.08)
        Rectangle(pos=(0, bar_h), size=(w, 1))

        # LIVE badge (right side)
        live_w, live_h = int(dp(64)), int(dp(28))
        live_x = w - int(dp(32)) - live_w
        live_y = bar_h // 2 - live_h // 2

        Color(*C.CYAN[:3], 0.06)
        RoundedRectangle(pos=(live_x, live_y), size=(live_w, live_h), radius=[14])
        Color(*C.CYAN[:3], 0.3)
        Line(rounded_rectangle=(live_x, live_y, live_w, live_h, 14), width=1)

        # Pulsing dot
        dot_a = 0.8 + 0.2 * math.sin(self.pulse_phase * 3)
        Color(*C.CYAN[:3], dot_a)
        dot_sz = int(dp(6))
        Ellipse(pos=(live_x + int(dp(10)), live_y + live_h // 2 - dot_sz // 2), size=(dot_sz, dot_sz))

        self._text("LIVE", live_x + int(dp(22)), live_y + int(dp(5)), font_size=sp(14), color=_rgba(C.CYAN, 0.8), bold=True)

        # Buttons (Left side)
        btn_w, btn_h = int(dp(110)), int(dp(40))
        btn_y = bar_h // 2
        self._draw_reg_button(int(dp(32)) + btn_w//2, btn_y, btn_w, btn_h, "Skip", C.TEXT_SEC)
        
        btn_w_reg = int(dp(130))
        self._draw_reg_button(int(dp(160)) + btn_w_reg//2, btn_y, btn_w_reg, btn_h, "Register", C.CYAN)
        
        self._draw_reg_button(int(dp(310)) + btn_w//2, btn_y, btn_w, btn_h, "Quit", C.TEXT_SEC)

    # ── toast ────────────────────────────────────────────────────────────────
    def _draw_toast(self, w, h):
        alpha = min(self.toast_alpha, 1.0)
        if alpha < 0.01:
            return

        # Slide up animation
        toast_y = int(dp(80)) - int((1.0 - alpha) * dp(20))
        text = self.toast_message
        
        # Estimate text width
        tw = len(text) * int(dp(9)) + int(dp(48))
        tx = (w - tw) // 2
        th = int(dp(48))

        Color(*C.GLASS_BG[:3], alpha * 0.9)
        RoundedRectangle(pos=(tx, toast_y), size=(tw, th), radius=[16])
        Color(*C.CYAN[:3], alpha * 0.4)
        Line(rounded_rectangle=(tx, toast_y, tw, th, 16), width=1.5)
        self._text(text, tx + int(dp(24)), toast_y + int(dp(14)), font_size=sp(20), color=_rgba(C.TEXT, alpha))

    # ── text helper ──────────────────────────────────────────────────────────
    def _text(self, text, x, y, font_size=sp(20), color=None, bold=False):
        if color is None:
            color = C.TEXT
        tex = self._text_cache.get(text, font_size, bold, color)
        if tex:
            Color(1, 1, 1, 1)  # Color is baked into the texture
            Rectangle(texture=tex, pos=(int(x), int(y)), size=tex.size)

    # ── registration UI & logic ──

    def on_touch_down(self, touch):
        # Let the text input handle its own touches first
        if self._text_input:
            if self._text_input.collide_point(touch.x, touch.y):
                return super().on_touch_down(touch)

        mx, my = self.to_local(touch.x, touch.y)

        if self.page == "player":
            bar_h = int(dp(72))
            cy = bar_h // 2
            btn_w, btn_h = int(dp(110)), int(dp(40))
            btn_w_reg = int(dp(130))
            
            if self._hit_btn(int(dp(32)) + btn_w//2, cy, btn_w, btn_h, mx, my):
                self.player.skip()
                return True
            elif self._hit_btn(int(dp(160)) + btn_w_reg//2, cy, btn_w_reg, btn_h, mx, my):
                self.page = "register"
                self.reg_state = REG_STATE_HOME
                self._remove_text_input()
                self.dirty = True
                return True
            elif self._hit_btn(int(dp(310)) + btn_w//2, cy, btn_w, btn_h, mx, my):
                from kivy.app import App
                App.get_running_app().stop()
                return True
            return super().on_touch_down(touch)

        # ── Registration page touch handling ──
        self.dirty = True
        w, h = self.width, self.height
        cx = w // 2
        cy = h // 2

        # Back button (top-right)
        back_x = w - int(dp(80))
        back_y = h - int(dp(50))
        if self._hit_btn(back_x, back_y, int(dp(120)), int(dp(44)), mx, my):
            if self.reg_state in (REG_STATE_HOME, REG_STATE_DONE):
                self.page = "player"
                self._remove_text_input()
            else:
                self._reset_reg()
                self.reg_state = REG_STATE_HOME
            return True

        if self.reg_state == REG_STATE_HOME:
            if self._hit_btn(cx, cy - int(dp(20)), int(dp(320)), int(dp(60)), mx, my):
                self.reg_state = REG_STATE_WAITING_SCAN
                return True
            if self._hit_btn(cx, cy - int(dp(100)), int(dp(320)), int(dp(60)), mx, my):
                self._load_cards_list()
                self.reg_state = REG_STATE_LIST
                return True

        elif self.reg_state == REG_STATE_INPUT_TITLE:
            btn_y = cy - int(dp(100))
            if self._hit_btn(cx, btn_y, int(dp(240)), int(dp(54)), mx, my):
                self._submit_text_input()
                return True

        elif self.reg_state == REG_STATE_PICK_FILE:
            list_x = cx - int(dp(280))
            list_y_start = cy + int(dp(120))
            item_h = int(dp(54))
            visible = min(10, len(self.local_files) - self.file_scroll)
            for i in range(visible):
                idx = self.file_scroll + i
                iy = list_y_start - i * item_h
                if list_x < mx < list_x + int(dp(560)) and iy - item_h < my < iy:
                    self.file_selected = idx
                    filename = self.local_files[idx]
                    self.reg_url = filename
                    title = self._get_title_from_file(filename)
                    preset = title if title else os.path.splitext(filename)[0]
                    self.reg_state = REG_STATE_INPUT_TITLE
                    self._show_text_input("Enter Song Title:", preset)
                    return True

        elif self.reg_state == REG_STATE_CONFIRM:
            if self._hit_btn(cx - int(dp(120)), cy - int(dp(120)), int(dp(200)), int(dp(54)), mx, my):
                self._do_confirm_register()
                return True
            if self._hit_btn(cx + int(dp(120)), cy - int(dp(120)), int(dp(200)), int(dp(54)), mx, my):
                self._reset_reg()
                self.reg_state = REG_STATE_HOME
                return True

        elif self.reg_state == REG_STATE_DONE:
            if self._hit_btn(cx, cy - int(dp(120)), int(dp(280)), int(dp(54)), mx, my):
                self._reset_reg()
                self.reg_state = REG_STATE_HOME
                return True

        elif self.reg_state == REG_STATE_LIST:
            list_x = cx - int(dp(360))
            list_y_start = cy + int(dp(220))
            item_h = int(dp(64))
            visible = min(9, len(self.cards_list) - self.cards_scroll)
            for i in range(visible):
                idx = self.cards_scroll + i
                if idx >= len(self.cards_list):
                    break
                iy = list_y_start - i * item_h
                del_x = list_x + int(dp(640))
                if del_x < mx < del_x + int(dp(60)) and iy - item_h < my < iy:
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
            self.dirty = True
        elif self.reg_state == REG_STATE_LIST:
            self.cards_scroll = max(0, self.cards_scroll - direction * 3)
            self.cards_scroll = min(self.cards_scroll, max(0, len(self.cards_list) - 9))
            self.dirty = True

    # ── text input management ────────────────────────────────────────────────
    def _show_text_input(self, label, preset=""):
        self._remove_text_input()
        self._input_label = label
        ti = TextInput(
            text=preset,
            multiline=False,
            size_hint=(None, None),
            size=(min(int(dp(1200)), self.width - int(dp(100))), int(dp(80))),
            pos_hint={'center_x': 0.5, 'center_y': 0.5},
            font_size=sp(40),
            background_color=(C.GLASS_BG[0], C.GLASS_BG[1], C.GLASS_BG[2], 0.9),
            foreground_color=C.TEXT,
            cursor_color=C.CYAN,
            padding=[16, 12],
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

    def _submit_text_input(self):
        text = self._get_input_text()
        if not text.strip():
            return
        if self.reg_state == REG_STATE_INPUT_TITLE:
            self.reg_title = text.strip()
            self._remove_text_input()
            self.reg_state = REG_STATE_CONFIRM
        self.dirty = True

    def _do_confirm_register(self):
        from registry import Registry
        registry = Registry(self.config.db_path)
        registry.register_card(self.scanned_uid, self.reg_title, self.reg_url)
        self.show_toast(f"Registered: {self.reg_title}")
        self.reg_state = REG_STATE_DONE
        self.dirty = True

    def _reset_reg(self):
        self.scanned_uid = None
        self.existing_card = None
        self.selected_source = None
        self.reg_title = ""
        self.reg_url = ""
        self._remove_text_input()
        self.dirty = True

    def _draw_reg_header(self, w, h):
        self._text("uTune", int(dp(32)), h - int(dp(50)), font_size=sp(36), color=C.CYAN, bold=True)
        self._text("CARD REGISTRATION", int(dp(160)), h - int(dp(44)), font_size=sp(20), color=_rgba(C.VIOLET, 0.8), bold=True)
        Color(1, 1, 1, 0.1)
        Rectangle(pos=(int(dp(32)), h - int(dp(72))), size=(w - int(dp(64)), 1))
        
        label = "← Player" if self.reg_state in (REG_STATE_HOME, REG_STATE_DONE) else "← Back"
        self._draw_reg_button(w - int(dp(80)), h - int(dp(50)), int(dp(120)), int(dp(44)), label, C.TEXT_SEC)

    def _draw_reg_home(self, w, h):
        cx, cy = w // 2, h // 2
        self._text("Card Manager", cx - int(dp(110)), cy + int(dp(120)), font_size=sp(36), color=C.TEXT, bold=True)
        self._text(f"Cards registered: {self._card_count()}", cx - int(dp(110)), cy + int(dp(70)), font_size=sp(24), color=C.TEXT_SEC)
        
        self._draw_reg_button(cx, cy - int(dp(20)), int(dp(320)), int(dp(60)), "Register New Card", C.CYAN)
        self._draw_reg_button(cx, cy - int(dp(100)), int(dp(320)), int(dp(60)), "View All Cards", C.VIOLET)

    def _draw_reg_waiting_scan(self, w, h):
        cx, cy = w // 2, h // 2
        radius = int(dp(70)) + int(dp(12) * math.sin(self.pulse_phase * 2))
        ring_a = 0.4 + 0.2 * math.sin(self.pulse_phase * 3)
        Color(*C.CYAN[:3], ring_a)
        Line(circle=(cx, cy, radius), width=2)
        inner_a = 0.2 + 0.1 * math.sin(self.pulse_phase * 2 + 1)
        Color(*C.CYAN[:3], inner_a)
        Ellipse(pos=(cx - int(dp(40)), cy - int(dp(40))), size=(int(dp(80)), int(dp(80))))
        self._text("Scan RFID Card", cx - int(dp(120)), cy - radius - int(dp(60)), font_size=sp(36), color=C.TEXT, bold=True)
        self._text("Place card on the reader...", cx - int(dp(140)), cy - radius - int(dp(100)), font_size=sp(24), color=C.TEXT_MUTED)

    def _draw_reg_input_screen(self, w, h, label):
        cx, cy = w // 2, h // 2
        self._text(label, cx - int(dp(180)), cy + int(dp(140)), font_size=sp(36), color=C.TEXT, bold=True)
        self._text("Type below, then tap Submit or press ENTER", cx - int(dp(220)), cy + int(dp(90)), font_size=sp(22), color=C.TEXT_MUTED)
        self._draw_reg_button(cx, cy - int(dp(120)), int(dp(260)), int(dp(64)), "Submit", C.CYAN)

    def _draw_reg_pick_file(self, w, h):
        cx, cy = w // 2, h // 2
        self._text("Select Audio File", cx - int(dp(120)), cy + int(dp(240)), font_size=sp(36), color=C.TEXT, bold=True)
        self._text(f"Folder: {self.config.music_folder}", cx - int(dp(220)), cy + int(dp(200)), font_size=sp(20), color=C.TEXT_MUTED)
        
        if not self.local_files:
            self._text("No audio files found in music folder", cx - int(dp(200)), cy, font_size=sp(26), color=C.RED)
            return

        list_x = cx - int(dp(280))
        list_y = cy + int(dp(120))
        item_h = int(dp(54))
        visible = min(10, len(self.local_files) - self.file_scroll)
        
        for i in range(visible):
            idx = self.file_scroll + i
            iy = list_y - i * item_h
            is_sel = idx == self.file_selected
            bg_a = 0.6 if is_sel else 0.3
            Color(*C.GLASS_BG[:3], bg_a)
            RoundedRectangle(pos=(list_x, iy - item_h + int(dp(4))), size=(int(dp(560)), item_h - int(dp(4))), radius=[6])
            
            border_c = C.CYAN if is_sel else C.VIOLET
            border_a = 0.5 if is_sel else 0.2
            Color(*border_c[:3], border_a)
            Line(rounded_rectangle=(list_x, iy - item_h + int(dp(4)), int(dp(560)), item_h - int(dp(4)), 6), width=1.5 if is_sel else 1)
            
            ext = os.path.splitext(self.local_files[idx])[1].upper()
            self._text(ext, list_x + int(dp(16)), iy - item_h + int(dp(16)), font_size=sp(20), color=C.CYAN if is_sel else C.TEXT_MUTED)
            fname = self.local_files[idx]
            if len(fname) > 42:
                fname = fname[:39] + "..."
            self._text(fname, list_x + int(dp(80)), iy - item_h + int(dp(16)), font_size=sp(22), color=C.TEXT if is_sel else C.TEXT_SEC)

    def _draw_reg_confirm(self, w, h):
        cx, cy = w // 2, h // 2
        self._text("Confirm Registration", cx - int(dp(160)), cy + int(dp(140)), font_size=sp(36), color=C.TEXT, bold=True)
        self._text(f"UID: {self.scanned_uid}", cx - int(dp(160)), cy + int(dp(80)), font_size=sp(26), color=C.TEXT_SEC)
        self._text(f"Title: {self.reg_title}", cx - int(dp(160)), cy + int(dp(30)), font_size=sp(26), color=C.TEXT_SEC)
        self._draw_reg_button(cx - int(dp(120)), cy - int(dp(120)), int(dp(200)), int(dp(54)), "Confirm", C.CYAN)
        self._draw_reg_button(cx + int(dp(120)), cy - int(dp(120)), int(dp(200)), int(dp(54)), "Cancel", C.TEXT_SEC)

    def _draw_reg_done(self, w, h):
        cx, cy = w // 2, h // 2
        Color(*C.GREEN[:3], 0.2)
        Ellipse(pos=(cx - int(dp(60)), cy + int(dp(70))), size=(int(dp(120)), int(dp(120))))
        Color(*C.GREEN[:3], 0.8)
        Line(circle=(cx, cy + int(dp(130)), int(dp(60))), width=4)
        self._text("✓", cx - int(dp(16)), cy + int(dp(100)), font_size=sp(60), color=C.GREEN, bold=True)
        self._text("Success!", cx - int(dp(70)), cy, font_size=sp(36), color=C.TEXT, bold=True)
        self._draw_reg_button(cx, cy - int(dp(120)), int(dp(280)), int(dp(54)), "Register Another", C.CYAN)

    def _draw_reg_cards_list(self, w, h):
        cx, cy = w // 2, h // 2
        self._text("Registered Cards", cx - int(dp(140)), cy + int(dp(300)), font_size=sp(36), color=C.TEXT, bold=True)
        list_x = cx - int(dp(360))
        list_y = cy + int(dp(220))
        item_h = int(dp(64))
        
        if not self.cards_list:
            self._text("No cards registered yet", cx - int(dp(140)), cy, font_size=sp(26), color=C.TEXT_MUTED)
            return

        visible = min(9, len(self.cards_list) - self.cards_scroll)
        for i in range(visible):
            idx = self.cards_scroll + i
            if idx >= len(self.cards_list):
                break
            iy = list_y - i * item_h
            card = self.cards_list[idx]
            
            Color(*C.GLASS_BG[:3], 0.5)
            RoundedRectangle(pos=(list_x, iy - item_h + int(dp(6))), size=(int(dp(720)), item_h - int(dp(6))), radius=[6])
            Color(1, 1, 1, 0.1)
            Line(rounded_rectangle=(list_x, iy - item_h + int(dp(6)), int(dp(720)), item_h - int(dp(6)), 6), width=1)
            
            uid_str = card["uid"][:8] + ".."
            self._text(uid_str, list_x + int(dp(16)), iy - item_h + int(dp(20)), font_size=sp(20), color=C.CYAN)
            title_text = card.get("title", "Unknown")
            if len(title_text) > 34:
                title_text = title_text[:31] + "..."
            self._text(title_text, list_x + int(dp(160)), iy - item_h + int(dp(20)), font_size=sp(24), color=C.TEXT)
            
            is_url = card["url"].startswith("http")
            src = "YT" if is_url else "LOCAL"
            src_c = C.CYAN if is_url else C.VIOLET
            self._text(src, list_x + int(dp(560)), iy - item_h + int(dp(20)), font_size=sp(20), color=src_c)
            
            dx = list_x + int(dp(640))
            dy = iy - item_h + int(dp(12))
            Color(*C.RED[:3], 0.2)
            RoundedRectangle(pos=(dx, dy), size=(int(dp(60)), int(dp(40))), radius=[6])
            Color(*C.RED[:3], 0.6)
            Line(rounded_rectangle=(dx, dy, int(dp(60)), int(dp(40)), 6), width=1.5)
            self._text("X", dx + int(dp(22)), dy + int(dp(8)), font_size=sp(24), color=C.RED, bold=True)

    def _draw_reg_button(self, cx, cy, bw, bh, text, color):
        bx = cx - bw // 2
        by = cy - bh // 2
        
        # Glass fill
        Color(*C.GLASS_BG[:3], 0.8)
        RoundedRectangle(pos=(bx, by), size=(bw, bh), radius=[8])
        
        # Border
        Color(*color[:3], 0.6)
        Line(rounded_rectangle=(bx, by, bw, bh, 8), width=1.5)
        
        # Subtle top highlight (3D effect)
        Color(*color[:3], 0.2)
        Rectangle(pos=(bx + int(dp(4)), by + bh - int(dp(4))), size=(bw - int(dp(8)), int(dp(2))))
        
        # Text
        self._text(text, cx - len(text) * int(dp(5)), cy - int(dp(12)), font_size=sp(20), color=color, bold=True)

    def _hit_btn(self, cx, cy, bw, bh, mx, my):
        bx = cx - bw // 2
        by = cy - bh // 2
        return bx < mx < bx + bw and by < my < by + bh

    def _scan_local_files(self):
        folder = self.config.music_folder
        if not os.path.isdir(folder):
            self.local_files = []
            return
        scan_exts = AUDIO_EXTENSIONS | {".webm", ".mp4"}
        self.local_files = sorted(
            f for f in os.listdir(folder)
            if os.path.splitext(f)[1].lower() in scan_exts
        )
        self.file_scroll = 0
        self.file_selected = 0 if self.local_files else -1
        self.dirty = True

    def _load_cards_list(self):
        import sqlite3
        try:
            with sqlite3.connect(self.config.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT uid, title, file_path FROM cards ORDER BY date_added DESC")
                self.cards_list = [
                    {"uid": r[0], "title": r[1], "url": r[2]} for r in cursor.fetchall()
                ]
        except Exception:
            self.cards_list = []
        self.cards_scroll = 0
        self.dirty = True

    def _card_count(self):
        import sqlite3
        try:
            with sqlite3.connect(self.config.db_path) as conn:
                return conn.cursor().execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        except Exception:
            return 0

    def _get_title_from_file(self, filename):
        import json

        file_path = os.path.join(self.config.music_folder, filename.replace('\\', '/'))
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
                title = data.get("title")
                artist = data.get("artist") or data.get("uploader") or data.get("channel")
                if title and artist:
                    return f"{artist} - {title}"
                elif title:
                    return title
            except Exception:
                pass
        return None
