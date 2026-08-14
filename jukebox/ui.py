"""uTune Jukebox UI — Kivy canvas renderer.

Matches the Full-Screen Jukebox Interface reference at 1920×1200 (Nexus 7 2013
landscape). Canvas-based for performance on low-power Android hardware.
"""
import io
import json
import math
import os
import sqlite3
import time as _time
import collections

from kivy.uix.floatlayout import FloatLayout
from kivy.uix.textinput import TextInput
from kivy.graphics import Color, Rectangle, RoundedRectangle, Ellipse, Line
from kivy.graphics.texture import Texture
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.metrics import sp
from kivy.properties import StringProperty, NumericProperty

from player import AUDIO_EXTENSIONS


# ── Design canvas (Nexus 7 2013 landscape) ───────────────────────────────────
DESIGN_W = 1920
DESIGN_H = 1200


def _c(r, g, b, a=255):
    return (r / 255, g / 255, b / 255, a / 255)


def _rgba(c, alpha=None):
    return (c[0], c[1], c[2], alpha if alpha is not None else c[3])


def _scale(v, w, h):
    return int(v * min(w / DESIGN_W, h / DESIGN_H))


# ── Easing (mirrors reference CSS cubic-bezier curves) ───────────────────────

def _ease_out_back(t):
    """cubic-bezier(0.22, 1, 0.36, 1) overshoot."""
    if t >= 1.0:
        return 1.0
    t = max(0.0, min(1.0, t))
    c1 = 1.70158
    c3 = c1 + 1
    t -= 1
    return 1 + c3 * t * t * t + c1 * t * t


def _card_slide_x(t, distance):
    """card-slide-in keyframes: 0→60px, 60%→-4px, 100%→0."""
    if t <= 0:
        return distance, 0.0
    if t >= 1:
        return 0, 1.0
    if t < 0.6:
        p = t / 0.6
        x = distance * (1 - p) + (-4) * p
        return x, p
    p = (t - 0.6) / 0.4
    return -4 * (1 - p), 1.0


def _flash_alpha(progress):
    """screen-flash keyframes."""
    if progress < 0.15:
        return progress / 0.15
    if progress < 0.60:
        return 1.0 - (progress - 0.15) / 0.45 * 0.6
    return max(0.0, 0.4 * (1.0 - (progress - 0.60) / 0.40))


# ── Palette (Full-Screen Jukebox Interface) ──────────────────────────────────
class C:
    BG = _c(6, 8, 16)
    BG_INDIGO = _c(18, 16, 58)
    BG_VIOLET = _c(26, 11, 46)
    CYAN = _c(34, 211, 238)
    VIOLET = _c(139, 92, 246)
    VIOLET_DIM = _c(109, 40, 217)
    GLASS = _c(30, 27, 75)
    TEXT = _c(241, 245, 249)
    TEXT_SEC = _c(148, 163, 184)
    TEXT_MUTED = _c(100, 116, 139)
    TEXT_DIM = _c(71, 85, 105)
    TEXT_SLATE = _c(226, 232, 240)
    TEXT_FAINT = _c(51, 65, 85)
    RED = _c(239, 68, 68)
    GREEN = _c(34, 197, 94)


REG_STATE_HOME = "home"
REG_STATE_WAITING_SCAN = "waiting_scan"
REG_STATE_PICK_SOURCE = "pick_source"
REG_STATE_INPUT_URL = "input_url"
REG_STATE_DOWNLOADING = "downloading"
REG_STATE_PICK_FILE = "pick_file"
REG_STATE_INPUT_TITLE = "input_title"
REG_STATE_INPUT_ARTIST = "input_artist"
REG_STATE_CONFIRM = "confirm"
REG_STATE_DONE = "done"
REG_STATE_LIST = "list"

FONT_DISPLAY = "NotoSans-Regular.ttf"
FONT_MONO = "fonts/JetBrainsMono.ttf"


def _resolve_font(path, fallback="Roboto"):
    return path if os.path.exists(path) else fallback


def _load_image_bytes(raw_bytes):
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
    """LRU cache for CoreLabel textures."""

    def __init__(self, max_size=256):
        self.cache = collections.OrderedDict()
        self.max_size = max_size
        self._display = _resolve_font(FONT_DISPLAY)
        self._mono = _resolve_font(FONT_MONO)
        self._display_bold = _resolve_font("NotoSans-Bold.ttf", fallback=self._display)

    def get(self, text, font_size, bold, color, mono=False):
        from kivy.core.text import Label as CoreLabel

        if mono:
            font_name = self._mono
        else:
            font_name = self._display_bold if bold else self._display
            # Let Kivy handle bold if we're falling back
            bold = bold if font_name == self._display else False

        key = (text, font_size, bold, mono, tuple(color), font_name)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]

        cl = CoreLabel(
            text=str(text),
            font_size=font_size,
            bold=bold,
            color=color,
            font_name=font_name,
        )
        cl.refresh()
        tex = cl.texture
        self.cache[key] = tex
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)
        return tex


class UI(FloatLayout):
    """Root jukebox widget — player view + card registration overlay."""

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
        self.ripple_phase = 0.0
        self._page_blend = 0.0

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
        self._queue_slide = {}
        self._new_queue_uid = None
        self._new_queue_until = 0
        self._progress_smooth = 0.0

        self.player.on_status_change = self._on_player_status

        self.page = "player"
        self._target_page = "player"
        self.reg_state = REG_STATE_HOME
        self.scanned_uid = None
        self.existing_card = None
        self.selected_source = None
        self.reg_title = ""
        self.reg_artist = ""
        self.reg_url = ""
        self.download_progress = ""

        self.local_files = []
        self.file_scroll = 0
        self.file_selected = -1
        self.cards_list = []
        self.cards_scroll = 0

        self._text_input = None
        self._input_label = ""
        self._btn_press = None

        self.bind(size=self._on_resize)
        self.dirty = True
        self._bg_size = (0, 0)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self):
        self.running = True
        Clock.schedule_interval(self._tick, 1.0 / self.config.ui_fps)

    def stop_ui(self):
        self.running = False
        Clock.unschedule(self._tick)

    def _on_resize(self, *_args):
        self.dirty = True
        self._bg_size = (0, 0)

    def _tick(self, dt):
        if not self.running:
            return

        animate = False
        self.pulse_phase += dt * 2.0
        self.idle_bob += dt * 0.8
        self.ripple_phase += dt * 0.55
        now = _time.time()

        if self._target_page != self.page:
            self._page_blend = min(1.0, self._page_blend + dt * 4)
            if self._page_blend >= 1.0:
                self.page = self._target_page
                self._page_blend = 0.0
            animate = True

        old_toast = self.toast_alpha
        if now < self.toast_end:
            self.toast_alpha = min(1.0, self.toast_alpha + dt * 3)
        else:
            self.toast_alpha = max(0, self.toast_alpha - dt * 2)
        if abs(self.toast_alpha - old_toast) > 0.01:
            animate = True

        if self.flash_active and (now - self.flash_start) > self.flash_duration:
            self.flash_active = False
        if self.flash_active:
            animate = True

        if self._update_queue_anims(dt):
            animate = True

        track = self.player.current_track
        if track and self.player.play_start_time:
            duration = track.get("duration")
            if duration:
                elapsed = _time.time() - self.player.play_start_time
                target = min(1.0, elapsed / float(duration))
                self._progress_smooth += (target - self._progress_smooth) * min(1.0, dt * 4)
                animate = True

        if self.player.is_playing:
            animate = True

        if self.page == "register" or self._target_page == "register":
            animate = True

        if not track and self.page == "player":
            animate = True

        if self.dirty or animate:
            self._redraw()
            self.dirty = False

    def _update_queue_anims(self, dt):
        upcoming = self.queue_mgr.get_upcoming()
        uids = [item.get("uid", f"idx_{i}") for i, item in enumerate(upcoming[:6])]
        changed = False

        for uid in uids:
            if uid not in self._queue_slide:
                self._queue_slide[uid] = 0.0
                changed = True

        for uid in list(self._queue_slide):
            if uid in uids:
                prev = self._queue_slide[uid]
                self._queue_slide[uid] = min(1.0, prev + dt / 0.6)
                if self._queue_slide[uid] != prev:
                    changed = True
            else:
                del self._queue_slide[uid]
                changed = True

        if self._new_queue_uid and _time.time() > self._new_queue_until:
            self._new_queue_uid = None

        return changed

    # ── public API ───────────────────────────────────────────────────────────

    def show_toast(self, message, duration=3.0):
        self.toast_message = message
        self.toast_end = _time.time() + duration
        self.toast_alpha = 1.0
        self.flash_active = True
        self.flash_start = _time.time()
        self.tap_count += 1

        upcoming = self.queue_mgr.get_upcoming()
        if upcoming:
            self._new_queue_uid = upcoming[0].get("uid")
            self._new_queue_until = _time.time() + 0.8
            if self._new_queue_uid:
                self._queue_slide[self._new_queue_uid] = 0.0

        self.dirty = True

    def _on_player_status(self, msg):
        self.status_message = msg
        self.status_end = _time.time() + 5
        self.dirty = True

    def handle_scan(self, uid):
        if self.page == "player" and self._target_page == "player":
            if self.on_scan:
                self.on_scan(uid)
        elif self.page == "register" and self.reg_state == REG_STATE_WAITING_SCAN:
            self.scanned_uid = str(uid)
            from registry import Registry

            self.existing_card = Registry(self.config.db_path).get_card(self.scanned_uid)
            self.selected_source = "local"
            self._scan_local_files()
            self.reg_state = REG_STATE_PICK_FILE
        self.dirty = True

    def handle_key_down(self, window, key, scancode, codepoint, modifiers):
        self.dirty = True
        if self.page == "player" and self._target_page == "player":
            if key == 27:
                return False
            if key == 114 or codepoint == "r":
                self._target_page = "register"
                self.reg_state = REG_STATE_HOME
                self._remove_text_input()
                return True
            if key == 115 or codepoint == "s":
                self.player.skip()
                return True
            if codepoint and codepoint.isdigit():
                self._key_buffer += codepoint
                return True
            if key in (13, 271) and self._key_buffer and self.on_scan:
                self.on_scan(self._key_buffer)
                self._key_buffer = ""
                return True
            return False

        if self.page == "register" or self._target_page == "register":
            return self._handle_reg_key(key, codepoint)
        return False

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

    # ── background (cached) ──────────────────────────────────────────────────

    def _build_background(self, w, h):
        if self._bg_size == (w, h):
            return

        self.canvas.before.clear()
        with self.canvas.before:
            Color(*C.BG)
            Rectangle(pos=self.pos, size=(w, h))

            cx, cy = int(w * 0.2), int(h * 0.5)
            max_r = int(min(w, h) * 0.35)
            for step in range(0, max_r, 20):
                r = max_r - step
                alpha = max(0, 0.14 * (1 - step / max_r))
                Color(*C.BG_INDIGO[:3], alpha)
                Ellipse(pos=(cx - r, cy - r), size=(r * 2, r * 2))

            cx2, cy2 = int(w * 0.05), int(h * 0.55)
            max_r2 = int(min(w, h) * 0.25)
            for step in range(0, max_r2, 24):
                r = max_r2 - step
                alpha = max(0, 0.10 * (1 - step / max_r2))
                Color(*C.BG_VIOLET[:3], alpha)
                Ellipse(pos=(cx2 - r, cy2 - r), size=(r * 2, r * 2))

            grid_step = _scale(60, w, h)
            Color(*C.CYAN[:3], 0.02)
            for gx in range(0, w + 1, grid_step):
                Line(points=[gx, 0, gx, h], width=1)
            for gy in range(0, h + 1, grid_step):
                Line(points=[0, gy, w, gy], width=1)

            buf = bytearray([0, 0, 0, 30] * 2 * w + [0, 0, 0, 0] * 2 * w)
            tex = Texture.create(size=(w, 4), colorfmt="rgba")
            tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
            tex.wrap = "repeat"
            tex.uvsize = (1, h / 4)
            Color(1, 1, 1, 1)
            Rectangle(pos=self.pos, size=(w, h), texture=tex)

        self._bg_size = (w, h)

    # ── main redraw ──────────────────────────────────────────────────────────

    def _redraw(self):
        w, h = self.width, self.height
        if w < 10 or h < 10:
            return

        self._build_background(w, h)
        self.canvas.clear()
        self.canvas.after.clear()

        pad = _scale(28, w, h)
        bottom_h = _scale(56, w, h)
        right_w = _scale(320, w, h)
        divider_x = w - right_w - pad
        left_w = divider_x - pad - _scale(24, w, h)

        with self.canvas:
            if self.page == "player" or self._target_page == "player":
                self._draw_player_layout(pad, bottom_h, left_w, right_w, divider_x, w, h)

            if self.page == "register" or self._target_page == "register":
                if self._target_page == "register" and self.page != "register":
                    overlay_a = self._page_blend
                elif self._target_page == "player" and self.page == "register":
                    overlay_a = 1.0 - self._page_blend
                else:
                    overlay_a = 1.0 if self.page == "register" else 0.0
                if overlay_a > 0.01:
                    Color(0, 0, 0, overlay_a * 0.55)
                    Rectangle(pos=(0, 0), size=(w, h))
                    self._draw_registration(w, h, overlay_a)

        with self.canvas.after:
            if self.flash_active and self.page == "player":
                self._draw_flash(w, h)
            if self.toast_alpha > 0.01:
                self._draw_toast(w, h, pad)

    def _draw_player_layout(self, pad, bottom_h, left_w, right_w, divider_x, w, h):
        self._draw_now_playing(pad, bottom_h, left_w, h, w)

        Color(1, 1, 1, 0.06)
        Rectangle(pos=(divider_x, bottom_h + pad), size=(1, h - bottom_h - pad))

        self._draw_queue(divider_x + _scale(24, w, h), bottom_h, right_w, h, w)
        self._draw_bottom_bar(w, bottom_h, pad, h)

    # ── player: now playing ──────────────────────────────────────────────────

    def _draw_now_playing(self, x, bottom_h, lw, h, w):
        top = h - _scale(28, w, h)
        label_y = top - _scale(20, w, h)

        dot = _scale(6, w, h)
        Color(*C.CYAN[:3], 0.75)
        Ellipse(pos=(x, label_y - 1), size=(dot, dot))
        self._text_mono(
            "NOW PLAYING",
            x + dot + _scale(8, w, h),
            label_y - _scale(2, w, h),
            font_size=sp(_scale(10, w, h)),
            color=_rgba(C.CYAN, 0.75),
            bold=True,
        )

        track = self.player.current_track
        if track:
            self._draw_playing(x, label_y - _scale(36, w, h), lw, h, w, bottom_h, track)
        else:
            self._draw_idle(x, bottom_h, lw, h, w)

    def _draw_playing(self, x, top_y, lw, h, w, bottom_h, track):
        art_size = _scale(200, w, h)
        art_y = top_y - art_size - _scale(8, w, h)
        info_x = x + art_size + _scale(32, w, h)
        info_w = lw - art_size - _scale(32, w, h)

        self._draw_album_art(x, art_y, art_size, track, w, h)

        title_fs = sp(_scale(48, w, h))
        self._text_display(
            track.get("title", "Unknown Track"),
            info_x,
            art_y + art_size - _scale(12, w, h),
            font_size=title_fs,
            color=C.TEXT,
            bold=True,
            max_w=info_w,
        )

        artist_fs = sp(_scale(20, w, h))
        artist_y = art_y + art_size - _scale(68, w, h)
        self._text_display(
            track.get("artist", "Unknown Artist"),
            info_x,
            artist_y,
            font_size=artist_fs,
            color=C.TEXT_SEC,
            max_w=info_w,
        )

        album = track.get("album", "")
        if album:
            self._text_mono(
                album.upper(),
                info_x,
                artist_y - _scale(28, w, h),
                font_size=sp(_scale(10, w, h)),
                color=C.TEXT_DIM,
                max_w=info_w,
            )

        prog_y = bottom_h + _scale(48, w, h)
        self._draw_progress_bar(info_x, prog_y, info_w, w, h)

    def _draw_album_art(self, x, y, size, track, w, h):
        cache_key = f"{track.get('title', '?')}_{'img' if track.get('image_bytes') else 'no'}"
        if self._art_cache_key != cache_key:
            self._art_cache_key = cache_key
            img_bytes = track.get("image_bytes")
            self._art_texture = _load_image_bytes(img_bytes) if img_bytes else None

        radius = _scale(12, w, h)
        shadow = _scale(8, w, h)
        Color(0, 0, 0, 0.4)
        RoundedRectangle(
            pos=(x - shadow // 2, y - shadow),
            size=(size + shadow, size + shadow),
            radius=[radius + 2],
        )

        if self._art_texture:
            Color(1, 1, 1, 1)
            RoundedRectangle(
                texture=self._art_texture,
                pos=(x, y),
                size=(size, size),
                radius=[radius],
            )
        else:
            Color(*C.GLASS[:3], 0.85)
            RoundedRectangle(pos=(x, y), size=(size, size), radius=[radius])
            Color(*C.VIOLET_DIM[:3], 0.25)
            RoundedRectangle(
                pos=(x, y + size // 2),
                size=(size, size // 2),
                radius=[0, 0, radius, radius],
            )
            initial = (track.get("title", "?"))[0].upper()
            self._text_display(
                initial,
                x + size // 2,
                y + size // 2,
                font_size=sp(_scale(56, w, h)),
                color=(1, 1, 1, 0.18),
                bold=True,
                halign="center",
                valign="center"
            )

        border_a = 0.3
        if self.player.is_playing:
            border_a = 0.3 + 0.15 * math.sin(self.pulse_phase * 2.0)
        Color(*C.CYAN[:3], border_a)
        Line(rounded_rectangle=(x, y, size, size, radius), width=1.2)

        if self.player.is_playing:
            glow = 0.10 + 0.08 * math.sin(self.pulse_phase * 2.0)
            Color(*C.CYAN[:3], glow)
            Line(
                rounded_rectangle=(x - 3, y - 3, size + 6, size + 6, radius + 2),
                width=2,
            )

    def _draw_progress_bar(self, x, y, bar_w, w, h):
        track = self.player.current_track
        if not track:
            return

        bar_h = max(2, _scale(2, w, h))

        if not self.player.play_start_time:
            Color(1, 1, 1, 0.07)
            RoundedRectangle(pos=(x, y), size=(bar_w, bar_h), radius=[2])
            pulse_w = _scale(64, w, h)
            pulse_x = int((bar_w - pulse_w) * ((math.sin(self.pulse_phase * 4) + 1) / 2))
            Color(*C.CYAN)
            RoundedRectangle(pos=(x + pulse_x, y), size=(pulse_w, bar_h), radius=[2])
            self._text_mono(
                "Loading stream...",
                x,
                y - _scale(22, w, h),
                font_size=sp(_scale(11, w, h)),
                color=_rgba(C.CYAN, 0.8),
            )
            return

        elapsed = _time.time() - self.player.play_start_time
        duration = track.get("duration")
        pct = self._progress_smooth if duration else 1.0

        Color(1, 1, 1, 0.07)
        RoundedRectangle(pos=(x, y), size=(bar_w, bar_h), radius=[2])

        fill_w = int(bar_w * pct)
        if fill_w > 0:
            Color(*C.CYAN)
            RoundedRectangle(pos=(x, y), size=(fill_w, bar_h), radius=[2])

        fs = sp(_scale(11, w, h))
        mins, secs = int(elapsed) // 60, int(elapsed) % 60
        self._text_mono(
            f"{mins}:{secs:02d}",
            x,
            y - _scale(20, w, h),
            font_size=fs,
            color=_rgba(C.CYAN, 0.7),
        )
        if duration:
            dur = float(duration)
            dm, ds = int(dur) // 60, int(dur) % 60
            self._text_mono(
                f"{dm}:{ds:02d}",
                x + bar_w,
                y - _scale(20, w, h),
                font_size=fs,
                color=C.TEXT_FAINT,
                halign="right"
            )

    def _draw_idle(self, x, bottom_h, lw, h, w):
        cx = x + lw // 2
        cy = (bottom_h + h) // 2 + _scale(20, w, h)
        bob = math.sin(self.idle_bob) * _scale(4, w, h)

        self._draw_nfc_tap_icon(cx, cy + _scale(40, w, h), w, h)

        title_fs = sp(_scale(32, w, h))
        self._text_display(
            "Ready to Play",
            cx,
            cy - _scale(30, w, h) + bob,
            font_size=title_fs,
            color=C.TEXT_SEC,
            bold=True,
            halign="center"
        )

        hint = "Place an NFC card on the reader"
        self._text_display(
            hint,
            cx,
            cy - _scale(64, w, h) + bob,
            font_size=sp(_scale(18, w, h)),
            color=C.TEXT_MUTED,
            halign="center"
        )

        if self.player.last_error:
            err = self.player.last_error[:60]
            self._text_display(
                err,
                cx,
                cy - _scale(110, w, h),
                font_size=sp(_scale(16, w, h)),
                color=C.RED,
                halign="center"
            )

    def _draw_nfc_tap_icon(self, cx, cy, w, h):
        card_w = _scale(56, w, h)
        card_h = _scale(72, w, h)
        card_x = cx - card_w // 2
        card_y = cy

        Color(*C.CYAN[:3], 0.04)
        RoundedRectangle(pos=(card_x, card_y), size=(card_w, card_h), radius=[8])
        Color(*C.CYAN[:3], 0.65)
        Line(rounded_rectangle=(card_x, card_y, card_w, card_h, 8), width=1.5)
        Color(*C.CYAN[:3], 0.45)
        RoundedRectangle(
            pos=(card_x + _scale(10, w, h), card_y + card_h - _scale(22, w, h)),
            size=(_scale(20, w, h), _scale(14, w, h)),
            radius=[3],
        )

        pad_y = card_y - _scale(18, w, h)
        pad_w = _scale(52, w, h)
        pad_h = _scale(6, w, h)
        Color(*C.CYAN[:3], 0.08)
        RoundedRectangle(pos=(cx - pad_w // 2, pad_y), size=(pad_w, pad_h), radius=[3])
        Color(*C.CYAN[:3], 0.5)
        Line(rounded_rectangle=(cx - pad_w // 2, pad_y, pad_w, pad_h, 3), width=1)

        for i in range(3):
            phase = ((self.ripple_phase + i * 0.6) % 1.8) / 1.8
            scale = 1.0 + phase * 1.5
            alpha = max(0, 0.8 * (1 - phase))
            rx = _scale(8, w, h) * scale
            ry = _scale(3, w, h) * scale
            Color(*C.CYAN[:3], alpha * 0.9)
            Line(ellipse=(cx, pad_y + pad_h // 2, rx, ry), width=1)

    # ── player: queue ────────────────────────────────────────────────────────

    def _draw_queue(self, x, bottom_h, qw, h, w):
        top = h - _scale(28, w, h)
        self._text_mono(
            "UP NEXT",
            x,
            top - _scale(18, w, h),
            font_size=sp(_scale(10, w, h)),
            color=_rgba(C.VIOLET, 0.75),
            bold=True,
        )

        upcoming = self.queue_mgr.get_upcoming()
        if not upcoming:
            self._text_display(
                "Queue is empty",
                x + _scale(4, w, h),
                top - _scale(56, w, h),
                font_size=sp(_scale(18, w, h)),
                color=C.TEXT_MUTED,
            )
            return

        gap = _scale(8, w, h)
        card_next_h = _scale(72, w, h)
        card_h = _scale(58, w, h)
        iy = top - _scale(40, w, h)
        max_visible = min(len(upcoming), 5)
        slide_dist = _scale(60, w, h)

        for i in range(max_visible):
            item = upcoming[i]
            is_next = i == 0
            ch = card_next_h if is_next else card_h
            uid = item.get("uid", f"idx_{i}")
            slide_t = self._queue_slide.get(uid, 1.0)

            if uid == self._new_queue_uid and slide_t < 1.0:
                slide_x, card_alpha = _card_slide_x(slide_t, slide_dist)
            else:
                slide_x = (1 - _ease_out_back(slide_t)) * slide_dist
                card_alpha = min(1.0, slide_t * 1.5)

            card_y = iy - ch
            accent = C.CYAN if i % 2 == 0 else C.VIOLET
            border_c = accent if is_next else C.VIOLET
            border_a = 0.38 if is_next else 0.2

            Color(*C.GLASS[:3], 0.45 * card_alpha)
            RoundedRectangle(pos=(x + slide_x, card_y), size=(qw, ch), radius=[10])
            Color(*border_c[:3], border_a * card_alpha)
            Line(rounded_rectangle=(x + slide_x, card_y, qw, ch, 10), width=1)

            if is_next:
                Color(*accent[:3], 0.5 * card_alpha)
                Rectangle(pos=(x + slide_x, card_y + ch - 1), size=(qw, 1))

            thumb = _scale(48, w, h) if is_next else _scale(38, w, h)
            thumb_x = x + slide_x + _scale(12, w, h)
            thumb_y = card_y + (ch - thumb) // 2

            img_bytes = item.get("image_bytes")
            if img_bytes:
                cache_key = f"{uid}_{thumb}"
                if cache_key not in self._mini_cache:
                    self._mini_cache[cache_key] = _load_image_bytes(img_bytes)
                tex = self._mini_cache.get(cache_key)
                if tex:
                    Color(1, 1, 1, card_alpha)
                    RoundedRectangle(
                        texture=tex,
                        pos=(thumb_x, thumb_y),
                        size=(thumb, thumb),
                        radius=[6],
                    )
            else:
                Color(*accent[:3], 0.22 * card_alpha)
                RoundedRectangle(pos=(thumb_x, thumb_y), size=(thumb, thumb), radius=[6])

            Color(*accent[:3], 0.35 * card_alpha)
            Line(rounded_rectangle=(thumb_x, thumb_y, thumb, thumb, 6), width=1)

            text_x = thumb_x + thumb + _scale(10, w, h)
            title = item.get("title", "Unknown")
            title_fs = sp(_scale(14 if is_next else 12, w, h))
            ty = card_y + ch // 2 + _scale(4, w, h)
            self._text_display(
                title,
                text_x,
                ty,
                font_size=title_fs,
                color=(*C.TEXT_SLATE[:3], card_alpha),
                bold=True,
                max_w=qw - thumb - _scale(28, w, h),
            )
            artist = item.get("artist", "")
            if artist:
                self._text_display(
                    artist,
                    text_x,
                    ty - _scale(18, w, h),
                    font_size=sp(_scale(11, w, h)),
                    color=(*C.TEXT_MUTED[:3], card_alpha),
                    max_w=qw - thumb - _scale(28, w, h),
                )

            iy -= ch + gap

        if len(upcoming) > max_visible:
            more = len(upcoming) - max_visible
            self._text_mono(
                f"+ {more} more...",
                x + _scale(4, w, h),
                iy - _scale(4, w, h),
                font_size=sp(_scale(11, w, h)),
                color=C.TEXT_MUTED,
            )

    # ── player: bottom bar ───────────────────────────────────────────────────

    def _draw_bottom_bar(self, w, bar_h, pad, h):
        Color(1, 1, 1, 0.06)
        Rectangle(pos=(0, bar_h), size=(w, 1))

        btn_h = _scale(40, w, h)
        btn_y = bar_h // 2
        bx = pad
        for label, action in (("Skip", "skip"), ("Register", "register"), ("Quit", "quit")):
            bw = _scale(120 if action == "register" else 100, w, h)
            color = C.CYAN if action == "register" else C.TEXT_SEC
            self._draw_glass_button(bx + bw // 2, btn_y, bw, btn_h, label, color, w, h)
            bx += bw + _scale(12, w, h)

        right_x = w - pad
        live_w = _scale(72, w, h)
        live_h = _scale(28, w, h)
        live_x = right_x - live_w
        live_y = btn_y - live_h // 2

        Color(*C.CYAN[:3], 0.04)
        RoundedRectangle(pos=(live_x, live_y), size=(live_w, live_h), radius=[20])
        Color(*C.CYAN[:3], 0.2)
        Line(rounded_rectangle=(live_x, live_y, live_w, live_h, 20), width=1)

        dot_a = 0.75 + 0.25 * math.sin(self.pulse_phase * 3)
        dot = _scale(5, w, h)
        Color(*C.CYAN[:3], dot_a)
        Ellipse(
            pos=(live_x + _scale(10, w, h), live_y + live_h // 2 - dot // 2),
            size=(dot, dot),
        )
        self._text_mono(
            "LIVE",
            live_x + _scale(22, w, h),
            live_y + _scale(7, w, h),
            font_size=sp(_scale(10, w, h)),
            color=_rgba(C.CYAN, 0.7),
            bold=True,
        )

        tap_text = "cards tapped tonight"
        count_str = str(self.tap_count)
        count_w, _ = self._measure_text(count_str, sp(_scale(12, w, h)), bold=True, mono=True)
        text_w, _ = self._measure_text(tap_text, sp(_scale(12, w, h)), mono=True)
        tw = count_w + _scale(4, w, h) + text_w
        tap_x = live_x - tw - _scale(24, w, h)
        self._text_mono(
            count_str,
            tap_x,
            btn_y - _scale(6, w, h),
            font_size=sp(_scale(12, w, h)),
            color=C.TEXT_SEC,
            bold=True,
        )
        self._text_mono(
            tap_text,
            tap_x + count_w + _scale(4, w, h),
            btn_y - _scale(6, w, h),
            font_size=sp(_scale(12, w, h)),
            color=C.TEXT_MUTED,
        )

        mid_x1 = bx + _scale(32, w, h)
        mid_x2 = tap_x - _scale(32, w, h)
        if mid_x2 > mid_x1:
            Color(1, 1, 1, 0.04)
            Rectangle(pos=(mid_x1, btn_y), size=(mid_x2 - mid_x1, 1))

    # ── overlays ─────────────────────────────────────────────────────────────

    def _draw_flash(self, w, h):
        elapsed = _time.time() - self.flash_start
        progress = min(1.0, elapsed / self.flash_duration)
        alpha = _flash_alpha(progress)

        layers = [
            (C.CYAN, 0.18, 1.2, 0.7, 1.1),
            (C.VIOLET_DIM, 0.14, 0.7, 0.5, 1.0),
        ]
        for i, (color, peak, rx_m, ry_m, y_off) in enumerate(layers):
            delay = i * 0.12
            if elapsed < delay:
                continue
            p = min(1.0, (elapsed - delay) / self.flash_duration)
            a = _flash_alpha(p) * peak * alpha
            if a > 0.005:
                cx, cy = w // 2, int(h * y_off)
                Color(*color[:3], a)
                Ellipse(
                    pos=(cx - w * rx_m // 2, cy - h * ry_m // 2),
                    size=(w * rx_m, h * ry_m),
                )

    def _draw_toast(self, w, h, pad):
        alpha = min(self.toast_alpha, 1.0)
        if alpha < 0.01:
            return

        toast_y = _scale(80, w, h) - int((1.0 - alpha) * _scale(20, w, h))
        text = self.toast_message
        text_w, _ = self._measure_text(text, sp(_scale(18, w, h)), bold=True)
        tw = text_w + _scale(48, w, h)
        tx = (w - tw) // 2
        th = _scale(48, w, h)

        Color(*C.GLASS[:3], alpha * 0.9)
        RoundedRectangle(pos=(tx, toast_y), size=(tw, th), radius=[16])
        Color(*C.CYAN[:3], alpha * 0.4)
        Line(rounded_rectangle=(tx, toast_y, tw, th, 16), width=1.5)
        self._text_display(
            text,
            w // 2,
            toast_y + th // 2,
            font_size=sp(_scale(18, w, h)),
            color=_rgba(C.TEXT, alpha),
            bold=True,
            halign="center",
            valign="center"
        )

    # ── registration ───────────────────────────────────────────────────────

    def _draw_registration(self, w, h, alpha=1.0):
        pad = _scale(32, w, h)
        self._text_display(
            "uTune",
            pad,
            h - _scale(50, w, h),
            font_size=sp(_scale(36, w, h)),
            color=(*C.CYAN[:3], alpha),
            bold=True,
        )
        self._text_mono(
            "CARD REGISTRATION",
            pad + _scale(130, w, h),
            h - _scale(44, w, h),
            font_size=sp(_scale(12, w, h)),
            color=(*C.VIOLET[:3], alpha * 0.8),
            bold=True,
        )
        Color(1, 1, 1, 0.06 * alpha)
        Rectangle(pos=(pad, h - _scale(72, w, h)), size=(w - pad * 2, 1))

        back_label = "← Player" if self.reg_state in (REG_STATE_HOME, REG_STATE_DONE) else "← Back"
        self._draw_glass_button(
            w - pad - _scale(60, w, h),
            h - _scale(50, w, h),
            _scale(120, w, h),
            _scale(44, w, h),
            back_label,
            C.TEXT_SEC,
            w,
            h,
        )

        cx, cy = w // 2, h // 2
        if self.reg_state == REG_STATE_HOME:
            self._draw_reg_home(cx, cy, w, h)
        elif self.reg_state == REG_STATE_WAITING_SCAN:
            self._draw_reg_waiting(cx, cy, w, h)
        elif self.reg_state == REG_STATE_PICK_FILE:
            self._draw_reg_files(cx, cy, w, h)
        elif self.reg_state in (REG_STATE_INPUT_TITLE, REG_STATE_INPUT_ARTIST):
            label = (
                "Enter Song Title:"
                if self.reg_state == REG_STATE_INPUT_TITLE
                else "Enter Artist Name:"
            )
            self._draw_reg_input(cx, cy, w, h, label)
        elif self.reg_state == REG_STATE_CONFIRM:
            self._draw_reg_confirm(cx, cy, w, h)
        elif self.reg_state == REG_STATE_DONE:
            self._draw_reg_done(cx, cy, w, h)
        elif self.reg_state == REG_STATE_LIST:
            self._draw_reg_list(cx, cy, w, h)

    def _draw_reg_home(self, cx, cy, w, h):
        self._text_display(
            "Card Manager",
            cx,
            cy + _scale(120, w, h),
            font_size=sp(_scale(36, w, h)),
            color=C.TEXT,
            bold=True,
            halign="center"
        )
        self._text_display(
            f"Cards registered: {self._card_count()}",
            cx,
            cy + _scale(70, w, h),
            font_size=sp(_scale(22, w, h)),
            color=C.TEXT_SEC,
            halign="center"
        )
        self._draw_glass_button(
            cx, cy - _scale(20, w, h), _scale(320, w, h), _scale(60, w, h),
            "Register New Card", C.CYAN, w, h,
        )
        self._draw_glass_button(
            cx, cy - _scale(100, w, h), _scale(320, w, h), _scale(60, w, h),
            "View All Cards", C.VIOLET, w, h,
        )

    def _draw_reg_waiting(self, cx, cy, w, h):
        radius = _scale(70, w, h) + int(_scale(12, w, h) * math.sin(self.pulse_phase * 2))
        ring_a = 0.4 + 0.2 * math.sin(self.pulse_phase * 3)
        Color(*C.CYAN[:3], ring_a)
        Line(circle=(cx, cy, radius), width=2)
        inner_a = 0.2 + 0.1 * math.sin(self.pulse_phase * 2 + 1)
        Color(*C.CYAN[:3], inner_a)
        sz = _scale(80, w, h)
        Ellipse(pos=(cx - sz // 2, cy - sz // 2), size=(sz, sz))
        self._draw_nfc_tap_icon(cx, cy, w, h)
        self._text_display(
            "Scan RFID Card",
            cx,
            cy - radius - _scale(60, w, h),
            font_size=sp(_scale(32, w, h)),
            color=C.TEXT,
            bold=True,
            halign="center"
        )
        self._text_display(
            "Place card on the reader...",
            cx,
            cy - radius - _scale(100, w, h),
            font_size=sp(_scale(20, w, h)),
            color=C.TEXT_MUTED,
            halign="center"
        )

    def _draw_reg_input(self, cx, cy, w, h, label):
        self._text_display(
            label,
            cx,
            cy + _scale(140, w, h),
            font_size=sp(_scale(32, w, h)),
            color=C.TEXT,
            bold=True,
            halign="center"
        )
        self._text_display(
            "Type below, then tap Submit or press ENTER",
            cx,
            cy + _scale(90, w, h),
            font_size=sp(_scale(18, w, h)),
            color=C.TEXT_MUTED,
            halign="center"
        )
        self._draw_glass_button(
            cx, cy - _scale(120, w, h), _scale(260, w, h), _scale(64, w, h),
            "Submit", C.CYAN, w, h,
        )

    def _draw_reg_files(self, cx, cy, w, h):
        self._text_display(
            "Select Audio File",
            cx,
            cy + _scale(240, w, h),
            font_size=sp(_scale(32, w, h)),
            color=C.TEXT,
            bold=True,
            halign="center"
        )
        folder = self.config.music_folder
        if len(folder) > 50:
            folder = "..." + folder[-47:]
        self._text_mono(
            f"Folder: {folder}",
            cx,
            cy + _scale(200, w, h),
            font_size=sp(_scale(12, w, h)),
            color=C.TEXT_MUTED,
            halign="center"
        )
        if not self.local_files:
            self._text_display(
                "No audio files found",
                cx,
                cy,
                font_size=sp(_scale(22, w, h)),
                color=C.RED,
                halign="center"
            )
            return

        list_w = _scale(560, w, h)
        list_x = cx - list_w // 2
        list_y = cy + _scale(120, w, h)
        item_h = _scale(54, w, h)
        visible = min(10, len(self.local_files) - self.file_scroll)

        for i in range(visible):
            idx = self.file_scroll + i
            iy = list_y - i * item_h
            is_sel = idx == self.file_selected
            Color(*C.GLASS[:3], 0.55 if is_sel else 0.3)
            RoundedRectangle(
                pos=(list_x, iy - item_h + 4), size=(list_w, item_h - 4), radius=[8],
            )
            bc = C.CYAN if is_sel else C.VIOLET
            Color(*bc[:3], 0.5 if is_sel else 0.2)
            Line(rounded_rectangle=(list_x, iy - item_h + 4, list_w, item_h - 4, 8), width=1.2)

            ext = os.path.splitext(self.local_files[idx])[1].upper()
            self._text_mono(
                ext, list_x + _scale(16, w, h), iy - item_h + _scale(18, w, h),
                font_size=sp(_scale(12, w, h)), color=C.CYAN if is_sel else C.TEXT_MUTED,
            )
            fname = self.local_files[idx]
            if len(fname) > 42:
                fname = fname[:39] + "..."
            self._text_display(
                fname, list_x + _scale(72, w, h), iy - item_h + _scale(16, w, h),
                font_size=sp(_scale(18, w, h)), color=C.TEXT if is_sel else C.TEXT_SEC,
            )

    def _draw_reg_confirm(self, cx, cy, w, h):
        self._text_display(
            "Confirm Registration",
            cx,
            cy + _scale(140, w, h),
            font_size=sp(_scale(32, w, h)),
            color=C.TEXT,
            bold=True,
            halign="center"
        )
        for i, line in enumerate(
            [f"UID: {self.scanned_uid}", f"Title: {self.reg_title}", f"Artist: {self.reg_artist}"]
        ):
            self._text_display(
                line, cx, cy + _scale(80 - i * 40, w, h),
                font_size=sp(_scale(22, w, h)), color=C.TEXT_SEC,
                halign="center"
            )
        self._draw_glass_button(
            cx - _scale(120, w, h), cy - _scale(120, w, h),
            _scale(200, w, h), _scale(54, w, h), "Confirm", C.CYAN, w, h,
        )
        self._draw_glass_button(
            cx + _scale(120, w, h), cy - _scale(120, w, h),
            _scale(200, w, h), _scale(54, w, h), "Cancel", C.TEXT_SEC, w, h,
        )

    def _draw_reg_done(self, cx, cy, w, h):
        sz = _scale(120, w, h)
        Color(*C.GREEN[:3], 0.18)
        Ellipse(pos=(cx - sz // 2, cy + _scale(40, w, h)), size=(sz, sz))
        Color(*C.GREEN[:3], 0.85)
        Line(circle=(cx, cy + _scale(100, w, h), sz // 2), width=3)
        self._text_display(
            "Success!", cx, cy,
            font_size=sp(_scale(36, w, h)), color=C.TEXT, bold=True,
            halign="center"
        )
        self._draw_glass_button(
            cx, cy - _scale(120, w, h), _scale(280, w, h), _scale(54, w, h),
            "Register Another", C.CYAN, w, h,
        )

    def _draw_reg_list(self, cx, cy, w, h):
        self._text_display(
            "Registered Cards",
            cx,
            cy + _scale(300, w, h),
            font_size=sp(_scale(32, w, h)),
            color=C.TEXT,
            bold=True,
            halign="center"
        )
        if not self.cards_list:
            self._text_display(
                "No cards registered yet",
                cx,
                cy,
                font_size=sp(_scale(22, w, h)),
                color=C.TEXT_MUTED,
                halign="center"
            )
            return

        list_w = _scale(720, w, h)
        list_x = cx - list_w // 2
        list_y = cy + _scale(220, w, h)
        item_h = _scale(64, w, h)
        visible = min(9, len(self.cards_list) - self.cards_scroll)

        for i in range(visible):
            idx = self.cards_scroll + i
            if idx >= len(self.cards_list):
                break
            card = self.cards_list[idx]
            iy = list_y - i * item_h
            Color(*C.GLASS[:3], 0.45)
            RoundedRectangle(
                pos=(list_x, iy - item_h + 6), size=(list_w, item_h - 6), radius=[8],
            )
            Color(1, 1, 1, 0.08)
            Line(rounded_rectangle=(list_x, iy - item_h + 6, list_w, item_h - 6, 8), width=1)

            uid_str = card["uid"][:8] + ".."
            self._text_mono(
                uid_str, list_x + _scale(16, w, h), iy - item_h + _scale(22, w, h),
                font_size=sp(_scale(12, w, h)), color=C.CYAN,
            )
            title_text = card.get("title", "Unknown")
            if len(title_text) > 34:
                title_text = title_text[:31] + "..."
            self._text_display(
                title_text, list_x + _scale(150, w, h), iy - item_h + _scale(20, w, h),
                font_size=sp(_scale(20, w, h)), color=C.TEXT,
            )
            is_url = card["url"].startswith("http")
            src = "YT" if is_url else "LOCAL"
            self._text_mono(
                src, list_x + _scale(560, w, h), iy - item_h + _scale(22, w, h),
                font_size=sp(_scale(12, w, h)), color=C.CYAN if is_url else C.VIOLET,
            )
            dx = list_x + list_w - _scale(72, w, h)
            dy = iy - item_h + _scale(12, w, h)
            Color(*C.RED[:3], 0.18)
            RoundedRectangle(
                pos=(dx, dy), size=(_scale(56, w, h), _scale(40, w, h)), radius=[8],
            )
            Color(*C.RED[:3], 0.55)
            Line(rounded_rectangle=(dx, dy, _scale(56, w, h), _scale(40, w, h), 8), width=1.2)
            self._text_display(
                "X", dx + _scale(28, w, h), dy + _scale(20, w, h),
                font_size=sp(_scale(20, w, h)), color=C.RED, bold=True,
                halign="center", valign="center"
            )

    def _draw_glass_button(self, cx, cy, bw, bh, text, color, w, h):
        bx = cx - bw // 2
        by = cy - bh // 2
        pressed = self._btn_press == (text, cx, cy)
        if pressed:
            bw = int(bw * 0.97)
            bh = int(bh * 0.97)
            bx = cx - bw // 2
            by = cy - bh // 2

        Color(*C.GLASS[:3], 0.55)
        RoundedRectangle(pos=(bx, by), size=(bw, bh), radius=[10])
        Color(*color[:3], 0.55)
        Line(rounded_rectangle=(bx, by, bw, bh, 10), width=1.2)
        Color(*color[:3], 0.15)
        Rectangle(pos=(bx + 4, by + bh - 3), size=(bw - 8, 2))

        self._text_display(
            text, cx, cy,
            font_size=sp(_scale(16, w, h)), color=color, bold=True,
            halign="center", valign="center"
        )

    # ── text helpers ─────────────────────────────────────────────────────────

    def _measure_text(self, text, font_size, bold=False, mono=False):
        tex = self._text_cache.get(text, font_size, bold, (1, 1, 1, 1), mono=mono)
        return tex.size if tex else (0, 0)

    def _text_display(self, text, x, y, font_size=sp(20), color=None, bold=False, max_w=None, halign="left", valign="bottom"):
        if color is None:
            color = C.TEXT
        display = str(text)
        if max_w:
            tw, _ = self._measure_text(display, font_size, bold=bold, mono=False)
            if tw > max_w and len(display) > 3:
                char_w = tw / len(display)
                max_chars = max(3, int(max_w / char_w))
                display = display[:max_chars - 3] + "..."
        self._blit_text(display, x, y, font_size, color, bold, False, halign, valign)

    def _text_mono(self, text, x, y, font_size=sp(12), color=None, bold=False, max_w=None, halign="left", valign="bottom"):
        if color is None:
            color = C.TEXT
        display = str(text)
        if max_w:
            tw, _ = self._measure_text(display, font_size, bold=bold, mono=True)
            if tw > max_w and len(display) > 3:
                char_w = tw / len(display)
                max_chars = max(3, int(max_w / char_w))
                display = display[:max_chars - 3] + "..."
        self._blit_text(display, x, y, font_size, color, bold, True, halign, valign)

    def _blit_text(self, text, x, y, font_size, color, bold, mono, halign="left", valign="bottom"):
        tex = self._text_cache.get(text, font_size, bold, color, mono=mono)
        if tex:
            tw, th = tex.size
            if halign == "center":
                x -= tw / 2.0
            elif halign == "right":
                x -= tw
            if valign == "center":
                y -= th / 2.0
            elif valign == "top":
                y -= th
            Color(1, 1, 1, 1)
            Rectangle(texture=tex, pos=(int(x), int(y)), size=(tw, th))

    # ── touch / input ────────────────────────────────────────────────────────

    def on_touch_down(self, touch):
        if self._text_input and self._text_input.collide_point(touch.x, touch.y):
            return super().on_touch_down(touch)

        mx, my = self.to_local(touch.x, touch.y)
        w, h = self.width, self.height

        if self.page == "player" and self._target_page == "player":
            if self._handle_player_touch(mx, my, w, h, down=True):
                return True
            return super().on_touch_down(touch)

        self.dirty = True
        return self._handle_reg_touch(mx, my, w, h) or super().on_touch_down(touch)

    def on_touch_up(self, touch):
        if self._btn_press:
            self._btn_press = None
            self.dirty = True
        return super().on_touch_up(touch)

    def _handle_player_touch(self, mx, my, w, h, down=False):
        self.dirty = True
        pad = _scale(28, w, h)
        bar_h = _scale(56, w, h)
        btn_y = bar_h // 2
        bx = pad
        for label, action in (("Skip", "skip"), ("Register", "register"), ("Quit", "quit")):
            bw = _scale(120 if action == "register" else 100, w, h)
            if self._hit_btn(bx + bw // 2, btn_y, bw, _scale(40, w, h), mx, my):
                if down:
                    self._btn_press = (label, bx + bw // 2, btn_y)
                if action == "skip":
                    self.player.skip()
                elif action == "register":
                    self._target_page = "register"
                    self.reg_state = REG_STATE_HOME
                    self._remove_text_input()
                elif action == "quit":
                    from kivy.app import App
                    App.get_running_app().stop()
                return True
            bx += bw + _scale(12, w, h)
        return False

    def _handle_reg_touch(self, mx, my, w, h):
        cx, cy = w // 2, h // 2
        pad = _scale(32, w, h)

        if self._hit_btn(w - pad - _scale(60, w, h), h - _scale(50, w, h),
                          _scale(120, w, h), _scale(44, w, h), mx, my):
            if self.reg_state in (REG_STATE_HOME, REG_STATE_DONE):
                self._target_page = "player"
                self._remove_text_input()
            else:
                self._reset_reg()
                self.reg_state = REG_STATE_HOME
            return True

        if self.reg_state == REG_STATE_HOME:
            if self._hit_btn(cx, cy - _scale(20, w, h), _scale(320, w, h), _scale(60, w, h), mx, my):
                self.reg_state = REG_STATE_WAITING_SCAN
                return True
            if self._hit_btn(cx, cy - _scale(100, w, h), _scale(320, w, h), _scale(60, w, h), mx, my):
                self._load_cards_list()
                self.reg_state = REG_STATE_LIST
                return True

        elif self.reg_state == REG_STATE_INPUT_TITLE:
            if self._hit_btn(cx, cy - _scale(120, w, h), _scale(260, w, h), _scale(64, w, h), mx, my):
                self._submit_text_input()
                return True

        elif self.reg_state == REG_STATE_INPUT_ARTIST:
            if self._hit_btn(cx, cy - _scale(120, w, h), _scale(260, w, h), _scale(64, w, h), mx, my):
                self._submit_text_input()
                return True

        elif self.reg_state == REG_STATE_PICK_FILE:
            list_w = _scale(560, w, h)
            list_x = cx - list_w // 2
            list_y = cy + _scale(120, w, h)
            item_h = _scale(54, w, h)
            visible = min(10, len(self.local_files) - self.file_scroll)
            for i in range(visible):
                idx = self.file_scroll + i
                iy = list_y - i * item_h
                if list_x < mx < list_x + list_w and iy - item_h < my < iy:
                    self.file_selected = idx
                    filename = self.local_files[idx]
                    self.reg_url = filename
                    title = self._get_title_from_file(filename)
                    preset = title if title else os.path.splitext(filename)[0]
                    self.reg_state = REG_STATE_INPUT_TITLE
                    self._show_text_input("Enter Song Title:", preset)
                    return True

        elif self.reg_state == REG_STATE_CONFIRM:
            if self._hit_btn(cx - _scale(120, w, h), cy - _scale(120, w, h),
                             _scale(200, w, h), _scale(54, w, h), mx, my):
                self._do_confirm_register()
                return True
            if self._hit_btn(cx + _scale(120, w, h), cy - _scale(120, w, h),
                             _scale(200, w, h), _scale(54, w, h), mx, my):
                self._reset_reg()
                self.reg_state = REG_STATE_HOME
                return True

        elif self.reg_state == REG_STATE_DONE:
            if self._hit_btn(cx, cy - _scale(120, w, h), _scale(280, w, h), _scale(54, w, h), mx, my):
                self._reset_reg()
                self.reg_state = REG_STATE_HOME
                return True

        elif self.reg_state == REG_STATE_LIST:
            list_w = _scale(720, w, h)
            list_x = cx - list_w // 2
            list_y = cy + _scale(220, w, h)
            item_h = _scale(64, w, h)
            visible = min(9, len(self.cards_list) - self.cards_scroll)
            for i in range(visible):
                idx = self.cards_scroll + i
                if idx >= len(self.cards_list):
                    break
                iy = list_y - i * item_h
                del_x = list_x + list_w - _scale(72, w, h)
                if del_x < mx < del_x + _scale(56, w, h) and iy - item_h < my < iy:
                    uid = self.cards_list[idx]["uid"]
                    from registry import Registry
                    Registry(self.config.db_path).delete_card(uid)
                    self.show_toast(f"Deleted card {uid[:6]}...")
                    self._load_cards_list()
                    return True

        return False

    def _hit_btn(self, cx, cy, bw, bh, mx, my):
        return cx - bw // 2 < mx < cx + bw // 2 and cy - bh // 2 < my < cy + bh // 2

    def _handle_reg_key(self, key, codepoint):
        if key == 27:
            if self.reg_state in (REG_STATE_HOME, REG_STATE_DONE):
                self._target_page = "player"
                self._remove_text_input()
            else:
                self._reset_reg()
                self.reg_state = REG_STATE_HOME
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
            if key == 273 and self.file_selected > 0:
                self.file_selected -= 1
            elif key == 274 and self.file_selected < len(self.local_files) - 1:
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
                    self.reg_state = REG_STATE_INPUT_ARTIST
                    self._show_text_input("Enter Artist Name:", preset="")
            return True

        if self.reg_state == REG_STATE_INPUT_ARTIST:
            if key in (13, 271):
                text = self._get_input_text()
                self.reg_artist = text.strip()
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

        if self.reg_state == REG_STATE_DONE and key in (13, 271):
            self._reset_reg()
            self.reg_state = REG_STATE_HOME
            return True

        return False

    # ── text input ───────────────────────────────────────────────────────────

    def _show_text_input(self, label, preset=""):
        self._remove_text_input()
        self._input_label = label
        w, h = self.width, self.height
        ti = TextInput(
            text=preset,
            multiline=False,
            size_hint=(None, None),
            size=(min(_scale(900, w, h), w - _scale(100, w, h)), _scale(72, w, h)),
            pos_hint={"center_x": 0.5, "center_y": 0.5},
            font_size=sp(_scale(28, w, h)),
            font_name=_resolve_font(FONT_DISPLAY),
            background_color=(*C.GLASS[:3], 0.35),
            background_normal="",
            background_active="",
            foreground_color=C.TEXT,
            cursor_color=C.CYAN,
            padding=[16, 14],
        )
        self._text_input = ti
        self.add_widget(ti)
        ti.focus = True
        Clock.schedule_once(lambda dt: ti.select_all(), 0.1)

    def _get_input_text(self):
        return self._text_input.text if self._text_input else ""

    def _remove_text_input(self):
        if self._text_input:
            self.remove_widget(self._text_input)
            self._text_input = None

    def _submit_text_input(self):
        text = self._get_input_text()
        if self.reg_state == REG_STATE_INPUT_TITLE:
            if not text.strip():
                return
            self.reg_title = text.strip()
            self.reg_state = REG_STATE_INPUT_ARTIST
            self._show_text_input("Enter Artist Name:", preset="")
        elif self.reg_state == REG_STATE_INPUT_ARTIST:
            self.reg_artist = text.strip()
            self._remove_text_input()
            self.reg_state = REG_STATE_CONFIRM
        self.dirty = True

    # ── registration logic ───────────────────────────────────────────────────

    def _do_confirm_register(self):
        from registry import Registry
        Registry(self.config.db_path).register_card(
            self.scanned_uid, self.reg_title, self.reg_url, self.reg_artist
        )
        self.show_toast(f"Registered: {self.reg_title}")
        self.reg_state = REG_STATE_DONE
        self.dirty = True

    def _reset_reg(self):
        self.scanned_uid = None
        self.existing_card = None
        self.selected_source = None
        self.reg_title = ""
        self.reg_artist = ""
        self.reg_url = ""
        self._remove_text_input()
        self.dirty = True

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
        try:
            with sqlite3.connect(self.config.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT uid, title, file_path, artist FROM cards ORDER BY date_added DESC"
                )
                self.cards_list = [
                    {"uid": r[0], "title": r[1], "url": r[2], "artist": r[3] or ""}
                    for r in cursor.fetchall()
                ]
        except Exception:
            self.cards_list = []
        self.cards_scroll = 0
        self.dirty = True

    def _card_count(self):
        try:
            with sqlite3.connect(self.config.db_path) as conn:
                return conn.cursor().execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        except Exception:
            return 0

    def _get_title_from_file(self, filename):
        file_path = os.path.join(self.config.music_folder, filename.replace("\\", "/"))
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
                if title:
                    return title
            except Exception:
                pass
        return None
