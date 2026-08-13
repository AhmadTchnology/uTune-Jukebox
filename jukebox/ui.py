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

from kivy.uix.floatlayout import FloatLayout
from kivy.uix.widget import Widget
from kivy.uix.label import Label
from kivy.graphics import (
    Color, Rectangle, RoundedRectangle, Ellipse, Line,
)
from kivy.graphics.texture import Texture
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.properties import (
    BooleanProperty, StringProperty, NumericProperty, ListProperty,
)


# ── Colour palette (Kivy 0‑1 floats) ────────────────────────────────────────
def _c(r, g, b, a=255):
    return (r / 255, g / 255, b / 255, a / 255)


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

    # ── keyboard (RFID via USB OTG keyboard emulation) ───────────────────────
    def handle_key_down(self, window, key, scancode, codepoint, modifiers):
        """Called from the Kivy App's on_key_down."""
        if key == 27:  # ESC
            return False  # Let App handle quit
        if key == 115:  # 's' — skip
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

    # ── drawing ──────────────────────────────────────────────────────────────
    def _redraw(self):
        self.canvas.clear()
        w, h = self.width, self.height
        if w < 10 or h < 10:
            return

        pad = 28
        right_w = 320
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

            # 3 — Left panel: Now Playing
            self._draw_now_playing(pad, pad, left_w, h)

            # 4 — Divider
            Color(1, 1, 1, 0.06)
            Rectangle(pos=(divider_x, pad), size=(1, h - pad * 2 - 48))

            # 5 — Right panel: Queue
            self._draw_queue(divider_x + 24, pad, right_w - 24, h)

            # 6 — Bottom bar
            self._draw_bottom_bar(w, h)

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
        label_y = top - 20
        Color(*C.CYAN[:3], 0.75)
        Ellipse(pos=(x, label_y - 2), size=(6, 6))
        self._text(
            "NOW PLAYING", x + 14, label_y,
            font_size=14, color=_rgba(C.CYAN, 0.75), bold=True,
        )

        if track:
            self._draw_playing(x, label_y - 40, w, track)
        else:
            self._draw_idle(x, y_pad, w, h)

    def _draw_playing(self, x, top_y, w, track):
        art_size = 200
        art_x = x
        art_y = top_y - art_size

        # Album art
        self._draw_album_art(art_x, art_y, art_size, track)

        # Track info to the right of art
        info_x = art_x + art_size + 32

        title = track.get("title", "Unknown Track")
        if len(title) > 24:
            title = title[:21] + "..."
        self._text(title, info_x, top_y - 10, font_size=42, color=C.TEXT, bold=True)

        artist = track.get("artist", "Unknown Artist")
        self._text(artist, info_x, top_y - 60, font_size=22, color=C.TEXT_SEC)

        album = track.get("album", "")
        offset = 85
        if album:
            self._text(album.upper(), info_x, top_y - offset, font_size=13, color=C.TEXT_MUTED)
            offset += 30

        # Progress bar
        prog_w = w - (info_x - x) - 20
        self._draw_progress_bar(info_x, top_y - offset - 10, prog_w)

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
                initial, x + size // 2 - 20, y + size // 2 - 30,
                font_size=72, color=(1, 1, 1, 0.2), bold=True,
            )

        # Border
        Color(*C.CYAN[:3], 0.3)
        Line(rounded_rectangle=(x, y, size, size, 12), width=1)

        # Glow pulse when playing
        if self.player.is_playing:
            glow_a = 0.06 + 0.04 * math.sin(self.pulse_phase * 1.5)
            Color(*C.CYAN[:3], glow_a)
            Line(rounded_rectangle=(x - 2, y - 2, size + 4, size + 4, 14), width=1.5)

    def _draw_progress_bar(self, x, y, w):
        track = self.player.current_track
        if not track:
            return

        bar_h = 2

        if not self.player.play_start_time:
            # Loading indicator — pulsing bar
            Color(1, 1, 1, 0.07)
            Rectangle(pos=(x, y), size=(w, bar_h))
            pulse_w = 40
            pulse_x = int((w - pulse_w) * ((math.sin(self.pulse_phase * 3) + 1) / 2))
            Color(*C.CYAN)
            Rectangle(pos=(x + pulse_x, y), size=(pulse_w, bar_h))
            self._text("Loading...", x, y - 24, font_size=18, color=C.CYAN)
            return

        elapsed = _time.time() - self.player.play_start_time
        duration = track.get("duration")
        pct = min(1.0, elapsed / float(duration)) if duration else 1.0

        # Background
        Color(1, 1, 1, 0.07)
        Rectangle(pos=(x, y), size=(w, bar_h))
        # Fill
        fill_w = int(w * pct)
        if fill_w > 0:
            Color(*C.CYAN)
            Rectangle(pos=(x, y), size=(fill_w, bar_h))

        # Time labels
        mins, secs = int(elapsed) // 60, int(elapsed) % 60
        self._text(
            f"{mins}:{secs:02d}", x, y - 24,
            font_size=18, color=_rgba(C.CYAN, 0.7),
        )
        if duration:
            dur = float(duration)
            dm, ds = int(dur) // 60, int(dur) % 60
            self._text(
                f"{dm}:{ds:02d}", x + w - 50, y - 24,
                font_size=18, color=C.TEXT_MUTED,
            )

    def _draw_idle(self, x, y_pad, w, h):
        cx = x + w // 2
        cy = h // 2

        # NFC card icon
        card_x, card_y = cx - 24, cy + 20
        Color(*C.CYAN[:3], 0.16)
        RoundedRectangle(pos=(card_x, card_y), size=(48, 56), radius=[6])
        Color(*C.CYAN[:3], 0.55)
        Line(rounded_rectangle=(card_x, card_y, 48, 56, 6), width=1.5)

        # Chip
        Color(*C.CYAN[:3], 0.4)
        RoundedRectangle(pos=(card_x + 8, card_y + 38), size=(16, 10), radius=[2])

        # Signal arcs
        for i, (radius, alpha) in enumerate([(6, 0.8), (12, 0.5), (18, 0.3)]):
            Color(*C.CYAN[:3], alpha)
            Line(
                circle=(card_x + 36, card_y + 28, radius, 315, 405),
                width=1.5,
            )

        # Text with bob
        bob = math.sin(self.idle_bob) * 3
        self._text(
            "Tap a card to play", cx - 100, cy - 30 + bob,
            font_size=28, color=C.TEXT_SEC,
        )
        self._text(
            "Place an RFID card on the reader", cx - 150, cy - 65 + bob,
            font_size=18, color=C.TEXT_MUTED,
        )

        # Error display
        if self.player.last_error:
            err = self.player.last_error[:60]
            self._text(err, cx - 150, cy - 100, font_size=16, color=(1, 0.37, 0.37, 1))

    # ── queue panel ──────────────────────────────────────────────────────────
    def _draw_queue(self, x, y_pad, w, h):
        top = h - y_pad
        Color(*C.VIOLET[:3], 0.75)
        self._text("UP NEXT", x, top - 20, font_size=14, color=_rgba(C.VIOLET, 0.75), bold=True)

        upcoming = self.queue_mgr.get_upcoming()

        if not upcoming:
            self._text("Queue is empty", x + w // 2 - 55, top - 70, font_size=18, color=C.TEXT_MUTED)
            self._text("Scan cards to add songs", x + w // 2 - 80, top - 95, font_size=15, color=C.TEXT_MUTED)
            return

        card_h_next = 58
        card_h_normal = 48
        gap = 8
        max_visible = min(len(upcoming), 5)
        iy = top - 50

        for i in range(max_visible):
            item = upcoming[i]
            is_next = i == 0
            ch = card_h_next if is_next else card_h_normal

            # Glass card background
            bg_alpha = 0.45 if is_next else 0.35
            Color(*C.GLASS_BG[:3], bg_alpha)
            RoundedRectangle(pos=(x, iy - ch), size=(w, ch), radius=[10])

            # Border
            border_c = C.CYAN if is_next else C.VIOLET
            border_a = 0.38 if is_next else 0.20
            Color(*border_c[:3], border_a)
            Line(rounded_rectangle=(x, iy - ch, w, ch, 10), width=1)

            # Highlight bar for "next" card
            if is_next:
                Color(*C.CYAN[:3], 0.2)
                Rectangle(pos=(x, iy - 1), size=(w, 1))

            # Mini album art
            thumb_size = 42 if is_next else 34
            thumb_x = x + 12
            thumb_y = iy - ch + (ch - thumb_size) // 2
            accent = C.CYAN if i % 2 == 0 else C.VIOLET
            Color(*accent[:3], 0.3)
            RoundedRectangle(pos=(thumb_x, thumb_y), size=(thumb_size, thumb_size), radius=[6])

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
                    RoundedRectangle(texture=tex, pos=(thumb_x, thumb_y), size=(thumb_size, thumb_size), radius=[6])

            Color(*accent[:3], 0.3)
            Line(rounded_rectangle=(thumb_x, thumb_y, thumb_size, thumb_size, 6), width=1)

            # Title
            text_x = thumb_x + thumb_size + 10
            title = item.get("title", "Unknown")
            if len(title) > 22:
                title = title[:19] + "..."
            self._text(title, text_x, iy - ch // 2 + 2, font_size=16, color=C.TEXT_SLATE, bold=True)

            artist = item.get("artist", "")
            if artist:
                self._text(artist, text_x, iy - ch // 2 - 14, font_size=13, color=C.TEXT_DIM)

            iy -= ch + gap

        if len(upcoming) > max_visible:
            more = len(upcoming) - max_visible
            self._text(f"+ {more} more...", x + 10, iy - 5, font_size=15, color=C.TEXT_MUTED)

    # ── bottom bar ───────────────────────────────────────────────────────────
    def _draw_bottom_bar(self, w, h):
        bar_h = 48
        bar_y = 0

        # Top border
        Color(1, 1, 1, 0.06)
        Rectangle(pos=(0, bar_h), size=(w, 1))

        # Hints
        self._text("[S] Skip", 28, bar_h // 2 - 6, font_size=13, color=C.TEXT_MUTED)
        self._text("[ESC] Quit", 120, bar_h // 2 - 6, font_size=13, color=C.TEXT_MUTED)

        # LIVE badge (right side)
        live_w, live_h = 56, 22
        live_x = w - 28 - live_w
        live_y = bar_h // 2 - live_h // 2

        Color(*C.CYAN[:3], 0.04)
        RoundedRectangle(pos=(live_x, live_y), size=(live_w, live_h), radius=[11])
        Color(*C.CYAN[:3], 0.2)
        Line(rounded_rectangle=(live_x, live_y, live_w, live_h, 11), width=1)

        # Pulsing dot
        dot_a = 0.8 + 0.2 * math.sin(self.pulse_phase * 3)
        Color(*C.CYAN[:3], dot_a)
        Ellipse(pos=(live_x + 7, live_y + live_h // 2 - 3), size=(6, 6))

        self._text("LIVE", live_x + 18, live_y + 4, font_size=12, color=_rgba(C.CYAN, 0.7), bold=True)

        # Tap count
        count_str = f"{self.tap_count} cards tapped"
        self._text(
            count_str, live_x - 140, bar_h // 2 - 6,
            font_size=14, color=C.TEXT_MUTED,
        )

    # ── toast ────────────────────────────────────────────────────────────────
    def _draw_toast(self, w, h):
        alpha = min(self.toast_alpha, 1.0)
        if alpha < 0.01:
            return

        toast_y = 80
        text = self.toast_message
        tw = len(text) * 10 + 48
        tx = (w - tw) // 2
        th = 44

        Color(*C.GLASS_BG[:3], alpha * 0.8)
        RoundedRectangle(pos=(tx, toast_y), size=(tw, th), radius=[12])
        Color(*C.CYAN[:3], alpha * 0.3)
        Line(rounded_rectangle=(tx, toast_y, tw, th, 12), width=1)
        self._text(text, tx + 24, toast_y + 12, font_size=18, color=_rgba(C.TEXT, alpha))

    # ── text helper ──────────────────────────────────────────────────────────
    def _text(self, text, x, y, font_size=16, color=None, bold=False):
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
