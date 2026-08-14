"""uTune Jukebox UI — Kivy canvas renderer.

Implements the "uTune / Marquee" redesign (design_handoff_player_ui) at
1920×1200 (Nexus 7 2013 landscape). Canvas-based for performance on low-power
Android hardware: the whole product is one widget whose canvas redraw branches
on state (``self.page`` / ``self.reg_state`` / the player's own state), which
covers the six screens in the handoff:

    A01 now playing · A02 idle · A03 loading · A04 card added
    A05 unknown card · A06 register a card

Everything is authored in design-space pixels (1920×1200, y measured from the
top) and mapped onto the widget by :class:`Frame`, so the numbers in this file
match the numbers in the design reference.
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
from kivy.graphics import Color, Rectangle, RoundedRectangle, Line, Triangle
from kivy.graphics.texture import Texture
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.properties import StringProperty, NumericProperty

from player import AUDIO_EXTENSIONS


# ── Design canvas (Nexus 7 2013 landscape) ───────────────────────────────────
DESIGN_W = 1920
DESIGN_H = 1200


def _c(r, g, b, a=255):
    return (r / 255, g / 255, b / 255, a / 255)


def _rgba(c, alpha=None):
    return (c[0], c[1], c[2], alpha if alpha is not None else c[3])


def _mix(c0, c1, t):
    """Linear blend between two colours (used for the brand gradient)."""
    return tuple(a + (b - a) * t for a, b in zip(c0, c1))


class Frame:
    """Maps the 1920×1200 design space onto the widget (uniform, centered).

    ``x``/``y`` take design coordinates with y measured downwards from the top
    edge and return Kivy coordinates (y upwards); ``u`` converts a length.
    """

    __slots__ = ("s", "ox", "oy")

    def __init__(self, w, h):
        self.s = min(w / float(DESIGN_W), h / float(DESIGN_H))
        self.ox = (w - DESIGN_W * self.s) / 2.0
        self.oy = (h - DESIGN_H * self.s) / 2.0

    def x(self, v):
        return self.ox + v * self.s

    def y(self, v):
        return self.oy + (DESIGN_H - v) * self.s

    def u(self, v):
        return v * self.s

    def rect(self, x, y, w, h):
        """Design-space top-left rect → Kivy (x, y, w, h)."""
        return (
            self.ox + x * self.s,
            self.oy + (DESIGN_H - y - h) * self.s,
            w * self.s,
            h * self.s,
        )


# ── Layout constants (design pixels, y from top) ─────────────────────────────
PAD_X = 64
PAD_TOP = 56
PAD_BOTTOM = 48
CONTENT_R = DESIGN_W - PAD_X                 # 1856
TOPBAR_H = 63
TOPBAR_CY = PAD_TOP + TOPBAR_H / 2.0         # 87.5
BODY_TOP = PAD_TOP + TOPBAR_H + 64           # 183
BODY_BOTTOM = DESIGN_H - PAD_BOTTOM          # 1152

ART = 520
HERO_GAP = 72
INFO_X = PAD_X + ART + HERO_GAP              # 656
INFO_W = CONTENT_R - INFO_X                  # 1200

NP_LABEL_Y = BODY_TOP                        # 183
NP_TITLE_Y = 231
NP_ARTIST_Y = 335
NP_META_Y = 395
NP_PROG_Y = 477
NP_TIMES_Y = 503
NP_TRANSPORT_Y = 585

LOAD_RULE_Y = 1096
LOAD_BAR_Y = 1042
LOAD_STATUS_Y = 1070

QUEUE_CARD_W = 272
QUEUE_CARD_GAP = 22
QUEUE_ART_H = 150
QUEUE_BLOCK_H = 229
QUEUE_TOP = BODY_BOTTOM - QUEUE_BLOCK_H      # 923
QUEUE_RULE_Y = QUEUE_TOP - 26                # 897
QUEUE_HEAD_Y = QUEUE_RULE_Y - 47             # 850
QUEUE_SLOTS = 5

EMPTY_PANEL_H = 198
EMPTY_TOP = BODY_BOTTOM - EMPTY_PANEL_H      # 954
EMPTY_RULE_Y = EMPTY_TOP - 26                # 928
EMPTY_HEAD_Y = EMPTY_RULE_Y - 47             # 881

REG_RAIL_W = 440
REG_RIGHT_X = PAD_X + REG_RAIL_W + 64        # 568
REG_RIGHT_W = CONTENT_R - REG_RIGHT_X        # 1288
REG_LIST_Y = 232
REG_ROW_H = 104
REG_FILE_ROWS = 5
REG_FIELD_Y = 792
REG_FIELD_H = 76
REG_ACTION_Y = BODY_BOTTOM - 75
REG_CARDS_ROWS = 8

# Border alphas (--border-subtle / default / strong / glow)
B_SUBTLE = 0.08
B_DEFAULT = 0.14
B_STRONG = 0.26


# ── Palette (Utech design system tokens) ─────────────────────────────────────
class C:
    VOID = _c(5, 3, 8)
    BASE = _c(10, 7, 20)
    SURFACE = _c(18, 12, 34)
    SURFACE_2 = _c(26, 18, 48)
    ELEVATED = _c(36, 26, 61)

    TEXT = _c(245, 243, 251)
    TEXT_SEC = _c(183, 174, 208)
    TEXT_MUTED = _c(122, 113, 148)
    TEXT_DIM = _c(76, 70, 102)

    INDIGO = _c(99, 102, 241)
    INDIGO_STRONG = _c(76, 70, 224)
    INDIGO_SOFT = _c(165, 166, 247)
    MAGENTA = _c(224, 72, 217)
    MAGENTA_STRONG = _c(192, 38, 211)
    MAGENTA_SOFT = _c(242, 154, 233)

    BLUE = _c(59, 130, 246)
    CYAN = _c(56, 189, 248)
    CYAN_SOFT = _c(159, 216, 247)

    SUCCESS = _c(45, 212, 168)
    SUCCESS_SOFT = _c(140, 232, 208)
    WARNING = _c(245, 166, 35)
    DANGER = _c(244, 82, 122)

    WHITE = (1.0, 1.0, 1.0, 1.0)


# Queue-card tints, in slot order (mirrors the gradients in the reference).
QUEUE_TINTS = [
    ((C.INDIGO, 0.42), (C.MAGENTA, 0.24), 0.18),
    ((C.INDIGO, 0.26), (C.BLUE, 0.16), 0.14),
    ((C.MAGENTA, 0.24), (C.INDIGO, 0.18), 0.14),
    ((C.INDIGO, 0.20), (C.MAGENTA, 0.14), 0.12),
    ((C.BLUE, 0.18), (C.INDIGO, 0.12), 0.12),
]


# ── Easing / keyframe helpers ────────────────────────────────────────────────

def _ease_out_back(t):
    """cubic-bezier(0.22, 1, 0.36, 1) overshoot."""
    if t >= 1.0:
        return 1.0
    t = max(0.0, min(1.0, t))
    c1 = 1.70158
    c3 = c1 + 1
    t -= 1
    return 1 + c3 * t * t * t + c1 * t * t


def _ease_in_out(t):
    """Smooth 0→1→0 for the ambient `drift` / `pulseDot` keyframes."""
    return (1.0 - math.cos(t * 2.0 * math.pi)) / 2.0


def _flash_alpha(progress):
    """screen-flash keyframes."""
    if progress < 0.15:
        return progress / 0.15
    if progress < 0.60:
        return 1.0 - (progress - 0.15) / 0.45 * 0.6
    return max(0.0, 0.4 * (1.0 - (progress - 0.60) / 0.40))


def _pulse(phase, period, low=0.35, high=1.0):
    """`pulseDot`: opacity high→low→high over `period` seconds."""
    t = (phase % period) / period
    return low + (high - low) * _ease_in_out(t)


# ── Texture helpers (gradients / glows — no CSS equivalents in Kivy) ─────────
_TEX_CACHE = {}


def _grad_tex(c0, c1, diagonal=True):
    """Two-stop gradient as a tiny texture; bilinear filtering does the rest.

    A 2×2 texture reproduces the 135° brand gradient well enough for fills on
    rounded rects and circles, which is how the mock paints every accent.
    """
    key = ("grad", tuple(c0), tuple(c1), diagonal)
    tex = _TEX_CACHE.get(key)
    if tex is not None:
        return tex

    mid = _mix(c0, c1, 0.5)
    if diagonal:
        # rows bottom-up: (bottom-left, bottom-right), (top-left, top-right)
        rows = [(mid, c1), (c0, mid)]
        size = (2, 2)
    else:
        rows = [(c0, c1)]
        size = (2, 1)

    buf = bytearray()
    for row in rows:
        for col in row:
            buf += bytes(int(max(0.0, min(1.0, v)) * 255) for v in col)

    tex = Texture.create(size=size, colorfmt="rgba")
    tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
    tex.wrap = "clamp_to_edge"
    _TEX_CACHE[key] = tex
    return tex


def _band_tex(peak=1.0):
    """transparent → white → transparent sweep, for the loading shimmer."""
    key = ("band", round(peak, 3))
    tex = _TEX_CACHE.get(key)
    if tex is not None:
        return tex
    stops = [0.0, peak, peak, 0.0]
    buf = bytearray()
    for a in stops:
        buf += bytes((255, 255, 255, int(max(0.0, min(1.0, a)) * 255)))
    tex = Texture.create(size=(4, 1), colorfmt="rgba")
    tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
    tex.wrap = "clamp_to_edge"
    _TEX_CACHE[key] = tex
    return tex


def _radial_tex():
    """White radial falloff — tinted at draw time to fake the blurred blobs."""
    tex = _TEX_CACHE.get("radial")
    if tex is not None:
        return tex
    n = 96
    buf = bytearray()
    for j in range(n):
        for i in range(n):
            dx = (i + 0.5) / n * 2.0 - 1.0
            dy = (j + 0.5) / n * 2.0 - 1.0
            a = max(0.0, 1.0 - math.sqrt(dx * dx + dy * dy))
            a = a * a * (3.0 - 2.0 * a)
            buf += bytes((255, 255, 255, int(a * 255)))
    tex = Texture.create(size=(n, n), colorfmt="rgba")
    tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
    tex.wrap = "clamp_to_edge"
    _TEX_CACHE["radial"] = tex
    return tex


def _vignette_tex():
    """radial-gradient(ellipse 90% 80% at 50% 45%, transparent 30%, void 100%)."""
    tex = _TEX_CACHE.get("vignette")
    if tex is not None:
        return tex
    n = 96
    r, g, b = [int(v * 255) for v in C.VOID[:3]]
    buf = bytearray()
    for j in range(n):
        for i in range(n):
            px = (i + 0.5) / n
            py = 1.0 - (j + 0.5) / n
            dx = (px - 0.5) / 0.45
            dy = (py - 0.45) / 0.40
            d = math.sqrt(dx * dx + dy * dy)
            a = max(0.0, min(1.0, (d - 0.30) / 0.70)) * 0.8
            buf += bytes((r, g, b, int(a * 255)))
    tex = Texture.create(size=(n, n), colorfmt="rgba")
    tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
    tex.wrap = "clamp_to_edge"
    _TEX_CACHE["vignette"] = tex
    return tex


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

# Audiowide → NotoSans-Bold, Sora → NotoSans-Regular, Space Mono → JetBrainsMono
# (SpaceGrotesk.ttf ships as a Light weight, too light for the display role).
FONT_BODY = "NotoSans-Regular.ttf"
FONT_DISPLAY = "NotoSans-Bold.ttf"
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


def _fmt_time(seconds):
    try:
        seconds = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        return "--:--"
    return f"{seconds // 60}:{seconds % 60:02d}"


def _fmt_uid(uid):
    """`04A72E91` → `04 A7 2E 91`, the readout style used across the mock."""
    s = str(uid or "").strip().upper()
    if len(s) > 4 and len(s) % 2 == 0 and all(ch in "0123456789ABCDEF" for ch in s):
        return " ".join(s[i:i + 2] for i in range(0, len(s), 2))
    return s


class TextCache:
    """LRU cache for CoreLabel textures.

    Glyphs are rendered white and tinted by the ``Color`` in front of them, so
    one texture serves every colour it is drawn in.
    """

    def __init__(self, max_size=768):
        self.cache = collections.OrderedDict()
        self.max_size = max_size
        self._body = _resolve_font(FONT_BODY)
        self._display = _resolve_font(FONT_DISPLAY, fallback=self._body)
        self._mono = _resolve_font(FONT_MONO, fallback=self._body)

    def font(self, family):
        if family == "mono":
            return self._mono
        if family == "display":
            return self._display
        return self._body

    def get(self, text, font_size, family="body", bold=False):
        from kivy.core.text import Label as CoreLabel

        font_name = self.font(family)
        if family == "display" or (family == "body" and bold):
            # NotoSans-Bold is already the bold face; only ask Kivy to embolden
            # when we had to fall back to a font that has no bold cut.
            font_name = self._display
            bold = font_name == self._body
        else:
            bold = False

        key = (text, font_size, family, bold, font_name)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]

        cl = CoreLabel(
            text=str(text),
            font_size=font_size,
            bold=bold,
            color=(1, 1, 1, 1),
            font_name=font_name,
        )
        cl.refresh()
        tex = cl.texture
        self.cache[key] = tex
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)
        return tex


class UI(FloatLayout):
    """Root jukebox widget — player screens + card registration."""

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
        self.drift_phase = 0.0
        self.ripple_phase = 0.0
        self.shimmer_phase = 0.0
        self.spin_phase = 0.0
        self.tap_count = 0
        self._page_blend = 0.0

        self.toast_message = ""
        self.toast_end = 0
        self.flash_active = False
        self.flash_start = 0
        self.flash_duration = 1.4
        self.flash_tint = (C.INDIGO, C.MAGENTA)

        # A04 "added to queue" confirmation + A05 "unknown card" modal
        self.added_item = None
        self.added_until = 0
        self.added_alpha = 0.0
        self.unknown_uid = None
        self.unknown_until = 0
        self.unknown_alpha = 0.0

        self.status_message = ""
        self.status_end = 0

        self.volume = 68

        self._art_cache_key = None
        self._art_texture = None
        self._mini_cache = {}
        self._file_meta = {}
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
        self._drag = None
        self._hits = collections.OrderedDict()

        self.f = Frame(DESIGN_W, DESIGN_H)
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
        if self._text_input:
            self._place_text_input()

    def _tick(self, dt):
        if not self.running:
            return

        animate = False
        self.pulse_phase += dt
        self.drift_phase += dt
        self.ripple_phase += dt
        self.shimmer_phase += dt
        self.spin_phase += dt
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

        old_added = self.added_alpha
        if now < self.added_until:
            self.added_alpha = min(1.0, self.added_alpha + dt * 5)
        else:
            self.added_alpha = max(0.0, self.added_alpha - dt * 3)
            if self.added_alpha <= 0.0:
                self.added_item = None
        if abs(self.added_alpha - old_added) > 0.01:
            animate = True

        old_unknown = self.unknown_alpha
        if self.unknown_uid and now < self.unknown_until:
            self.unknown_alpha = min(1.0, self.unknown_alpha + dt * 5)
            animate = True
        else:
            self.unknown_alpha = max(0.0, self.unknown_alpha - dt * 3)
            if self.unknown_alpha <= 0.0:
                self.unknown_uid = None
        if abs(self.unknown_alpha - old_unknown) > 0.01:
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
                elapsed = now - self.player.play_start_time
                target = min(1.0, elapsed / float(duration))
                self._progress_smooth += (target - self._progress_smooth) * min(1.0, dt * 4)
            animate = True

        # Ambient motion (drifting backdrop, pulsing dots) never stops.
        animate = True

        if self.dirty or animate:
            self._redraw()
            self.dirty = False

    def _update_queue_anims(self, dt):
        upcoming = self.queue_mgr.get_upcoming()
        uids = [item.get("uid", f"idx_{i}") for i, item in enumerate(upcoming[:8])]
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
        """Feedback entry point used by main.py and the registration flow.

        Every call comes from a card tap, so this is also where the "cards
        tonight" counter ticks. main.py is out of scope for this redesign, so
        the two scan results it reports are routed here to their richer screens
        (A04 / A05); anything else falls back to the small pill toast.
        """
        msg = str(message)
        self.tap_count += 1

        if msg.startswith("Unknown Card:"):
            self.show_unknown_card(msg.split(":", 1)[1].strip())
            return
        if msg.startswith("Added:"):
            self.show_added_to_queue(msg.split(":", 1)[1].strip())
            return

        self._start_flash((C.INDIGO, C.MAGENTA))
        self._toast(msg, duration)

    def _toast(self, message, duration=2.5):
        """Small pill toast — in-app feedback that is not a card tap."""
        self.toast_message = str(message)
        self.toast_end = _time.time() + duration
        self.toast_alpha = max(self.toast_alpha, 0.01)
        self.dirty = True

    def show_added_to_queue(self, title, duration=3.2):
        """A04 — confirmation panel for a card that joined the queue."""
        upcoming = self.queue_mgr.get_upcoming()
        item, position = None, len(upcoming)
        for i, it in enumerate(upcoming):
            if it.get("title") == title:
                item, position = it, i + 1

        self.added_item = {
            "title": title,
            "artist": (item or {}).get("artist", ""),
            "position": max(1, position),
            "wait": self._estimate_wait(upcoming, position - 1),
        }
        self.added_until = _time.time() + duration
        self._start_flash((C.INDIGO, C.MAGENTA))

        if item is not None:
            self._new_queue_uid = item.get("uid")
            self._new_queue_until = _time.time() + duration
            if self._new_queue_uid:
                self._queue_slide[self._new_queue_uid] = 0.0
        self.dirty = True

    def show_unknown_card(self, uid, duration=8.0):
        """A05 — blocking modal for a card with no registered track.

        Auto-dismisses after `duration` seconds, and offers a shortcut into the
        staff registration flow with this UID already scanned.
        """
        self.unknown_uid = str(uid)
        self.unknown_until = _time.time() + duration
        self._start_flash((C.DANGER, C.MAGENTA_STRONG))
        self.dirty = True

    def _start_flash(self, tint):
        self.flash_tint = tint
        self.flash_active = True
        self.flash_start = _time.time()

    def _estimate_wait(self, upcoming, index):
        """Seconds until the item at `index` starts, or None if unknowable."""
        track = self.player.current_track
        total = 0.0
        known = False

        if track:
            duration = track.get("duration")
            if duration and self.player.play_start_time:
                elapsed = _time.time() - self.player.play_start_time
                total += max(0.0, float(duration) - elapsed)
                known = True
            else:
                total += 210.0

        for item in upcoming[:max(0, index)]:
            duration = item.get("duration")
            if duration:
                total += float(duration)
                known = True
            else:
                total += 210.0

        return total if known else None

    def _on_player_status(self, msg):
        self.status_message = msg
        self.status_end = _time.time() + 5
        self.dirty = True

    def handle_scan(self, uid):
        if self.page == "player" and self._target_page == "player":
            if self.on_scan:
                self.on_scan(uid)
        elif self.page == "register" and self.reg_state == REG_STATE_WAITING_SCAN:
            self._begin_register_with_uid(uid)
        self.dirty = True

    def _begin_register_with_uid(self, uid):
        from registry import Registry

        self.scanned_uid = str(uid)
        self.existing_card = Registry(self.config.db_path).get_card(self.scanned_uid)
        self.selected_source = "local"
        self._scan_local_files()
        self.reg_state = REG_STATE_PICK_FILE
        self.dirty = True

    def handle_key_down(self, window, key, scancode, codepoint, modifiers):
        self.dirty = True
        if self.page == "player" and self._target_page == "player":
            if self.unknown_uid:
                if key == 27:
                    self._dismiss_unknown()
                    return True
                if key in (13, 271):
                    self._register_unknown_card()
                    return True
            if key == 27:
                return False
            if key == 114 or codepoint == "r":
                self._open_register()
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
        if self.reg_state in (REG_STATE_PICK_FILE, REG_STATE_INPUT_TITLE,
                              REG_STATE_INPUT_ARTIST, REG_STATE_CONFIRM):
            self.file_scroll = max(0, self.file_scroll - direction * 2)
            self.file_scroll = min(
                self.file_scroll, max(0, len(self.local_files) - REG_FILE_ROWS)
            )
            self.dirty = True
        elif self.reg_state == REG_STATE_LIST:
            self.cards_scroll = max(0, self.cards_scroll - direction * 2)
            self.cards_scroll = min(
                self.cards_scroll, max(0, len(self.cards_list) - REG_CARDS_ROWS)
            )
            self.dirty = True

    # ── background (static parts cached in canvas.before) ────────────────────

    def _build_background(self, w, h):
        if self._bg_size == (w, h):
            return
        f = self.f

        self.canvas.before.clear()
        with self.canvas.before:
            Color(*C.VOID)
            Rectangle(pos=(0, 0), size=(w, h))

            Color(1, 1, 1, 0.032)
            for gx in range(0, DESIGN_W + 1, 80):
                Line(points=[f.x(gx), f.y(DESIGN_H), f.x(gx), f.y(0)], width=1)
            for gy in range(0, DESIGN_H + 1, 80):
                Line(points=[f.x(0), f.y(gy), f.x(DESIGN_W), f.y(gy)], width=1)

        self._bg_size = (w, h)

    def _blob(self, cx, cy, bw, bh, color, alpha, period, dx, dy, s0, s1, phase=0.0):
        """One drifting radial glow (the `drift` / `driftAlt` keyframes)."""
        p = _ease_in_out(((self.drift_phase + phase) % period) / period)
        scale = s0 + (s1 - s0) * p
        sw, sh = bw * scale, bh * scale
        x = cx + bw * dx * p - sw / 2.0
        y = cy + bh * dy * p - sh / 2.0
        Color(*color[:3], alpha)
        Rectangle(texture=_radial_tex(), **self._rect_kw(x, y, sw, sh))

    def _rect_kw(self, x, y, w, h):
        px, py, pw, ph = self.f.rect(x, y, w, h)
        return {"pos": (px, py), "size": (pw, ph)}

    def _draw_ambient(self, variant="player"):
        if variant == "register":
            self._blob(0.78 * DESIGN_W, 0.24 * DESIGN_H, 1064, 728,
                       C.MAGENTA, 0.20, 15.0, -0.05, 0.04, 1.05, 1.0)
            self._blob(0.14 * DESIGN_W, 0.76 * DESIGN_H, 1064, 728,
                       C.INDIGO, 0.22, 13.0, 0.04, -0.03, 1.0, 1.08)
        else:
            self._blob(0.20 * DESIGN_W, 0.28 * DESIGN_H, 1224, 843,
                       C.INDIGO, 0.30, 14.0, 0.04, -0.03, 1.0, 1.08)
            self._blob(0.84 * DESIGN_W, 0.82 * DESIGN_H, 1064, 728,
                       C.MAGENTA, 0.22, 12.0, -0.05, 0.04, 1.05, 1.0)

    def _draw_vignette(self):
        Color(1, 1, 1, 1)
        Rectangle(texture=_vignette_tex(),
                  **self._rect_kw(0, 0, DESIGN_W, DESIGN_H))

    # ── canvas primitives (all arguments in design space) ────────────────────

    def _radii(self, radius, w, h):
        """Scale a scalar radius, or a (tl, tr, br, bl) tuple, to device px."""
        limit = min(w, h) / 2.0
        if isinstance(radius, (tuple, list)):
            return [self.f.u(min(r, limit)) for r in radius]
        return [self.f.u(min(radius, limit))]

    def _fill(self, x, y, w, h, color=None, radius=0, alpha=None, texture=None):
        f = self.f
        if color is None:
            color = C.WHITE
        Color(*color[:3], alpha if alpha is not None else color[3])
        px, py, pw, ph = f.rect(x, y, w, h)
        if radius:
            RoundedRectangle(texture=texture, pos=(px, py), size=(pw, ph),
                             radius=self._radii(radius, w, h))
        else:
            Rectangle(texture=texture, pos=(px, py), size=(pw, ph))

    def _stroke(self, x, y, w, h, color, radius=0, alpha=None, width=1, dash=0):
        f = self.f
        Color(*color[:3], alpha if alpha is not None else color[3])
        px, py, pw, ph = f.rect(x, y, w, h)
        kwargs = {"width": max(1.0, f.u(width))}
        if dash:
            kwargs["dash_length"] = int(max(2, f.u(dash)))
            kwargs["dash_offset"] = int(max(2, f.u(dash * 0.8)))
        if radius:
            radii = self._radii(radius, w, h)
            Line(rounded_rectangle=(px, py, pw, ph, *radii), **kwargs)
        else:
            Line(rectangle=(px, py, pw, ph), **kwargs)

    def _circle(self, cx, cy, d, color=None, alpha=None, texture=None):
        self._fill(cx - d / 2.0, cy - d / 2.0, d, d, color, alpha=alpha, texture=texture,
                   radius=d / 2.0)

    def _circle_stroke(self, cx, cy, d, color, alpha=None, width=1,
                       angle_start=0, angle_end=360):
        f = self.f
        Color(*color[:3], alpha if alpha is not None else color[3])
        Line(circle=(f.x(cx), f.y(cy), f.u(d / 2.0), angle_start, angle_end),
             width=max(1.0, f.u(width)))

    def _rule(self, x, y, w, color=None, alpha=B_SUBTLE, thickness=1):
        self._fill(x, y, w, thickness, color or C.WHITE, alpha=alpha)

    def _vrule(self, x, y, h, color=None, alpha=B_DEFAULT, thickness=1):
        self._fill(x, y, thickness, h, color or C.WHITE, alpha=alpha)

    def _glow(self, x, y, w, h, radius, color, alpha, spread, steps=6):
        """Approximates a CSS box-shadow glow.

        Stacked translucent fills, largest and faintest first, so the halo
        hugs the shape and falls away quickly — concentric *strokes* read as
        neon tubing rather than a shadow, and a linear ramp leaves a visible
        outer edge, hence the quadratic falloff.
        """
        for i in range(steps, 0, -1):
            grow = spread * i / float(steps)
            t = 1.0 - (i - 1) / float(steps)
            a = alpha * 0.30 * t * t
            if a <= 0.004:
                continue
            self._fill(x - grow, y - grow, w + grow * 2, h + grow * 2,
                       color, radius=radius + grow, alpha=a)

    def _glow_circle(self, cx, cy, d, color, alpha, spread, steps=6):
        self._glow(cx - d / 2.0, cy - d / 2.0, d, d, d / 2.0, color, alpha, spread, steps)

    def _grad_fill(self, x, y, w, h, c0, c1, radius=0, alpha=1.0, diagonal=True):
        self._fill(x, y, w, h, C.WHITE, radius=radius, alpha=alpha,
                   texture=_grad_tex(c0, c1, diagonal))

    def _shimmer(self, x, y, w, h, radius, period=1.8, band=0.4, peak=0.16,
                 color=None):
        """Diagonal light sweep used by the loading skeletons."""
        bw = w * band
        p = (self.shimmer_phase % period) / period
        bx = x - bw + (w + bw * 2) * p
        left = max(x, bx)
        right = min(x + w, bx + bw)
        if right <= left:
            return
        Color(*(color or C.INDIGO_SOFT)[:3], peak)
        px, py, pw, ph = self.f.rect(left, y, right - left, h)
        if radius:
            RoundedRectangle(texture=_band_tex(), pos=(px, py), size=(pw, ph),
                             radius=[self.f.u(min(radius, h / 2.0))])
        else:
            Rectangle(texture=_band_tex(), pos=(px, py), size=(pw, ph))

    # ── text ─────────────────────────────────────────────────────────────────

    def _glyph(self, ch, fs, family, bold):
        return self._text_cache.get(ch, fs, family, bold)

    def _measure(self, text, size, family="body", bold=False, tracking=0.0):
        """Width of `text` in design pixels."""
        f = self.f
        fs = max(6, int(round(f.u(size))))
        text = str(text)
        if tracking and tracking * fs >= 1.0:
            total = 0.0
            for ch in text:
                if ch == " ":
                    total += fs * 0.34
                    continue
                tex = self._glyph(ch, fs, family, bold)
                total += tex.size[0] if tex else 0
            total += tracking * fs * max(0, len(text) - 1)
        else:
            tex = self._text_cache.get(text, fs, family, bold)
            total = tex.size[0] if tex else 0
        return total / f.s if f.s else 0

    def _text(self, text, x, y, size, color=None, family="body", bold=False,
              tracking=0.0, halign="left", valign="top", max_w=None, alpha=None):
        """Draw `text` with its box anchored at design-space (x, y)."""
        f = self.f
        text = str(text)
        if color is None:
            color = C.TEXT
        fs = max(6, int(round(f.u(size))))

        if max_w:
            text = self._ellipsize(text, size, max_w, family, bold, tracking)

        tracked = bool(tracking) and tracking * fs >= 1.0
        width = self._measure(text, size, family, bold, tracking)

        if halign == "center":
            x -= width / 2.0
        elif halign == "right":
            x -= width

        col = (color[0], color[1], color[2],
               alpha if alpha is not None else color[3])

        if not tracked:
            tex = self._text_cache.get(text, fs, family, bold)
            if not tex:
                return width
            tw, th = tex.size
            py = self._text_y(y, th, valign)
            Color(*col)
            Rectangle(texture=tex, pos=(int(f.x(x)), int(py)), size=(tw, th))
            return width

        pen = f.x(x)
        step = tracking * fs
        for ch in text:
            if ch == " ":
                pen += fs * 0.34 + step
                continue
            tex = self._glyph(ch, fs, family, bold)
            if not tex:
                pen += step
                continue
            tw, th = tex.size
            py = self._text_y(y, th, valign)
            Color(*col)
            Rectangle(texture=tex, pos=(int(pen), int(py)), size=(tw, th))
            pen += tw + step
        return width

    def _text_y(self, y, th, valign):
        base = self.f.y(y)
        if valign == "middle":
            return base - th / 2.0
        if valign == "bottom":
            return base
        return base - th

    def _ellipsize(self, text, size, max_w, family, bold, tracking):
        width = self._measure(text, size, family, bold, tracking)
        if width <= max_w or len(text) <= 3:
            return text
        per_char = width / max(1, len(text))
        keep = max(1, int(max_w / per_char) - 1)
        out = text[:keep].rstrip() + "…"
        while keep > 1 and self._measure(out, size, family, bold, tracking) > max_w:
            keep -= 1
            out = text[:keep].rstrip() + "…"
        return out

    def _wrap(self, text, size, max_w, family="body", bold=False, tracking=0.0,
              max_lines=3):
        words = str(text).split()
        lines, current = [], ""
        for word in words:
            probe = f"{current} {word}".strip()
            if current and self._measure(probe, size, family, bold, tracking) > max_w:
                lines.append(current)
                current = word
            else:
                current = probe
        if current:
            lines.append(current)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = self._ellipsize(lines[-1] + " …", size, max_w, family,
                                        bold, tracking)
        return lines

    # ── hit testing ──────────────────────────────────────────────────────────

    def _hit(self, name, x, y, w, h):
        """Record a touch target (design space) while drawing it."""
        self._hits[name] = self.f.rect(x, y, w, h)

    def _hit_test(self, mx, my):
        for name, (px, py, pw, ph) in reversed(list(self._hits.items())):
            if px <= mx <= px + pw and py <= my <= py + ph:
                return name
        return None

    def _pressed(self, name):
        return self._btn_press == name

    # ── main redraw ──────────────────────────────────────────────────────────

    def _redraw(self):
        w, h = self.width, self.height
        if w < 10 or h < 10:
            return

        self.f = Frame(w, h)
        self._build_background(w, h)
        self.canvas.clear()
        self.canvas.after.clear()
        self._hits.clear()

        show_player = self.page == "player" or self._target_page == "player"
        show_reg = self.page == "register" or self._target_page == "register"

        if self._target_page != self.page:
            reg_alpha = (self._page_blend if self._target_page == "register"
                         else 1.0 - self._page_blend)
        else:
            reg_alpha = 1.0 if self.page == "register" else 0.0

        with self.canvas:
            if show_player and reg_alpha < 1.0:
                self._draw_ambient("player")
                self._draw_vignette()
                self._draw_player()

            if show_reg and reg_alpha > 0.01:
                self._fill(0, 0, DESIGN_W, DESIGN_H, C.VOID, alpha=reg_alpha)
                self._draw_ambient("register")
                self._draw_vignette()
                if reg_alpha > 0.55:
                    self._draw_registration()

        with self.canvas.after:
            if self.flash_active and self.page == "player":
                self._draw_flash()
            if self.toast_alpha > 0.01:
                self._draw_toast()

    # ── player screens ───────────────────────────────────────────────────────

    def _draw_player(self):
        track = self.player.current_track
        loading = bool(track) and not self.player.play_start_time
        upcoming = self.queue_mgr.get_upcoming()

        if self.added_alpha > 0.01:
            status = ("CARD READ", C.SUCCESS, C.SUCCESS_SOFT, 0.0)
        elif loading:
            status = ("LOADING", C.INDIGO_SOFT, C.INDIGO_SOFT, 1.0)
        elif track:
            status = ("LIVE", C.CYAN, C.CYAN_SOFT, 1.6)
        else:
            status = ("READY", C.CYAN, C.CYAN_SOFT, 1.6)

        self._draw_topbar(status)

        if track and loading:
            self._draw_hero_loading(track)
        elif track:
            self._draw_hero_playing(track)
        else:
            self._draw_hero_idle(upcoming)

        # A04 dims the hero behind the confirmation panel.
        if self.added_alpha > 0.01:
            head = QUEUE_HEAD_Y if upcoming else EMPTY_HEAD_Y
            self._fill(0, BODY_TOP - 40, DESIGN_W, head - BODY_TOP + 10,
                       C.VOID, alpha=0.45 * self.added_alpha)

        if track and loading:
            self._draw_loading_footer(upcoming)
        elif upcoming:
            self._draw_queue_row(upcoming)
        else:
            self._draw_empty_queue()

        if self.added_alpha > 0.01 and self.added_item:
            self._draw_added_panel()

        if self.unknown_alpha > 0.01 and self.unknown_uid:
            self._draw_unknown_modal()

    def _draw_topbar(self, status):
        cy = TOPBAR_CY
        self._draw_wordmark(PAD_X, cy)
        x = PAD_X + self._measure("UTUNE", 40, "display", tracking=0.16) + 26
        self._vrule(x, cy - 17, 34)
        self._text("RFID JUKEBOX", x + 27, cy, 19, C.TEXT_MUTED, "mono",
                   tracking=0.18, valign="middle")

        # right cluster, laid out right → left
        right = CONTENT_R
        self._draw_icon_button("quit", right - 54, cy - 27, 54, self._icon_power)

        bw, bh = 300, 63
        bx = right - 54 - 24 - bw
        self._draw_button("register", bx, cy - bh / 2.0, bw, bh, "Register card",
                          kind="secondary")

        x = bx - 24
        self._vrule(x, cy - 17, 34)
        x -= 24

        label = " CARDS TONIGHT"
        lw = self._measure(label, 18, "mono", tracking=0.10)
        self._text(label, x, cy, 18, C.TEXT_MUTED, "mono", tracking=0.10,
                   halign="right", valign="middle")
        count = str(self.tap_count)
        cw = self._measure(count, 18, "mono", tracking=0.10)
        self._text(count, x - lw, cy, 18, C.TEXT_SEC, "mono", tracking=0.10,
                   halign="right", valign="middle")

        self._draw_status_pill(x - lw - cw - 24, cy, *status)

    def _draw_wordmark(self, x, cy, size=40, tracking=0.16):
        """`UTUNE` in the brand gradient — approximated per glyph."""
        f = self.f
        fs = max(6, int(round(f.u(size))))
        text = "UTUNE"
        pen = f.x(x)
        step = tracking * fs
        for i, ch in enumerate(text):
            tex = self._glyph(ch, fs, "display", False)
            if not tex:
                continue
            tw, th = tex.size
            col = _mix(C.INDIGO, C.MAGENTA, i / float(max(1, len(text) - 1)))
            Color(*col)
            Rectangle(texture=tex, pos=(int(pen), int(f.y(cy) - th / 2.0)),
                      size=(tw, th))
            pen += tw + step

    def _draw_status_pill(self, right, cy, label, color, label_color, period):
        pad_x, dot, gap = 22, 11, 12
        tw = self._measure(label, 17, "mono", tracking=0.20)
        pw = pad_x * 2 + dot + gap + tw
        ph = 46
        x = right - pw
        y = cy - ph / 2.0

        self._fill(x, y, pw, ph, color, radius=ph / 2.0, alpha=0.07)
        self._stroke(x, y, pw, ph, color, radius=ph / 2.0, alpha=0.30)
        alpha = _pulse(self.pulse_phase, period) if period else 1.0
        self._circle(x + pad_x + dot / 2.0, cy, dot, color, alpha=alpha)
        self._text(label, x + pad_x + dot + gap, cy, 17, label_color, "mono",
                   tracking=0.20, valign="middle")

    # ── A01 now playing ──────────────────────────────────────────────────────

    def _draw_hero_playing(self, track):
        self._draw_album_art(PAD_X, BODY_TOP, ART, track)

        self._circle(INFO_X + 5, NP_LABEL_Y + 12, 10, C.MAGENTA,
                     alpha=_pulse(self.pulse_phase, 1.8))
        self._text("NOW PLAYING", INFO_X + 24, NP_LABEL_Y + 12, 19,
                   C.MAGENTA_SOFT, "mono", tracking=0.24, valign="middle")

        self._text(str(track.get("title", "Unknown Track")).upper(),
                   INFO_X, NP_TITLE_Y, 76, C.TEXT, "display", tracking=0.02,
                   max_w=INFO_W)
        self._text(track.get("artist") or "Unknown Artist", INFO_X, NP_ARTIST_Y,
                   34, C.TEXT_SEC, max_w=INFO_W)

        meta = [p for p in (str(track.get("album", "")).upper() or None,
                            f"CARD {_fmt_uid(track.get('uid'))}"
                            if track.get("uid") else None) if p]
        if meta:
            self._text(" · ".join(meta), INFO_X, NP_META_Y, 18, C.TEXT_MUTED,
                       "mono", tracking=0.16, max_w=INFO_W)

        self._draw_progress(track)
        self._draw_transport()

    def _draw_album_art(self, x, y, size, track):
        cache_key = f"{track.get('title', '?')}_{'img' if track.get('image_bytes') else 'no'}"
        if self._art_cache_key != cache_key:
            self._art_cache_key = cache_key
            img_bytes = track.get("image_bytes")
            self._art_texture = _load_image_bytes(img_bytes) if img_bytes else None

        # glowPulse 3.4s: 24px @ .35 ↔ 60px @ .6
        p = _ease_in_out((self.drift_phase % 3.4) / 3.4)
        self._glow(x, y, size, size, 22, C.INDIGO, 0.35 + 0.25 * p,
                   24 + 36 * p)

        if self._art_texture:
            self._fill(x, y, size, size, C.WHITE, radius=22,
                       texture=self._art_texture)
        else:
            self._grad_fill(x, y, size, size, _rgba(C.INDIGO, 0.42),
                            _rgba(C.MAGENTA, 0.24), radius=22)
            initial = (str(track.get("title", "?")) or "?")[0].upper()
            self._text(initial, x + size / 2.0, y + size / 2.0, 200,
                       C.WHITE, "display", halign="center", valign="middle",
                       alpha=0.16)
        self._stroke(x, y, size, size, C.INDIGO_SOFT, radius=22, alpha=0.35)

    def _draw_progress(self, track):
        duration = track.get("duration")
        elapsed = 0.0
        if self.player.play_start_time:
            elapsed = _time.time() - self.player.play_start_time
        pct = self._progress_smooth if duration else 0.0
        pct = max(0.0, min(1.0, pct))

        self._fill(INFO_X, NP_PROG_Y, INFO_W, 8, C.WHITE, radius=4, alpha=0.08)
        fill_w = INFO_W * pct
        if fill_w > 2:
            self._glow(INFO_X, NP_PROG_Y, fill_w, 8, 4, C.MAGENTA, 0.45, 24)
            self._grad_fill(INFO_X, NP_PROG_Y, fill_w, 8, C.INDIGO, C.MAGENTA,
                            radius=4, diagonal=False)
            dot_x = INFO_X + fill_w
            self._glow_circle(dot_x, NP_PROG_Y + 4, 22, C.TEXT, 0.55, 20)
            self._circle(dot_x, NP_PROG_Y + 4, 22, C.TEXT)

        self._text(_fmt_time(elapsed), INFO_X, NP_TIMES_Y, 22, C.TEXT_SEC,
                   "mono", tracking=0.08)
        self._text(_fmt_time(duration) if duration else "--:--",
                   INFO_X + INFO_W, NP_TIMES_Y, 22, C.TEXT_DIM, "mono",
                   tracking=0.08, halign="right")

    def _draw_transport(self):
        cy = NP_TRANSPORT_Y + 52
        x = INFO_X

        # play / pause — gradient fill + brand glow
        pressed = self._pressed("playpause")
        self._glow_circle(x + 52, cy, 104, C.INDIGO, 0.45 if not pressed else 0.6, 30)
        self._circle(x + 52, cy, 104, C.WHITE,
                     texture=_grad_tex(C.INDIGO, C.MAGENTA))
        if self.player.is_playing:
            self._icon_pause(x + 52, cy, 34, C.WHITE)
        else:
            self._icon_play(x + 52, cy, 34, C.WHITE)
        self._hit("playpause", x, cy - 52, 104, 104)

        x += 104 + 32
        self._circle(x + 44, cy, 88, C.WHITE,
                     alpha=0.06 if self._pressed("skip") else 0.0)
        self._circle_stroke(x + 44, cy, 88, C.WHITE, alpha=0.18)
        self._icon_skip(x + 44, cy, 30, C.TEXT_SEC)
        self._hit("skip", x, cy - 44, 88, 88)

        x += 88 + 32 + 6
        self._vrule(x, cy - 28, 56, alpha=0.10)
        x += 1 + 6 + 32

        self._icon_volume(x + 14, cy, 28, C.TEXT_SEC)
        x += 28 + 20
        self._draw_volume_slider(x, cy, 260)
        self._text(str(int(self.volume)), x + 260 + 20, cy, 18, C.TEXT_MUTED,
                   "mono", valign="middle")

    def _draw_volume_slider(self, x, cy, width):
        pct = max(0.0, min(1.0, self.volume / 100.0))
        self._fill(x, cy - 3, width, 6, C.WHITE, radius=3, alpha=0.08)
        if pct > 0:
            self._fill(x, cy - 3, width * pct, 6, C.INDIGO_SOFT, radius=3)
        self._circle(x + width * pct, cy, 18, C.TEXT)
        self._hit("volume", x - 12, cy - 22, width + 24, 44)

    # ── A02 idle ─────────────────────────────────────────────────────────────

    def _draw_hero_idle(self, upcoming):
        bottom = (QUEUE_HEAD_Y if upcoming else EMPTY_HEAD_Y) - 30
        top = PAD_TOP + TOPBAR_H

        error = self.player.last_error
        hint = ("Hold your NFC card against the box. "
                "Your song joins the queue and plays next.")
        hint_lines = self._wrap(hint, 30, 900, max_lines=2)

        block = 340 + 56 + 94 + 24 + len(hint_lines) * 42 + 38 + 26
        y = (top + bottom) / 2.0 - block / 2.0 - 40
        y = max(top + 8, y)
        cx = DESIGN_W / 2.0

        ring_cy = y + 170
        for i in range(3):
            # `ping` 2.6s, staggered .9s: scale .6→1.9, opacity .55→0
            p = ((self.ripple_phase + i * 0.9) % 2.6) / 2.6
            scale = 0.6 + 1.3 * p
            alpha = max(0.0, 0.55 * (1.0 - p))
            color = (C.INDIGO_SOFT, C.MAGENTA, C.INDIGO)[i]
            self._circle_stroke(cx, ring_cy, 340 * scale, color,
                                alpha=alpha * (0.4, 0.35, 0.3)[i] / 0.4)

        self._draw_nfc_card(cx, ring_cy)

        y += 340 + 56
        self._text("TAP YOUR CARD", cx, y, 78, C.TEXT, "display", tracking=0.06,
                   halign="center")
        y += 94 + 24
        for line in hint_lines:
            self._text(line, cx, y, 30, C.TEXT_SEC, halign="center")
            y += 42
        y += 38

        if error:
            self._text(str(error)[:70], cx, y, 18, C.DANGER, "mono",
                       tracking=0.16, halign="center")
        else:
            label = "NO CARD DETECTED"
            lw = self._measure(label, 18, "mono", tracking=0.16)
            self._text(label, cx, y + 13, 18, C.TEXT_DIM, "mono", tracking=0.16,
                       halign="center", valign="middle")
            self._rule(cx - lw / 2.0 - 18 - 60, y + 13, 60, alpha=B_DEFAULT)
            self._rule(cx + lw / 2.0 + 18, y + 13, 60, alpha=B_DEFAULT)

    def _draw_nfc_card(self, cx, cy):
        w, h = 186, 124
        x, y = cx - w / 2.0, cy - h / 2.0
        self._glow(x, y, w, h, 16, C.INDIGO_SOFT, 0.22, 40)
        self._grad_fill(x, y, w, h, _rgba(C.INDIGO, 0.18),
                        _rgba(C.MAGENTA, 0.12), radius=16)
        self._stroke(x, y, w, h, C.INDIGO_SOFT, radius=16, alpha=0.45)
        self._fill(x + 20, y + 20, 44, 32, C.TEXT, radius=6, alpha=0.22)
        self._icon_nfc(x + w - 18 - 17, y + h - 16 - 17, 34, C.MAGENTA_SOFT)

    # ── A03 loading ──────────────────────────────────────────────────────────

    def _draw_hero_loading(self, track):
        # skeleton album tile
        self._fill(PAD_X, BODY_TOP, ART, ART, C.SURFACE, radius=22)
        self._grad_fill(PAD_X, BODY_TOP, ART, ART, _rgba(C.INDIGO, 0.16),
                        _rgba(C.MAGENTA, 0.10), radius=22)
        self._shimmer(PAD_X, BODY_TOP, ART, ART, 22)
        self._stroke(PAD_X, BODY_TOP, ART, ART, C.WHITE, radius=22, alpha=0.10)
        self._icon_spinner(PAD_X + ART / 2.0, BODY_TOP + ART / 2.0, 72,
                           C.INDIGO_SOFT)

        self._circle(INFO_X + 5, NP_LABEL_Y + 12, 10, C.INDIGO_SOFT,
                     alpha=_pulse(self.pulse_phase, 1.0))
        uid = _fmt_uid(track.get("uid")) if track.get("uid") else ""
        self._text(f"READING CARD {uid}".strip(), INFO_X, NP_LABEL_Y + 12, 19,
                   C.INDIGO_SOFT, "mono", tracking=0.24, valign="middle",
                   max_w=INFO_W)

        title = str(track.get("title") or "").strip()
        self._text(title.upper() if title else "LOADING…", INFO_X, NP_TITLE_Y,
                   76, C.TEXT, "display", tracking=0.02, max_w=INFO_W)

        artist = str(track.get("artist") or "").strip()
        if artist:
            self._text(artist, INFO_X, NP_ARTIST_Y, 34, C.TEXT_SEC, max_w=INFO_W)
        else:
            self._fill(INFO_X, NP_ARTIST_Y, 520, 26, C.WHITE, radius=8, alpha=0.07)
            self._shimmer(INFO_X, NP_ARTIST_Y, 520, 26, 8, peak=0.18)
            self._fill(INFO_X, NP_ARTIST_Y + 44, 320, 20, C.WHITE, radius=8,
                       alpha=0.05)

        # indeterminate bar — `slideLoad` 1.6s
        self._fill(INFO_X, LOAD_BAR_Y, INFO_W, 8, C.WHITE, radius=4, alpha=0.08)
        seg = INFO_W * 0.28
        p = (self.shimmer_phase % 1.6) / 1.6
        sx = INFO_X - seg * 0.28 + (INFO_W + seg * 0.28) * p
        left = max(INFO_X, sx)
        right = min(INFO_X + INFO_W, sx + seg)
        if right > left:
            self._grad_fill(left, LOAD_BAR_Y, right - left, 8, C.INDIGO,
                            C.MAGENTA, radius=4, diagonal=False)

        source = track.get("file_path") or ""
        name = os.path.basename(str(source)) if source else "—"
        self._text(f"DECODING AUDIO · {name}", INFO_X, LOAD_STATUS_Y, 20,
                   C.TEXT_MUTED, "mono", tracking=0.14, max_w=INFO_W)

    def _draw_loading_footer(self, upcoming):
        self._rule(PAD_X, LOAD_RULE_Y, CONTENT_R - PAD_X)
        y = LOAD_RULE_Y + 30 + 13
        x = PAD_X
        self._text("NEXT UP", x, y, 19, C.INDIGO_SOFT, "mono", tracking=0.16,
                   valign="middle")
        x += self._measure("NEXT UP", 19, "mono", tracking=0.16) + 26

        if upcoming:
            nxt = upcoming[0]
            label = nxt.get("title", "Unknown")
            if nxt.get("artist"):
                label = f"{label} — {nxt['artist']}"
            self._text(label, x, y, 19, C.TEXT_SEC, "mono", tracking=0.16,
                       valign="middle", max_w=760)
            x += min(760, self._measure(label, 19, "mono", tracking=0.16)) + 26
            self._vrule(x, y - 13, 26, alpha=0.10)
            more = max(0, len(upcoming) - 1)
            self._text(f"{more} MORE IN QUEUE", x + 27, y, 19, C.TEXT_DIM,
                       "mono", tracking=0.16, valign="middle")
        else:
            self._text("QUEUE IS EMPTY", x, y, 19, C.TEXT_DIM, "mono",
                       tracking=0.16, valign="middle")

    # ── queue rail ───────────────────────────────────────────────────────────

    def _draw_queue_header(self, head_y, rule_y, upcoming):
        self._text("UP NEXT", PAD_X, head_y, 19, C.INDIGO_SOFT, "mono",
                   tracking=0.24)
        x = PAD_X + self._measure("UP NEXT", 19, "mono", tracking=0.24) + 20
        self._text(self._queue_meta(upcoming), x, head_y + 1, 18, C.TEXT_DIM,
                   "mono", tracking=0.10)

        if upcoming:
            label = "CLEAR QUEUE"
            lw = self._measure(label, 17, "mono", tracking=0.14)
            self._text(label, CONTENT_R, head_y + 12, 17, C.TEXT_MUTED, "mono",
                       tracking=0.14, halign="right", valign="middle")
            self._icon_close(CONTENT_R - lw - 16 - 10, head_y + 12, 20,
                             C.TEXT_MUTED)
            self._hit("clear_queue", CONTENT_R - lw - 46, head_y - 8,
                      lw + 46, 44)

        self._rule(PAD_X, rule_y, CONTENT_R - PAD_X)

    def _queue_meta(self, upcoming):
        n = len(upcoming)
        if not n:
            return "0 TRACKS"
        total, known = 0.0, 0
        for item in upcoming:
            duration = item.get("duration")
            if duration:
                total += float(duration)
                known += 1
        if not known:
            return f"{n} TRACKS"
        total += (n - known) * 210.0
        return f"{n} TRACKS · {max(1, int(total // 60))} MIN"

    def _draw_empty_queue(self):
        self._draw_queue_header(EMPTY_HEAD_Y, EMPTY_RULE_Y, [])
        self._stroke(PAD_X, EMPTY_TOP, CONTENT_R - PAD_X, EMPTY_PANEL_H,
                     C.WHITE, radius=16, alpha=0.10, dash=10)
        cx = DESIGN_W / 2.0
        self._text("QUEUE IS EMPTY", cx, EMPTY_TOP + 72, 22, C.TEXT_MUTED,
                   "mono", tracking=0.20, halign="center")
        self._text("Up to 20 tracks can wait in line", cx, EMPTY_TOP + 112, 20,
                   C.TEXT_DIM, halign="center")

    def _draw_queue_row(self, upcoming):
        self._draw_queue_header(QUEUE_HEAD_Y, QUEUE_RULE_Y, upcoming)

        visible = min(len(upcoming), QUEUE_SLOTS)
        for i in range(visible):
            x = PAD_X + i * (QUEUE_CARD_W + QUEUE_CARD_GAP)
            self._draw_queue_card(upcoming[i], i, x)

        extra = len(upcoming) - visible
        if extra > 0:
            x = PAD_X + visible * (QUEUE_CARD_W + QUEUE_CARD_GAP)
            width = CONTENT_R - x
            if width > 120:
                self._stroke(x, QUEUE_TOP, width, QUEUE_ART_H, C.WHITE,
                             radius=14, alpha=B_DEFAULT, dash=10)
                cx = x + width / 2.0
                self._text(f"+{extra}", cx, QUEUE_TOP + 44, 34, C.INDIGO_SOFT,
                           "display", halign="center")
                self._text("MORE", cx, QUEUE_TOP + 98, 16, C.TEXT_MUTED, "mono",
                           tracking=0.16, halign="center")

    def _draw_queue_card(self, item, index, x):
        uid = item.get("uid", f"idx_{index}")
        slide = self._queue_slide.get(uid, 1.0)
        alpha = min(1.0, slide * 1.6)
        y = QUEUE_TOP + (1.0 - _ease_out_back(slide)) * 24

        just_added = uid is not None and uid == self._new_queue_uid
        (c0, a0), (c1, a1), num_a = QUEUE_TINTS[min(index, len(QUEUE_TINTS) - 1)]

        if just_added:
            c0, a0, c1, a1, num_a = C.MAGENTA, 0.32, C.INDIGO, 0.20, 0.20
            self._glow(x, y, QUEUE_CARD_W, QUEUE_ART_H, 14, C.INDIGO_SOFT,
                       0.22 * alpha, 40)

        image = self._queue_thumb(item, uid)
        if image is not None:
            self._fill(x, y, QUEUE_CARD_W, QUEUE_ART_H, C.WHITE, radius=14,
                       alpha=alpha, texture=image)
            self._fill(x, y + QUEUE_ART_H - 60, QUEUE_CARD_W, 60, C.VOID,
                       alpha=0.45 * alpha)
            num_a = 0.55
        else:
            self._grad_fill(x, y, QUEUE_CARD_W, QUEUE_ART_H, _rgba(c0, a0),
                            _rgba(c1, a1), radius=14, alpha=alpha)

        if index == 0 or just_added:
            border, border_a = ((C.MAGENTA, 0.50) if just_added
                                else (C.INDIGO_SOFT, 0.35))
        else:
            border, border_a = C.WHITE, B_SUBTLE
        self._stroke(x, y, QUEUE_CARD_W, QUEUE_ART_H, border, radius=14,
                     alpha=border_a * alpha)

        self._text(f"{index + 1:02d}", x + 16, y + QUEUE_ART_H - 8, 52, C.WHITE,
                   "display", valign="bottom", alpha=num_a * alpha)

        tag = "JUST ADDED" if just_added else ("NEXT" if index == 0 else None)
        if tag:
            self._text(tag, x + QUEUE_CARD_W - 14, y + 14, 15, C.MAGENTA_SOFT,
                       "mono", tracking=0.14, halign="right", alpha=alpha)

        self._text(item.get("title", "Unknown"), x, y + QUEUE_ART_H + 16, 24,
                   C.TEXT, bold=True, max_w=QUEUE_CARD_W, alpha=alpha)
        artist = item.get("artist")
        if artist:
            self._text(artist, x, y + QUEUE_ART_H + 54, 19, C.TEXT_MUTED,
                       max_w=QUEUE_CARD_W, alpha=alpha)

    def _queue_thumb(self, item, uid):
        img_bytes = item.get("image_bytes")
        if not img_bytes:
            return None
        if uid not in self._mini_cache:
            self._mini_cache[uid] = _load_image_bytes(img_bytes)
        return self._mini_cache.get(uid)

    # ── A04 added-to-queue confirmation ──────────────────────────────────────

    def _draw_added_panel(self):
        data = self.added_item
        alpha = self.added_alpha

        # radial-gradient(1200px 700px at 50% 108%) — a wash rising from below
        # the bottom edge, so the queue rail keeps its contrast.
        Color(*C.MAGENTA[:3], 0.34 * alpha)
        Rectangle(texture=_radial_tex(),
                  **self._rect_kw(DESIGN_W / 2.0 - 840, DESIGN_H * 1.08 - 490,
                                  1680, 980))

        title = str(data["title"]).upper()
        sub = data["artist"] or "Queued"
        if data.get("wait"):
            mins = max(1, int(round(data["wait"] / 60.0)))
            sub = f"{sub} · plays in about {mins} minute{'s' if mins != 1 else ''}"
        pos = f"#{data['position']}"

        badge, gap, pad_x, pad_y = 76, 34, 56, 34
        col_w = max(self._measure("ADDED TO QUEUE", 18, "mono", tracking=0.22),
                    self._measure(title, 40, "display", tracking=0.02),
                    self._measure(sub, 22))
        col_w = min(col_w, 780)
        pos_w = max(self._measure(pos, 52, "display"),
                    self._measure("IN LINE", 16, "mono", tracking=0.16))

        pw = pad_x * 2 + badge + gap + col_w + gap + 1 + 16 + pos_w
        ph = pad_y * 2 + 122
        x = DESIGN_W / 2.0 - pw / 2.0
        y = 0.52 * DESIGN_H

        rise = (1.0 - alpha) * 18
        y += rise

        self._glow(x, y, pw, ph, 24, C.MAGENTA, 0.45 * alpha, 40)
        self._fill(x, y, pw, ph, C.SURFACE, radius=24, alpha=0.86 * alpha)
        self._stroke(x, y, pw, ph, C.INDIGO_SOFT, radius=24, alpha=0.35 * alpha)

        bx = x + pad_x
        self._circle(bx + badge / 2.0, y + ph / 2.0, badge, C.WHITE,
                     alpha=alpha, texture=_grad_tex(C.INDIGO, C.MAGENTA))
        self._icon_check(bx + badge / 2.0, y + ph / 2.0, 34, C.WHITE,
                         width=2.2, alpha=alpha)

        tx = bx + badge + gap
        ty = y + pad_y
        self._text("ADDED TO QUEUE", tx, ty, 18, C.MAGENTA_SOFT, "mono",
                   tracking=0.22, alpha=alpha)
        self._text(title, tx, ty + 34, 40, C.TEXT, "display", tracking=0.02,
                   max_w=col_w, alpha=alpha)
        self._text(sub, tx, ty + 92, 22, C.TEXT_SEC, max_w=col_w, alpha=alpha)

        dx = tx + col_w + gap
        self._vrule(dx, y + ph / 2.0 - 48, 96, alpha=B_DEFAULT * alpha)

        px = dx + 1 + 16 + pos_w / 2.0
        self._text(pos, px, y + pad_y + 14, 52, C.INDIGO_SOFT, "display",
                   halign="center", alpha=alpha)
        self._text("IN LINE", px, y + pad_y + 92, 16, C.TEXT_MUTED, "mono",
                   tracking=0.16, halign="center", alpha=alpha)

    # ── A05 unknown card ─────────────────────────────────────────────────────

    def _draw_unknown_modal(self):
        alpha = self.unknown_alpha
        uid = _fmt_uid(self.unknown_uid)

        Color(*C.DANGER[:3], 0.16 * alpha)
        Rectangle(texture=_radial_tex(),
                  **self._rect_kw(DESIGN_W / 2.0 - 1000, DESIGN_H / 2.0 - 620,
                                  2000, 1240))
        self._fill(0, 0, DESIGN_W, DESIGN_H, C.BASE, alpha=0.72 * alpha)

        body = ("This card isn't linked to a track yet. Ask the cashier to "
                "register it, or tap a different card.")
        lines = self._wrap(body, 26, 820, max_lines=3)

        pw, pad_x, pad_y = 1020, 80, 72
        ph = pad_y * 2 + 99 + 36 + len(lines) * 42 + 44 + 84 + 48 + 75
        x = DESIGN_W / 2.0 - pw / 2.0
        y = DESIGN_H / 2.0 - ph / 2.0 + (1.0 - alpha) * 16

        # a blocking modal: swallow taps that miss its controls
        self._hit("unknown.scrim", 0, 0, DESIGN_W, DESIGN_H)

        self._glow(x, y, pw, ph, 24, C.VOID, 0.55 * alpha, 48)
        self._fill(x, y, pw, ph, C.SURFACE, radius=24, alpha=0.88 * alpha)
        self._stroke(x, y, pw, ph, C.DANGER, radius=24, alpha=0.38 * alpha)

        cx, cy = x + pad_x, y + pad_y
        self._circle(cx + 39, cy + 39, 78, C.DANGER, alpha=0.10 * alpha)
        self._circle_stroke(cx + 39, cy + 39, 78, C.DANGER, alpha=0.50 * alpha)
        self._icon_warning(cx + 39, cy + 39, 36, C.DANGER, alpha=alpha)

        tx = cx + 78 + 26
        self._text("CARD NOT REGISTERED", tx, cy + 6, 19, C.DANGER, "mono",
                   tracking=0.22, alpha=alpha)
        self._text("NO SONG ON THIS CARD", tx, cy + 37, 52, C.TEXT, "display",
                   tracking=0.02, alpha=alpha)

        ty = cy + 99 + 36
        for line in lines:
            self._text(line, cx, ty, 26, C.TEXT_SEC, alpha=alpha)
            ty += 42

        ty += 44
        row_w = pw - pad_x * 2
        self._fill(cx, ty, row_w, 84, C.WHITE, radius=12, alpha=0.03 * alpha)
        self._stroke(cx, ty, row_w, 84, C.WHITE, radius=12, alpha=B_SUBTLE * alpha)
        self._text("CARD UID", cx + 28, ty + 42, 17, C.TEXT_MUTED, "mono",
                   tracking=0.20, valign="middle", alpha=alpha)
        lw = self._measure("CARD UID", 17, "mono", tracking=0.20)
        self._text(uid, cx + 28 + lw + 22, ty + 42, 30, C.TEXT, "mono",
                   tracking=0.14, valign="middle", alpha=alpha)

        bw, bh = 195, 45
        badge_x = cx + row_w - 28 - bw
        self._fill(badge_x, ty + 42 - bh / 2.0, bw, bh, C.WARNING,
                   radius=bh / 2.0, alpha=0.14 * alpha)
        self._stroke(badge_x, ty + 42 - bh / 2.0, bw, bh, C.WARNING,
                     radius=bh / 2.0, alpha=0.40 * alpha)
        self._text("UNKNOWN", badge_x + bw / 2.0, ty + 42, 18, C.WARNING,
                   "mono", tracking=0.12, halign="center", valign="middle",
                   alpha=alpha)

        ay = ty + 84 + 48
        self._draw_button("unknown.register", cx, ay, 406, 75,
                          "Register this card", kind="primary", alpha=alpha)
        self._draw_button("unknown.dismiss", cx + 406 + 24, ay, 218, 75,
                          "Dismiss", kind="ghost", alpha=alpha)

        left = max(0, int(math.ceil(self.unknown_until - _time.time())))
        self._text(f"CLOSES IN {left}S", cx + row_w, ay + 38, 18, C.TEXT_DIM,
                   "mono", tracking=0.14, halign="right", valign="middle",
                   alpha=alpha)

    def _dismiss_unknown(self):
        self.unknown_until = 0
        self.dirty = True

    def _register_unknown_card(self):
        uid = self.unknown_uid
        self._dismiss_unknown()
        self.unknown_uid = None
        self.unknown_alpha = 0.0
        self._open_register()
        if uid:
            self._begin_register_with_uid(uid)

    # ── shared controls ──────────────────────────────────────────────────────

    def _draw_button(self, name, x, y, w, h, label, kind="secondary",
                     alpha=1.0, size=None, enabled=True):
        radius = h / 2.0
        pressed = self._pressed(name)
        size = size or min(22, h * 0.34)

        if kind == "primary":
            self._glow(x, y, w, h, radius, C.INDIGO, 0.45 * alpha, 24)
            self._fill(x, y, w, h, C.WHITE, radius=radius, alpha=alpha,
                       texture=_grad_tex(C.INDIGO, C.MAGENTA))
            fg = C.WHITE
        elif kind == "ghost":
            self._stroke(x, y, w, h, C.WHITE, radius=radius,
                         alpha=B_DEFAULT * alpha)
            fg = C.TEXT_SEC
        elif kind == "danger":
            self._fill(x, y, w, h, C.DANGER, radius=radius, alpha=0.12 * alpha)
            self._stroke(x, y, w, h, C.DANGER, radius=radius, alpha=0.45 * alpha)
            fg = C.DANGER
        else:  # secondary
            self._fill(x, y, w, h, C.WHITE, radius=radius, alpha=0.05 * alpha)
            self._stroke(x, y, w, h, C.WHITE, radius=radius,
                         alpha=B_STRONG * alpha)
            fg = C.TEXT

        if not enabled:
            fg = C.TEXT_DIM
        if pressed:
            self._fill(x, y, w, h, C.WHITE, radius=radius, alpha=0.08)

        self._text(label, x + w / 2.0, y + h / 2.0, size, fg, bold=True,
                   halign="center", valign="middle", alpha=alpha, max_w=w - 32)
        if enabled:
            self._hit(name, x, y, w, h)

    def _draw_icon_button(self, name, x, y, d, icon, color=C.TEXT_MUTED,
                          alpha=1.0):
        if self._pressed(name):
            self._circle(x + d / 2.0, y + d / 2.0, d, C.WHITE, alpha=0.08)
        self._circle_stroke(x + d / 2.0, y + d / 2.0, d, C.WHITE,
                            alpha=B_DEFAULT * alpha)
        icon(x + d / 2.0, y + d / 2.0, d * 0.48, color, alpha=alpha)
        self._hit(name, x, y, d, d)

    # ── icons (line art recreated on the canvas) ─────────────────────────────

    def _icon_power(self, cx, cy, size, color, alpha=1.0):
        r = size * 0.38
        self._circle_stroke(cx, cy + size * 0.06, r * 2, color, alpha=alpha,
                            width=1.6, angle_start=30, angle_end=330)
        f = self.f
        Color(*color[:3], alpha)
        Line(points=[f.x(cx), f.y(cy - size * 0.42), f.x(cx), f.y(cy + size * 0.02)],
             width=max(1.0, f.u(1.6)))

    def _icon_play(self, cx, cy, size, color, alpha=1.0):
        f = self.f
        r = size * 0.5
        Color(*color[:3], alpha)
        Triangle(points=[
            f.x(cx - r * 0.7), f.y(cy - r),
            f.x(cx - r * 0.7), f.y(cy + r),
            f.x(cx + r * 0.9), f.y(cy),
        ])

    def _icon_pause(self, cx, cy, size, color, alpha=1.0):
        bar_w, bar_h, gap = size * 0.24, size * 0.94, size * 0.22
        self._fill(cx - gap / 2.0 - bar_w, cy - bar_h / 2.0, bar_w, bar_h,
                   color, radius=bar_w * 0.28, alpha=alpha)
        self._fill(cx + gap / 2.0, cy - bar_h / 2.0, bar_w, bar_h, color,
                   radius=bar_w * 0.28, alpha=alpha)

    def _icon_skip(self, cx, cy, size, color, alpha=1.0):
        f = self.f
        r = size * 0.5
        Color(*color[:3], alpha)
        Triangle(points=[
            f.x(cx - r), f.y(cy - r),
            f.x(cx - r), f.y(cy + r),
            f.x(cx + r * 0.45), f.y(cy),
        ])
        self._fill(cx + r * 0.62, cy - r, size * 0.16, size, color,
                   radius=size * 0.06, alpha=alpha)

    def _icon_volume(self, cx, cy, size, color, alpha=1.0):
        f = self.f
        r = size * 0.5
        self._fill(cx - r, cy - r * 0.38, r * 0.55, r * 0.76, color, alpha=alpha)
        Color(*color[:3], alpha)
        Triangle(points=[
            f.x(cx - r * 0.45), f.y(cy - r * 0.38),
            f.x(cx - r * 0.45), f.y(cy + r * 0.38),
            f.x(cx + r * 0.1), f.y(cy - r * 0.85),
        ])
        Triangle(points=[
            f.x(cx - r * 0.45), f.y(cy + r * 0.38),
            f.x(cx + r * 0.1), f.y(cy + r * 0.85),
            f.x(cx + r * 0.1), f.y(cy - r * 0.85),
        ])
        for i, rr in enumerate((r * 0.75, r * 1.15)):
            self._circle_stroke(cx + r * 0.05, cy, rr * 2, color,
                                alpha=alpha * (1.0 - i * 0.25), width=1.6,
                                angle_start=55, angle_end=125)

    def _icon_check(self, cx, cy, size, color, width=2.0, alpha=1.0):
        f = self.f
        r = size * 0.5
        Color(*color[:3], alpha)
        Line(points=[
            f.x(cx - r * 0.72), f.y(cy + r * 0.05),
            f.x(cx - r * 0.18), f.y(cy + r * 0.55),
            f.x(cx + r * 0.72), f.y(cy - r * 0.5),
        ], width=max(1.0, f.u(width)), joint="round", cap="round")

    def _icon_close(self, cx, cy, size, color, alpha=1.0):
        f = self.f
        r = size * 0.35
        Color(*color[:3], alpha)
        w = max(1.0, f.u(1.6))
        Line(points=[f.x(cx - r), f.y(cy - r), f.x(cx + r), f.y(cy + r)],
             width=w, cap="round")
        Line(points=[f.x(cx - r), f.y(cy + r), f.x(cx + r), f.y(cy - r)],
             width=w, cap="round")

    def _icon_warning(self, cx, cy, size, color, alpha=1.0):
        self._circle_stroke(cx, cy, size, color, alpha=alpha, width=1.8)
        f = self.f
        Color(*color[:3], alpha)
        Line(points=[f.x(cx), f.y(cy - size * 0.22), f.x(cx), f.y(cy + size * 0.12)],
             width=max(1.0, f.u(1.8)), cap="round")
        self._circle(cx, cy + size * 0.3, size * 0.1, color, alpha=alpha)

    def _icon_nfc(self, cx, cy, size, color, alpha=1.0):
        for i in range(3):
            r = size * (0.24 + 0.22 * i)
            self._circle_stroke(cx - size * 0.34, cy, r * 2, color,
                                alpha=alpha * (1.0 - i * 0.18), width=1.6,
                                angle_start=45, angle_end=135)

    def _icon_spinner(self, cx, cy, size, color, alpha=1.0):
        self._circle_stroke(cx, cy, size, color, alpha=0.25 * alpha, width=1.2)
        angle = (self.spin_phase * 260.0) % 360.0
        self._circle_stroke(cx, cy, size, color, alpha=0.9 * alpha, width=1.6,
                            angle_start=angle, angle_end=angle + 100)

    # ── overlays ─────────────────────────────────────────────────────────────

    def _draw_flash(self):
        elapsed = _time.time() - self.flash_start
        progress = min(1.0, elapsed / self.flash_duration)
        alpha = _flash_alpha(progress)

        layers = [
            (self.flash_tint[0], 0.16, 1.2, 0.7, 1.1),
            (self.flash_tint[1], 0.12, 0.7, 0.5, 1.0),
        ]
        for i, (color, peak, rx_m, ry_m, y_off) in enumerate(layers):
            delay = i * 0.12
            if elapsed < delay:
                continue
            p = min(1.0, (elapsed - delay) / self.flash_duration)
            a = _flash_alpha(p) * peak * alpha
            if a <= 0.005:
                continue
            Color(*color[:3], a)
            Rectangle(texture=_radial_tex(),
                      **self._rect_kw(DESIGN_W / 2.0 - DESIGN_W * rx_m / 2.0,
                                      DESIGN_H * (1.0 - y_off) - DESIGN_H * ry_m / 2.0,
                                      DESIGN_W * rx_m, DESIGN_H * ry_m))

    def _draw_toast(self):
        alpha = min(self.toast_alpha, 1.0)
        text = self.toast_message
        tw = self._measure(text, 24, bold=True)
        w = tw + 96
        h = 84
        x = DESIGN_W / 2.0 - w / 2.0
        y = DESIGN_H - 80 - h + (1.0 - alpha) * 20

        self._glow(x, y, w, h, h / 2.0, C.INDIGO, 0.35 * alpha, 24)
        self._fill(x, y, w, h, C.SURFACE, radius=h / 2.0, alpha=0.9 * alpha)
        self._stroke(x, y, w, h, C.INDIGO_SOFT, radius=h / 2.0, alpha=0.35 * alpha)
        self._text(text, DESIGN_W / 2.0, y + h / 2.0, 24, C.TEXT, bold=True,
                   halign="center", valign="middle", alpha=alpha, max_w=w - 64)

    # ── A06 registration ─────────────────────────────────────────────────────

    def _draw_registration(self):
        self._draw_reg_topbar()

        if self.reg_state == REG_STATE_HOME:
            self._draw_reg_home()
        elif self.reg_state == REG_STATE_DONE:
            self._draw_reg_done()
        elif self.reg_state == REG_STATE_LIST:
            self._draw_reg_list()
        else:
            self._draw_reg_rail()
            if self.reg_state == REG_STATE_WAITING_SCAN:
                self._draw_reg_waiting()
            else:
                self._draw_reg_workbench()

    def _draw_reg_topbar(self):
        cy = TOPBAR_CY
        self._draw_wordmark(PAD_X, cy)
        x = PAD_X + self._measure("UTUNE", 40, "display", tracking=0.16) + 26
        self._vrule(x, cy - 17, 34)
        self._text("CARD REGISTRATION", x + 27, cy, 19, C.TEXT_MUTED, "mono",
                   tracking=0.18, valign="middle")

        bw, bh = 240, 63
        bx = CONTENT_R - bw
        self._draw_button("reg.back", bx, cy - bh / 2.0, bw, bh,
                          "Back to player", kind="ghost")

        label = " CARDS REGISTERED"
        lw = self._measure(label, 18, "mono", tracking=0.10)
        self._text(label, bx - 24, cy, 18, C.TEXT_MUTED, "mono", tracking=0.10,
                   halign="right", valign="middle")
        self._text(str(self._card_count()), bx - 24 - lw, cy, 18, C.TEXT_SEC,
                   "mono", tracking=0.10, halign="right", valign="middle")

    def _reg_steps(self):
        """(label, state) for the four-step rail, from the current reg_state."""
        order = [REG_STATE_WAITING_SCAN, REG_STATE_PICK_FILE,
                 REG_STATE_INPUT_TITLE, REG_STATE_CONFIRM]
        current = self.reg_state
        if current == REG_STATE_INPUT_ARTIST:
            current = REG_STATE_INPUT_TITLE
        active = order.index(current) if current in order else 0
        labels = ["01 · SCAN CARD", "02 · PICK TRACK", "03 · TITLE & ARTIST",
                  "04 · CONFIRM"]
        return [
            (labels[i], "done" if i < active else ("active" if i == active
                                                   else "pending"))
            for i in range(4)
        ]

    def _draw_reg_rail(self):
        self._text("LINK A CARD", PAD_X, BODY_TOP, 46, C.TEXT, "display",
                   tracking=0.03)
        self._text("TO A SONG", PAD_X, BODY_TOP + 53, 46, C.TEXT, "display",
                   tracking=0.03)

        y = BODY_TOP + 106 + 38
        for label, state in self._reg_steps():
            h = 72
            if state == "done":
                self._fill(PAD_X, y, REG_RAIL_W, h, C.SUCCESS, radius=12,
                           alpha=0.06)
                self._stroke(PAD_X, y, REG_RAIL_W, h, C.SUCCESS, radius=12,
                             alpha=0.30)
                self._icon_check(PAD_X + 26 + 13, y + h / 2.0, 26, C.SUCCESS)
                color = C.SUCCESS_SOFT
            elif state == "active":
                self._glow(PAD_X, y, REG_RAIL_W, h, 12, C.INDIGO_SOFT, 0.22, 40)
                self._fill(PAD_X, y, REG_RAIL_W, h, C.INDIGO, radius=12,
                           alpha=0.10)
                self._stroke(PAD_X, y, REG_RAIL_W, h, C.INDIGO_SOFT, radius=12,
                             alpha=0.40)
                self._circle(PAD_X + 26 + 6, y + h / 2.0, 12, C.INDIGO_SOFT,
                             alpha=_pulse(self.pulse_phase, 1.6))
                color = C.TEXT
            else:
                self._stroke(PAD_X, y, REG_RAIL_W, h, C.WHITE, radius=12,
                             alpha=B_SUBTLE)
                self._circle_stroke(PAD_X + 26 + 6, y + h / 2.0, 12, C.WHITE,
                                    alpha=0.20)
                color = C.TEXT_DIM
            self._text(label, PAD_X + 26 + 26 + 20, y + h / 2.0, 20, color,
                       "mono", tracking=0.14, valign="middle")
            y += h + 22

        # scanned-card readout, pinned to the bottom of the rail
        panel_h = 170
        py = BODY_BOTTOM - panel_h
        self._fill(PAD_X, py, REG_RAIL_W, panel_h, C.WHITE, radius=12, alpha=0.03)
        self._stroke(PAD_X, py, REG_RAIL_W, panel_h, C.WHITE, radius=12,
                     alpha=B_SUBTLE)
        self._text("SCANNED CARD", PAD_X + 26, py + 26, 17, C.TEXT_MUTED,
                   "mono", tracking=0.20)
        self._text(_fmt_uid(self.scanned_uid) if self.scanned_uid else "— — — —",
                   PAD_X + 26, py + 62, 34, C.TEXT, "mono", tracking=0.14,
                   max_w=REG_RAIL_W - 52)
        if self.existing_card:
            note = f"Already linked · {self.existing_card.get('title', '')}"
            color = C.WARNING
        elif self.scanned_uid:
            note = "New card · no track linked"
            color = C.TEXT_DIM
        else:
            note = "Waiting for a card…"
            color = C.TEXT_DIM
        self._text(note, PAD_X + 26, py + 118, 19, color,
                   max_w=REG_RAIL_W - 52)

    def _draw_reg_waiting(self):
        cx = REG_RIGHT_X + REG_RIGHT_W / 2.0
        cy = (BODY_TOP + BODY_BOTTOM) / 2.0 - 60

        for i in range(3):
            p = ((self.ripple_phase + i * 0.9) % 2.6) / 2.6
            self._circle_stroke(cx, cy, 300 * (0.6 + 1.3 * p),
                                (C.INDIGO_SOFT, C.MAGENTA, C.INDIGO)[i],
                                alpha=max(0.0, 0.5 * (1.0 - p)))
        self._draw_nfc_card(cx, cy)

        self._text("SCAN A CARD", cx, cy + 150, 52, C.TEXT, "display",
                   tracking=0.06, halign="center")
        self._text("Hold the card on the reader to link it to a track.",
                   cx, cy + 220, 26, C.TEXT_SEC, halign="center")
        self._draw_button("reg.cancel", cx - 110, BODY_BOTTOM - 75, 220, 75,
                          "Cancel", kind="ghost")

    def _draw_reg_workbench(self):
        self._text(f"MUSIC FOLDER · {len(self.local_files)} FILES",
                   REG_RIGHT_X, BODY_TOP, 19, C.INDIGO_SOFT, "mono",
                   tracking=0.24)
        self._text(self._short_folder(), CONTENT_R, BODY_TOP + 1, 18,
                   C.TEXT_DIM, "mono", tracking=0.12, halign="right", max_w=520)

        list_h = REG_ROW_H * REG_FILE_ROWS
        self._fill(REG_RIGHT_X, REG_LIST_Y, REG_RIGHT_W, list_h, C.SURFACE,
                   radius=16, alpha=0.55)
        self._stroke(REG_RIGHT_X, REG_LIST_Y, REG_RIGHT_W, list_h, C.WHITE,
                     radius=16, alpha=B_SUBTLE)

        if not self.local_files:
            self._text("No audio files found in the music folder",
                       REG_RIGHT_X + REG_RIGHT_W / 2.0, REG_LIST_Y + list_h / 2.0,
                       24, C.TEXT_MUTED, halign="center", valign="middle")
        else:
            visible = min(REG_FILE_ROWS, len(self.local_files) - self.file_scroll)
            for i in range(visible):
                self._draw_reg_file_row(self.file_scroll + i,
                                        REG_LIST_Y + i * REG_ROW_H,
                                        first=i == 0, last=i == visible - 1)

        self._draw_reg_fields()

        ready = bool(self.scanned_uid and self.reg_url
                     and (self.reg_title or self._get_input_text().strip()))
        self._draw_button("reg.confirm", REG_RIGHT_X, REG_ACTION_Y, 348, 75,
                          "Register card",
                          kind="primary" if ready else "secondary",
                          enabled=ready)
        self._draw_button("reg.cancel", REG_RIGHT_X + 348 + 24, REG_ACTION_Y,
                          203, 75, "Cancel", kind="ghost")
        if len(self.local_files) > REG_FILE_ROWS:
            self._text("SCROLL FOR MORE FILES", CONTENT_R, REG_ACTION_Y + 38,
                       18, C.TEXT_DIM, "mono", tracking=0.14, halign="right",
                       valign="middle")

    def _draw_reg_file_row(self, index, y, first=False, last=False):
        name = self.local_files[index]
        selected = index == self.file_selected
        x = REG_RIGHT_X

        if selected:
            # keep the selection wash inside the panel's rounded corners
            radius = (16 if first else 0, 16 if first else 0,
                      16 if last else 0, 16 if last else 0)
            self._grad_fill(x, y, REG_RIGHT_W, REG_ROW_H, _rgba(C.INDIGO, 0.18),
                            _rgba(C.MAGENTA, 0.10), radius=radius,
                            diagonal=False)
        if not last:
            self._rule(x, y + REG_ROW_H - 1, REG_RIGHT_W, alpha=0.06)

        thumb = 52
        tx, ty = x + 30, y + (REG_ROW_H - thumb) / 2.0
        if selected:
            self._grad_fill(tx, ty, thumb, thumb, C.INDIGO, C.MAGENTA, radius=8)
        else:
            tint = QUEUE_TINTS[index % len(QUEUE_TINTS)]
            self._grad_fill(tx, ty, thumb, thumb,
                            _rgba(tint[0][0], tint[0][1] * 0.7),
                            _rgba(tint[1][0], tint[1][1] * 0.7), radius=8)

        title, duration = self._file_meta_for(name)
        text_x = tx + thumb + 24
        text_w = REG_RIGHT_W - (text_x - x) - 30 - 120
        self._text(name, text_x, y + REG_ROW_H / 2.0, 24,
                   C.TEXT if selected else C.TEXT_SEC, bold=selected,
                   valign="middle", max_w=text_w)

        dur_x = x + REG_RIGHT_W - 30
        if selected:
            self._icon_check(dur_x - 12, y + REG_ROW_H / 2.0, 24, C.MAGENTA_SOFT)
            dur_x -= 48
        self._text(_fmt_time(duration) if duration else "—", dur_x,
                   y + REG_ROW_H / 2.0, 18,
                   C.INDIGO_SOFT if selected else C.TEXT_DIM, "mono",
                   halign="right", valign="middle")

        self._hit(f"reg.file.{index}", x, y, REG_RIGHT_W, REG_ROW_H)

    def _draw_reg_fields(self):
        gap = 32
        field_w = (REG_RIGHT_W - gap) / 2.0
        for i, (label, value, state) in enumerate((
            ("Title", self.reg_title, REG_STATE_INPUT_TITLE),
            ("Artist", self.reg_artist, REG_STATE_INPUT_ARTIST),
        )):
            x = REG_RIGHT_X + i * (field_w + gap)
            active = self.reg_state == state
            self._text(label.upper(), x, REG_FIELD_Y, 17,
                       C.INDIGO_SOFT if active else C.TEXT_MUTED, "mono",
                       tracking=0.20)
            y = REG_FIELD_Y + 30
            self._fill(x, y, field_w, REG_FIELD_H, C.SURFACE, radius=12,
                       alpha=0.6)
            self._stroke(x, y, field_w, REG_FIELD_H,
                         C.INDIGO_SOFT if active else C.WHITE, radius=12,
                         alpha=0.45 if active else B_SUBTLE)
            if not active:
                self._text(value or "—", x + 24, y + REG_FIELD_H / 2.0, 24,
                           C.TEXT if value else C.TEXT_DIM, valign="middle",
                           max_w=field_w - 48)
                self._hit(f"reg.field.{i}", x, y, field_w, REG_FIELD_H)

    def _reg_field_rect(self, index):
        """Kivy rect of one of the two inputs — shared by draw and TextInput."""
        gap = 32
        field_w = (REG_RIGHT_W - gap) / 2.0
        x = REG_RIGHT_X + index * (field_w + gap)
        return self.f.rect(x, REG_FIELD_Y + 30, field_w, REG_FIELD_H)

    def _draw_reg_home(self):
        cx = DESIGN_W / 2.0
        cy = DESIGN_H / 2.0
        self._text("CARD MANAGER", cx, cy - 220, 62, C.TEXT, "display",
                   tracking=0.06, halign="center")
        self._text(f"{self._card_count()} cards registered", cx, cy - 130, 30,
                   C.TEXT_SEC, halign="center")
        self._draw_button("reg.new", cx - 240, cy - 20, 480, 88,
                          "Register new card", kind="primary")
        self._draw_button("reg.list", cx - 240, cy + 92, 480, 88,
                          "View all cards", kind="ghost")

    def _draw_reg_done(self):
        cx = DESIGN_W / 2.0
        cy = DESIGN_H / 2.0
        self._glow_circle(cx, cy - 200, 140, C.SUCCESS, 0.35, 40)
        self._circle(cx, cy - 200, 140, C.SUCCESS, alpha=0.14)
        self._circle_stroke(cx, cy - 200, 140, C.SUCCESS, alpha=0.55, width=2)
        self._icon_check(cx, cy - 200, 64, C.SUCCESS, width=3)

        self._text("CARD REGISTERED", cx, cy - 90, 62, C.TEXT, "display",
                   tracking=0.06, halign="center")
        self._text(self.reg_title or "", cx, cy - 6, 30, C.TEXT_SEC,
                   halign="center", max_w=900)
        if self.reg_artist:
            self._text(self.reg_artist, cx, cy + 36, 24, C.TEXT_MUTED,
                       halign="center", max_w=900)
        self._text(_fmt_uid(self.scanned_uid), cx, cy + 84, 24, C.INDIGO_SOFT,
                   "mono", tracking=0.14, halign="center")

        self._draw_button("reg.new", cx - 380, cy + 160, 420, 88,
                          "Register another", kind="primary")
        self._draw_button("reg.back", cx + 20, cy + 160, 360, 88,
                          "Back to player", kind="ghost")

    def _draw_reg_list(self):
        self._text("REGISTERED CARDS", PAD_X, BODY_TOP, 46, C.TEXT, "display",
                   tracking=0.03)
        self._draw_button("reg.home", CONTENT_R - 220, BODY_TOP - 6, 220, 75,
                          "Back", kind="ghost")

        list_y = BODY_TOP + 100
        list_w = CONTENT_R - PAD_X
        list_h = REG_ROW_H * REG_CARDS_ROWS
        self._fill(PAD_X, list_y, list_w, list_h, C.SURFACE, radius=16,
                   alpha=0.55)
        self._stroke(PAD_X, list_y, list_w, list_h, C.WHITE, radius=16,
                     alpha=B_SUBTLE)

        if not self.cards_list:
            self._text("No cards registered yet", PAD_X + list_w / 2.0,
                       list_y + list_h / 2.0, 26, C.TEXT_MUTED,
                       halign="center", valign="middle")
            return

        visible = min(REG_CARDS_ROWS, len(self.cards_list) - self.cards_scroll)
        for i in range(visible):
            idx = self.cards_scroll + i
            card = self.cards_list[idx]
            y = list_y + i * REG_ROW_H
            if i < visible - 1:
                self._rule(PAD_X, y + REG_ROW_H - 1, list_w, alpha=0.06)

            self._text(_fmt_uid(card["uid"]), PAD_X + 30, y + REG_ROW_H / 2.0,
                       22, C.INDIGO_SOFT, "mono", tracking=0.14,
                       valign="middle", max_w=280)
            self._text(card.get("title", "Unknown"), PAD_X + 360,
                       y + REG_ROW_H / 2.0, 24, C.TEXT, valign="middle",
                       max_w=620)
            if card.get("artist"):
                self._text(card["artist"], PAD_X + 1010, y + REG_ROW_H / 2.0,
                           20, C.TEXT_MUTED, valign="middle", max_w=380)

            is_url = str(card.get("url", "")).startswith("http")
            self._text("YT" if is_url else "LOCAL", PAD_X + list_w - 220,
                       y + REG_ROW_H / 2.0, 16,
                       C.CYAN if is_url else C.TEXT_MUTED, "mono",
                       tracking=0.14, halign="right", valign="middle")

            self._draw_button(f"reg.del.{idx}", PAD_X + list_w - 30 - 120,
                              y + (REG_ROW_H - 56) / 2.0, 120, 56, "Delete",
                              kind="danger", size=18)

        if len(self.cards_list) > REG_CARDS_ROWS:
            self._text("SCROLL FOR MORE", CONTENT_R, BODY_BOTTOM - 20, 18,
                       C.TEXT_DIM, "mono", tracking=0.14, halign="right")

    # ── touch / input ────────────────────────────────────────────────────────

    def on_touch_down(self, touch):
        if self._text_input and self._text_input.collide_point(touch.x, touch.y):
            return super().on_touch_down(touch)

        button = getattr(touch, "button", None)
        if button in ("scrollup", "scrolldown"):
            self.handle_scroll(1 if button == "scrollup" else -1)
            return True

        mx, my = self.to_local(touch.x, touch.y)
        name = self._hit_test(mx, my)
        self.dirty = True

        if name is None:
            return super().on_touch_down(touch)

        self._btn_press = name
        if name == "volume":
            self._drag = "volume"
            self._set_volume_from_touch(mx)
            return True

        self._activate(name)
        return True

    def on_touch_move(self, touch):
        if self._drag == "volume":
            mx, _my = self.to_local(touch.x, touch.y)
            self._set_volume_from_touch(mx)
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if self._btn_press or self._drag:
            self._btn_press = None
            self._drag = None
            self.dirty = True
        return super().on_touch_up(touch)

    def _set_volume_from_touch(self, mx):
        rect = self._hits.get("volume")
        if not rect:
            return
        px, _py, pw, _ph = rect
        pct = (mx - px - self.f.u(12)) / max(1.0, pw - self.f.u(24))
        self.volume = int(round(max(0.0, min(1.0, pct)) * 100))
        # player.py owns playback; apply the level only if it grew an API for it.
        setter = getattr(self.player, "set_volume", None)
        if callable(setter):
            try:
                setter(self.volume)
            except Exception:
                pass
        self.dirty = True

    def _activate(self, name):
        # ── player page ──
        if name == "unknown.scrim":
            return
        if name == "unknown.dismiss":
            self._dismiss_unknown()
            return
        if name == "unknown.register":
            self._register_unknown_card()
            return
        if name == "register":
            self._open_register()
            return
        if name == "quit":
            from kivy.app import App
            App.get_running_app().stop()
            return
        if name == "skip":
            self.player.skip()
            return
        if name == "playpause":
            self._toggle_playback()
            return
        if name == "clear_queue":
            self.queue_mgr.clear()
            self._toast("Queue cleared", 1.6)
            return

        # ── registration page ──
        if name == "reg.back":
            self._target_page = "player"
            self._remove_text_input()
            return
        if name == "reg.home":
            self._reset_reg()
            self.reg_state = REG_STATE_HOME
            return
        if name == "reg.cancel":
            self._reset_reg()
            self.reg_state = REG_STATE_HOME
            return
        if name == "reg.new":
            self._reset_reg()
            self.reg_state = REG_STATE_WAITING_SCAN
            return
        if name == "reg.list":
            self._load_cards_list()
            self.reg_state = REG_STATE_LIST
            return
        if name == "reg.confirm":
            self._do_confirm_register()
            return
        if name.startswith("reg.file."):
            self._select_file(int(name.rsplit(".", 1)[1]))
            return
        if name.startswith("reg.field."):
            index = int(name.rsplit(".", 1)[1])
            self._commit_active_field()
            self.reg_state = (REG_STATE_INPUT_TITLE if index == 0
                              else REG_STATE_INPUT_ARTIST)
            self._show_text_input(
                "Enter Song Title:" if index == 0 else "Enter Artist Name:",
                self.reg_title if index == 0 else self.reg_artist,
            )
            return
        if name.startswith("reg.del."):
            index = int(name.rsplit(".", 1)[1])
            if 0 <= index < len(self.cards_list):
                from registry import Registry
                uid = self.cards_list[index]["uid"]
                Registry(self.config.db_path).delete_card(uid)
                self._load_cards_list()
                self._toast(f"Deleted card {uid[:6]}…", 2.0)
            return

    def _toggle_playback(self):
        """player.py has no pause API yet — honour one if it appears."""
        for name in ("toggle_pause", "pause" if self.player.is_playing else "resume"):
            fn = getattr(self.player, name, None)
            if callable(fn):
                fn()
                self.dirty = True
                return

    def _open_register(self):
        self._target_page = "register"
        self.reg_state = REG_STATE_HOME
        self._remove_text_input()
        self.dirty = True

    def _select_file(self, index):
        if not (0 <= index < len(self.local_files)):
            return
        self.file_selected = index
        filename = self.local_files[index]
        self.reg_url = filename
        title = self._get_title_from_file(filename)
        preset = title if title else os.path.splitext(filename)[0]
        self.reg_state = REG_STATE_INPUT_TITLE
        self._show_text_input("Enter Song Title:", preset)

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
                self._reveal_file(self.file_selected)
            elif key == 274 and self.file_selected < len(self.local_files) - 1:
                self.file_selected += 1
                self._reveal_file(self.file_selected)
            elif key in (13, 271) and 0 <= self.file_selected < len(self.local_files):
                self._select_file(self.file_selected)
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

    def _reveal_file(self, index):
        if index < self.file_scroll:
            self.file_scroll = index
        elif index >= self.file_scroll + REG_FILE_ROWS:
            self.file_scroll = index - REG_FILE_ROWS + 1
        self.dirty = True

    # ── text input ───────────────────────────────────────────────────────────

    def _show_text_input(self, label, preset=""):
        self._remove_text_input()
        self._input_label = label
        ti = TextInput(
            text=preset,
            multiline=False,
            size_hint=(None, None),
            background_color=(*C.SURFACE[:3], 0.85),
            background_normal="",
            background_active="",
            foreground_color=C.TEXT,
            cursor_color=C.INDIGO_SOFT,
        )
        self._text_input = ti
        self.add_widget(ti)
        self._place_text_input()
        ti.focus = True
        Clock.schedule_once(lambda dt: ti.select_all(), 0.1)
        self.dirty = True

    def _place_text_input(self):
        ti = self._text_input
        if not ti:
            return
        f = Frame(self.width, self.height)
        index = 1 if self.reg_state == REG_STATE_INPUT_ARTIST else 0
        gap = 32
        field_w = (REG_RIGHT_W - gap) / 2.0
        x = REG_RIGHT_X + index * (field_w + gap)
        px, py, pw, ph = f.rect(x, REG_FIELD_Y + 30, field_w, REG_FIELD_H)
        ti.size = (pw, ph)
        ti.pos = (px, py)
        ti.font_size = f.u(24)
        ti.font_name = _resolve_font(FONT_BODY)
        ti.padding = [f.u(24), (ph - f.u(30)) / 2.0]

    def _get_input_text(self):
        return self._text_input.text if self._text_input else ""

    def _remove_text_input(self):
        if self._text_input:
            self.remove_widget(self._text_input)
            self._text_input = None

    def _commit_active_field(self):
        """Fold whatever is in the live TextInput back into the reg_* fields."""
        if not self._text_input:
            return
        text = self._get_input_text().strip()
        if self.reg_state == REG_STATE_INPUT_TITLE:
            if text:
                self.reg_title = text
        elif self.reg_state == REG_STATE_INPUT_ARTIST:
            self.reg_artist = text

    # ── registration logic ───────────────────────────────────────────────────

    def _do_confirm_register(self):
        self._commit_active_field()
        self._remove_text_input()
        if not (self.scanned_uid and self.reg_title):
            return

        from registry import Registry
        Registry(self.config.db_path).register_card(
            self.scanned_uid, self.reg_title, self.reg_url, self.reg_artist
        )
        self.reg_state = REG_STATE_DONE
        self.dirty = True

    def _reset_reg(self):
        self.scanned_uid = None
        self.existing_card = None
        self.selected_source = None
        self.reg_title = ""
        self.reg_artist = ""
        self.reg_url = ""
        self.file_selected = -1
        self.file_scroll = 0
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

    def _short_folder(self):
        """`…/jukebox/music` — the tail of the path is the useful part."""
        parts = [p for p in str(self.config.music_folder).replace("\\", "/").split("/") if p]
        return "/".join(parts[-2:]) if parts else ""

    def _sidecar_path(self, filename):
        file_path = os.path.join(self.config.music_folder,
                                 str(filename).replace("\\", "/"))
        json_path = file_path + ".info.json"
        if not os.path.exists(json_path):
            base_path = os.path.splitext(file_path)[0]
            json_path = base_path + ".info.json"
            if not os.path.exists(json_path):
                json_path = base_path + ".json"
        return json_path

    def _file_meta_for(self, filename):
        """(display title, duration) from the .info.json sidecar, cached."""
        if filename in self._file_meta:
            return self._file_meta[filename]

        title, duration = None, None
        json_path = self._sidecar_path(filename)
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                raw_title = data.get("title")
                artist = (data.get("artist") or data.get("uploader")
                          or data.get("channel"))
                if raw_title and artist:
                    title = f"{artist} - {raw_title}"
                elif raw_title:
                    title = raw_title
                if data.get("duration"):
                    duration = float(data["duration"])
            except Exception:
                pass

        self._file_meta[filename] = (title, duration)
        return title, duration

    def _get_title_from_file(self, filename):
        return self._file_meta_for(filename)[0]
