import pygame
import math
import time as _time
import datetime


class Colors:
    # Deep space background — reference: #060810
    BG = (6, 8, 16)
    # Radial gradient tints
    BG_INDIGO = (18, 16, 58)        # #12103a
    BG_VIOLET_DEEP = (26, 11, 46)   # #1a0b2e

    # Accent: cyan #22D3EE
    CYAN = (34, 211, 238)
    CYAN_DIM = (34, 211, 238, 180)

    # Accent: violet #8B5CF6
    VIOLET = (139, 92, 246)
    VIOLET_DIM = (109, 40, 217)     # #6D28D9

    # Glass card background
    GLASS_BG = (30, 27, 75)         # rgba(30,27,75,0.45)
    GLASS_BORDER = (139, 92, 246)   # at 0.2 alpha
    GLASS_BORDER_NEXT = (34, 211, 238)

    # Text hierarchy — from reference
    TEXT = (241, 245, 249)           # #F1F5F9
    TEXT_SECONDARY = (148, 163, 184) # #94A3B8
    TEXT_MUTED = (71, 85, 105)       # #475569
    TEXT_SLATE = (226, 232, 240)     # #E2E8F0 (card titles)
    TEXT_DIM_CARD = (100, 116, 139)  # #64748B (card artist)

    # Divider
    DIVIDER = (255, 255, 255)       # at ~6% alpha

    # Progress bar
    PROGRESS_BG = (255, 255, 255)   # at 7% alpha
    PROGRESS_FILL = (34, 211, 238)

    # Status
    LIVE_DOT = (34, 211, 238)
    TOAST_BG = (30, 27, 75)

    # Scanline
    SCANLINE = (0, 0, 0)            # at 12% alpha
    GRID_LINE = (34, 211, 238)      # at 2% alpha


class UI:
    def __init__(self, config, queue_mgr, player, on_scan=None):
        self.config = config
        self.queue_mgr = queue_mgr
        self.player = player
        self.on_scan = on_scan
        self.running = False
        self._key_buffer = ""

        pygame.init()
        self.width, self.height = self.config.ui_resolution

        flags = 0
        if self.config.ui_fullscreen:
            flags |= pygame.FULLSCREEN

        self.screen = pygame.display.set_mode((self.width, self.height), flags)
        pygame.display.set_caption("uTune")

        self.clock = pygame.time.Clock()

        self._init_fonts()

        # Animation state
        self.pulse_phase = 0.0
        self.idle_bob = 0.0
        self.toast_message = ""
        self.toast_time = 0
        self.toast_alpha = 0.0
        self.status_message = ""
        self.status_time = 0
        self.tap_count = 0

        # NFC tap flash
        self.flash_active = False
        self.flash_start = 0
        self.flash_duration = 1400  # ms

        # Card slide-in animation
        self.new_card_time = 0
        self.new_card_duration = 600  # ms

        # Drag and drop state
        self.dragging_idx = None
        self.drag_start_y = 0
        self.drag_offset_y = 0
        self.mouse_x = 0
        self.mouse_y = 0
        self.queue_rects = []

        # Cached album art
        self._cached_art_title = None
        self._cached_art_surface = None
        self._mini_art_cache = {}

        # Register player status callback
        self.player.on_status_change = self._on_player_status

        # Pre-render static textures
        self._scanline_surface = None
        self._grid_surface = None
        self._bg_gradient_surface = None
        self._build_background_textures()

    def _init_fonts(self):
        display = ["Space Grotesk", "Segoe UI", "Inter", "SF Pro Display", "Roboto"]
        mono = ["JetBrains Mono", "Cascadia Code", "Consolas", "Courier New"]

        def pick(names, size, bold=False):
            for name in names:
                f = pygame.font.SysFont(name, size, bold=bold)
                if f.get_height() > 0:
                    return f
            return pygame.font.SysFont(None, size, bold=bold)

        self.font_title = pick(display, 56, bold=True)
        self.font_artist = pick(display, 28)
        self.font_body = pick(display, 24)
        self.font_small = pick(display, 20)
        self.font_label = pick(mono, 16, bold=True)
        self.font_mono = pick(mono, 17)
        self.font_mono_small = pick(mono, 15)
        self.font_card_title = pick(display, 20, bold=True)
        self.font_card_artist = pick(display, 16)
        self.font_album_initial = pick(display, 84, bold=True)
        self.font_album_initial_small = pick(display, 22, bold=True)
        self.font_time = pick(mono, 22)
        self.font_live = pick(mono, 15, bold=True)
        self.font_tap_count = pick(mono, 17)
        self.font_idle = pick(display, 36)
        self.font_idle_sub = pick(display, 22)

    def _build_background_textures(self):
        w, h = self.width, self.height

        # Radial gradient background
        self._bg_gradient_surface = pygame.Surface((w, h), pygame.SRCALPHA)
        # Ellipse at 20% 50% — indigo tint
        for r in range(min(w, h) // 2, 0, -3):
            alpha = max(0, int(35 * (1 - r / (min(w, h) // 2))))
            cx = int(w * 0.2)
            cy = int(h * 0.5)
            s = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
            pygame.draw.ellipse(s, (*Colors.BG_INDIGO, alpha), s.get_rect())
            self._bg_gradient_surface.blit(s, (cx - r, cy - r))

        # Smaller violet blob at 5% 55%
        for r in range(min(w, h) // 3, 0, -4):
            alpha = max(0, int(25 * (1 - r / (min(w, h) // 3))))
            cx = int(w * 0.05)
            cy = int(h * 0.55)
            s = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
            pygame.draw.ellipse(s, (*Colors.BG_VIOLET_DEEP, alpha), s.get_rect())
            self._bg_gradient_surface.blit(s, (cx - r, cy - r))

        # Scanline texture — horizontal lines every 4px
        self._scanline_surface = pygame.Surface((w, h), pygame.SRCALPHA)
        for y in range(0, h, 4):
            pygame.draw.line(
                self._scanline_surface,
                (0, 0, 0, 30),
                (0, y + 3), (w, y + 3), 1,
            )

        # Subtle grid — 60px spacing, very faint cyan
        self._grid_surface = pygame.Surface((w, h), pygame.SRCALPHA)
        grid_color = (34, 211, 238, 5)
        for x in range(0, w, 60):
            pygame.draw.line(self._grid_surface, grid_color, (x, 0), (x, h), 1)
        for y in range(0, h, 60):
            pygame.draw.line(self._grid_surface, grid_color, (0, y), (w, y), 1)

    def _on_player_status(self, msg):
        self.status_message = msg
        self.status_time = pygame.time.get_ticks() + 5000

    def show_toast(self, message, duration=3.0):
        self.toast_message = message
        self.toast_time = pygame.time.get_ticks() + int(duration * 1000)
        self.toast_alpha = 255.0
        # Trigger tap flash + count
        self.flash_active = True
        self.flash_start = pygame.time.get_ticks()
        self.tap_count += 1
        # Trigger card slide-in
        self.new_card_time = pygame.time.get_ticks()

    def run(self):
        self.running = True
        while self.running:
            dt = self.clock.tick(self.config.ui_fps) / 1000.0

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        self.running = False
                    elif event.key == pygame.K_s:
                        self.player.skip()
                    elif event.unicode.isdigit():
                        self._key_buffer += event.unicode
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        if self._key_buffer and self.on_scan:
                            self.on_scan(self._key_buffer)
                        self._key_buffer = ""
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button == 1:
                        # Check queue rects
                        for i, rect in self.queue_rects:
                            if rect.collidepoint(event.pos):
                                self.dragging_idx = i
                                self.drag_offset_y = event.pos[1] - rect.y
                                self.drag_start_y = rect.y
                                break
                elif event.type == pygame.MOUSEBUTTONUP:
                    if event.button == 1 and self.dragging_idx is not None:
                        upcoming = self.queue_mgr.get_upcoming()
                        max_visible = min(len(upcoming), 5)
                        
                        drop_idx = self.dragging_idx
                        for i, rect in self.queue_rects:
                            if event.pos[1] < rect.centery:
                                drop_idx = i
                                break
                        else:
                            # Dropped below all visible rects
                            drop_idx = max_visible

                        if drop_idx != self.dragging_idx:
                            target_idx = drop_idx
                            if drop_idx > self.dragging_idx:
                                target_idx -= 1
                            
                            # Bound target_idx
                            target_idx = max(0, min(target_idx, len(upcoming) - 1))
                            self.queue_mgr.reorder(self.dragging_idx, target_idx)
                            
                        self.dragging_idx = None
                elif event.type == pygame.MOUSEMOTION:
                    self.mouse_x, self.mouse_y = event.pos

            self._update(dt)
            self._draw()
            pygame.display.flip()

        pygame.quit()

    def _update(self, dt):
        self.pulse_phase += dt * 2.0
        self.idle_bob += dt * 0.8

        # Flash timeout
        now = pygame.time.get_ticks()
        if self.flash_active and now - self.flash_start > self.flash_duration:
            self.flash_active = False

        # Toast fade
        if now < self.toast_time:
            self.toast_alpha = min(255.0, self.toast_alpha + dt * 800)
        else:
            self.toast_alpha = max(0, self.toast_alpha - dt * 400)

    def _draw(self):
        # 1. Base background
        self.screen.fill(Colors.BG)

        # 2. Background effects (gradients, scanlines, grid)
        self._draw_background_effects()

        # 3. Tap flash overlay
        self._draw_tap_flash()

        # Layout constants — reference: grid 1fr 320px, padding 28px
        pad = 28
        right_panel_w = 320
        divider_x = self.width - right_panel_w - pad
        left_w = divider_x - pad

        # 4. Left panel: Now Playing
        self._draw_now_playing(pad, pad, left_w)

        # 5. Vertical divider
        div_surf = pygame.Surface((1, self.height - pad * 2 - 40), pygame.SRCALPHA)
        div_surf.fill((255, 255, 255, 15))
        self.screen.blit(div_surf, (divider_x, pad))

        # 6. Right panel: Queue
        self._draw_queue(divider_x + 24, pad, right_panel_w - 24)

        # 7. Bottom bar (full width)
        self._draw_bottom_bar()

        # 8. Toast overlay
        self._draw_toast()

    def _draw_background_effects(self):
        if self._bg_gradient_surface:
            self.screen.blit(self._bg_gradient_surface, (0, 0))
        if self._scanline_surface:
            self.screen.blit(self._scanline_surface, (0, 0))
        if self._grid_surface:
            self.screen.blit(self._grid_surface, (0, 0))

    def _draw_tap_flash(self):
        if not self.flash_active:
            return

        now = pygame.time.get_ticks()
        elapsed_ms = now - self.flash_start
        progress = min(1.0, elapsed_ms / self.flash_duration)

        # Fade: peak at 15%, hold to 60%, fade out
        if progress < 0.15:
            alpha = progress / 0.15
        elif progress < 0.60:
            alpha = 1.0 - (progress - 0.15) / 0.45 * 0.6
        else:
            alpha = 0.4 * (1.0 - (progress - 0.60) / 0.40)

        alpha = max(0, min(1.0, alpha))
        if alpha < 0.01:
            return

        # Single semi-transparent overlay with tinted bottom half
        flash = pygame.Surface((self.width, self.height), pygame.SRCALPHA)

        # Cyan tint at bottom
        bottom_h = self.height // 2
        for row in range(bottom_h):
            t = row / bottom_h
            a = int(alpha * 40 * t)
            if a > 0:
                pygame.draw.line(
                    flash, (34, 211, 238, a),
                    (0, self.height - bottom_h + row),
                    (self.width, self.height - bottom_h + row),
                )

        # Violet tint at bottom edge
        if progress > 0.08:
            edge_h = self.height // 4
            for row in range(edge_h):
                t = row / edge_h
                a = int(alpha * 0.7 * 30 * t)
                if a > 0:
                    pygame.draw.line(
                        flash, (139, 92, 246, a),
                        (0, self.height - edge_h + row),
                        (self.width, self.height - edge_h + row),
                    )

        self.screen.blit(flash, (0, 0))

    def _draw_now_playing(self, x, y, w):
        track = self.player.current_track
        is_playing = self.player.is_playing

        # "NOW PLAYING" label — monospace, cyan, with dot
        label_y = y
        dot_surf = pygame.Surface((6, 6), pygame.SRCALPHA)
        pygame.draw.circle(dot_surf, (*Colors.CYAN, 190), (3, 3), 3)
        self.screen.blit(dot_surf, (x, label_y + 3))

        label = self.font_label.render("NOW PLAYING", True, Colors.CYAN)
        label.set_alpha(190)
        self.screen.blit(label, (x + 14, label_y))

        if track:
            self._draw_playing_state(x, y, w, track)
        else:
            self._draw_idle_state(x, y, w)

    def _draw_playing_state(self, x, y, w, track):
        # Album art + info layout — reference: flex row, gap 32
        art_y = y + 30
        art_size = 200
        art_x = x

        # Draw album art placeholder (gradient square with track initial)
        self._draw_album_art(art_x, art_y, art_size, track)

        # Track info — to the right of album art
        info_x = art_x + art_size + 32
        info_y = art_y + 6

        # Title — large bold
        title = track.get("title", "Unknown Track")
        if len(title) > 24:
            title = title[:21] + "..."
        title_surf = self.font_title.render(title, True, Colors.TEXT)
        self.screen.blit(title_surf, (info_x, info_y))

        # Artist (use UID or generic)
        artist_y = info_y + title_surf.get_height() + 10
        artist_text = track.get("artist", "Unknown Artist")
        artist_surf = self.font_artist.render(artist_text, True, Colors.TEXT_SECONDARY)
        self.screen.blit(artist_surf, (info_x, artist_y))

        # Album label — tiny mono uppercase
        album_y = artist_y + artist_surf.get_height() + 6
        album_text = track.get("album", "")
        if album_text:
            album_surf = self.font_mono_small.render(album_text.upper(), True, Colors.TEXT_MUTED)
            self.screen.blit(album_surf, (info_x, album_y))
            prog_y = album_y + album_surf.get_height() + 24
        else:
            prog_y = artist_y + artist_surf.get_height() + 24

        # Progress bar under the text
        prog_w = w - (info_x - x) - 20
        self._draw_progress_bar(info_x, prog_y, prog_w)

    def _build_album_art(self, size, track):
        """Pre-render album art surface. Called only when track changes."""
        image_bytes = track.get("image_bytes")
        if image_bytes:
            try:
                import io
                image = pygame.image.load(io.BytesIO(image_bytes)).convert_alpha()
                # Crop to square before scaling
                w, h = image.get_size()
                min_dim = min(w, h)
                crop_rect = pygame.Rect((w - min_dim) // 2, (h - min_dim) // 2, min_dim, min_dim)
                image = image.subsurface(crop_rect)
                image = pygame.transform.smoothscale(image, (size, size))
                
                # Create rounded mask and apply
                mask = pygame.Surface((size, size), pygame.SRCALPHA)
                pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=12)
                
                result = pygame.Surface((size, size), pygame.SRCALPHA)
                result.blit(image, (0, 0))
                result.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
                return result
            except Exception as e:
                print("Failed to load album art:", e)

        # Fallback: Draw gradient onto opaque surface first
        gradient = pygame.Surface((size, size))
        for row in range(size):
            t = row / size
            r = int(Colors.BG_INDIGO[0] * (1 - t) + Colors.VIOLET_DIM[0] * t * 0.3)
            g = int(Colors.BG_INDIGO[1] * (1 - t) + Colors.VIOLET_DIM[1] * t * 0.3)
            b = int(Colors.BG_INDIGO[2] * (1 - t) + Colors.VIOLET_DIM[2] * t * 0.5 + 40)
            pygame.draw.line(gradient, (r, g, min(b, 255)), (0, row), (size, row))

        # Track initial letter centered
        initial = (track.get("title", "?"))[0].upper()
        initial_surf = self.font_album_initial.render(initial, True, (255, 255, 255))
        initial_surf.set_alpha(50)
        ir = initial_surf.get_rect(center=(size // 2, size // 2))
        gradient.blit(initial_surf, ir)

        # Create rounded mask and apply
        mask = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=12)

        # Composite: blit gradient, then multiply alpha with mask
        result = pygame.Surface((size, size), pygame.SRCALPHA)
        result.blit(gradient, (0, 0))
        result.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        return result

    def _draw_album_art(self, x, y, size, track):
        # Cache the album art surface — rebuild only when track title or image changes
        cache_key = f"{track.get('title', '?')}_{'img' if track.get('image_bytes') else 'no_img'}"
        if self._cached_art_title != cache_key:
            self._cached_art_title = cache_key
            self._cached_art_surface = self._build_album_art(size, track)

        if self._cached_art_surface:
            self.screen.blit(self._cached_art_surface, (x, y))

        # Cyan border
        pygame.draw.rect(
            self.screen,
            (*Colors.CYAN[:3], 76),
            pygame.Rect(x, y, size, size),
            width=1, border_radius=12,
        )

        # Subtle glow when playing
        if self.player.is_playing:
            glow_alpha = int(15 + 10 * math.sin(self.pulse_phase * 1.5))
            pygame.draw.rect(
                self.screen,
                (*Colors.CYAN[:3], glow_alpha),
                pygame.Rect(x - 2, y - 2, size + 4, size + 4),
                width=2, border_radius=14,
            )

    def _draw_progress_bar(self, x, y, w):
        track = self.player.current_track
        if not track:
            return
            
        if not self.player.play_start_time:
            # Show a loading/buffering indicator instead
            bar_h = 2
            bg_surf = pygame.Surface((w, bar_h), pygame.SRCALPHA)
            bg_surf.fill((255, 255, 255, 18))
            self.screen.blit(bg_surf, (x, y))
            
            # Draw a pulsing small bar
            pulse_w = 40
            pulse_x = int((w - pulse_w) * ((math.sin(self.pulse_phase * 3) + 1) / 2))
            pygame.draw.rect(
                self.screen, Colors.PROGRESS_FILL,
                pygame.Rect(x + pulse_x, y, pulse_w, bar_h),
                border_radius=1,
            )
            
            time_y = y + 8
            el_surf = self.font_time.render("Loading...", True, Colors.CYAN)
            el_surf.set_alpha(180)
            self.screen.blit(el_surf, (x, time_y))
            return

        elapsed = _time.time() - self.player.play_start_time
        
        duration = track.get("duration")
        if duration:
            duration_val = float(duration)
            pct = min(1.0, elapsed / duration_val)
        else:
            duration_val = elapsed
            pct = 1.0

        # Track bar background
        bar_h = 2
        bg_surf = pygame.Surface((w, bar_h), pygame.SRCALPHA)
        bg_surf.fill((255, 255, 255, 18))
        self.screen.blit(bg_surf, (x, y))

        # Fill
        fill_w = int(w * pct)
        if fill_w > 0:
            pygame.draw.rect(
                self.screen, Colors.PROGRESS_FILL,
                pygame.Rect(x, y, fill_w, bar_h),
                border_radius=1,
            )

        # Time labels
        mins = int(elapsed) // 60
        secs = int(elapsed) % 60
        elapsed_str = f"{mins}:{secs:02d}"

        time_y = y + 8
        el_surf = self.font_time.render(elapsed_str, True, Colors.CYAN)
        el_surf.set_alpha(180)
        self.screen.blit(el_surf, (x, time_y))

        if duration:
            dur_mins = int(duration_val) // 60
            dur_secs = int(duration_val) % 60
            dur_str = f"{dur_mins}:{dur_secs:02d}"
            dur_surf = self.font_time.render(dur_str, True, Colors.TEXT_MUTED)
            self.screen.blit(dur_surf, (x + w - dur_surf.get_width(), time_y))

    def _draw_idle_state(self, x, y, w):
        # Centered idle prompt — NFC-style
        center_x = x + w // 2
        center_y = y + self.height // 2 - 80

        # NFC card icon (simplified SVG → Pygame rectangles)
        self._draw_nfc_icon(center_x - 28, center_y - 50)

        # "Tap a card to play" text with bob animation
        bob_offset = math.sin(self.idle_bob) * 3
        idle_surf = self.font_idle.render("Tap a card to play", True, Colors.TEXT_SECONDARY)
        ir = idle_surf.get_rect(centerx=center_x, top=center_y + 30 + bob_offset)
        self.screen.blit(idle_surf, ir)

        # Sub-text
        sub_surf = self.font_idle_sub.render(
            "Place an RFID card on the reader", True, Colors.TEXT_MUTED,
        )
        sr = sub_surf.get_rect(centerx=center_x, top=ir.bottom + 12)
        self.screen.blit(sub_surf, sr)

        # Error display
        if self.player.last_error:
            err_text = self.player.last_error[:60]
            err_surf = self.font_small.render(err_text, True, (255, 95, 95))
            er = err_surf.get_rect(centerx=center_x, top=sr.bottom + 20)
            self.screen.blit(err_surf, er)

    def _draw_nfc_icon(self, x, y):
        # Simplified NFC card icon matching the reference SVG
        # Card body
        card_rect = pygame.Rect(x, y, 48, 56)
        pygame.draw.rect(
            self.screen, (*Colors.CYAN, 40),
            card_rect, border_radius=6,
        )
        pygame.draw.rect(
            self.screen, (*Colors.CYAN, 140),
            card_rect, width=2, border_radius=6,
        )

        # Chip rectangle (top-left of card)
        chip_rect = pygame.Rect(x + 8, y + 8, 16, 10)
        pygame.draw.rect(
            self.screen, (*Colors.CYAN, 100),
            chip_rect, width=1, border_radius=2,
        )

        # Signal arcs (right side)
        arc_x = x + 36
        arc_y = y + 28
        for i, (radius, alpha) in enumerate([(6, 200), (12, 130), (18, 70)]):
            arc_surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
            pygame.draw.arc(
                arc_surf, (*Colors.CYAN, alpha),
                pygame.Rect(0, 0, radius * 2, radius * 2),
                math.radians(-45), math.radians(45), 2,
            )
            self.screen.blit(arc_surf, (arc_x - radius, arc_y - radius))

    def _draw_queue(self, x, y, w):
        upcoming = self.queue_mgr.get_upcoming()

        # "UP NEXT" label — monospace, violet
        label = self.font_label.render("UP NEXT", True, Colors.VIOLET)
        label.set_alpha(190)
        self.screen.blit(label, (x, y))

        if not upcoming:
            # Empty state
            empty_y = y + 50
            empty_surf = self.font_body.render("Queue is empty", True, Colors.TEXT_MUTED)
            er = empty_surf.get_rect(centerx=x + w // 2, top=empty_y)
            self.screen.blit(empty_surf, er)

            sub_surf = self.font_small.render(
                "Scan cards to add songs", True, Colors.TEXT_MUTED,
            )
            sr = sub_surf.get_rect(centerx=x + w // 2, top=empty_y + 30)
            self.screen.blit(sub_surf, sr)
            return

        # Queue cards
        card_h_next = 58
        card_h_normal = 48
        gap = 8
        max_visible = min(len(upcoming), 5)
        iy = y + 24

        now = pygame.time.get_ticks()
        self.queue_rects = []

        # Helper to draw a single card
        def draw_card(item_index, item_data, cx, cy, cw, ch, is_next, is_new, alpha):
            card_surf = pygame.Surface((cw, ch), pygame.SRCALPHA)
            pygame.draw.rect(card_surf, (*Colors.GLASS_BG, 115), card_surf.get_rect(), border_radius=10)
            
            border_color = Colors.GLASS_BORDER_NEXT if is_next else Colors.GLASS_BORDER
            border_alpha = 96 if is_next else 50
            pygame.draw.rect(card_surf, (*border_color, border_alpha), card_surf.get_rect(), width=1, border_radius=10)

            if is_next:
                highlight = pygame.Surface((cw, 1), pygame.SRCALPHA)
                for hx in range(cw):
                    t = hx / cw
                    a = int(128 * math.sin(t * math.pi))
                    highlight.set_at((hx, 0), (*Colors.CYAN, a))
                card_surf.blit(highlight, (0, 0))

            card_surf.set_alpha(alpha)
            self.screen.blit(card_surf, (cx, cy))

            thumb_size = 48 if is_next else 38
            thumb_x = cx + 12
            thumb_y = cy + (ch - thumb_size) // 2
            self._draw_mini_album(thumb_x, thumb_y, thumb_size, item_data, item_index)

            title = item_data.get("title", "Unknown")
            if len(title) > 22:
                title = title[:19] + "..."
            title_surf = self.font_card_title.render(title, True, Colors.TEXT_SLATE)
            title_surf.set_alpha(alpha)
            text_x = thumb_x + thumb_size + 10
            text_y = cy + (ch // 2) - title_surf.get_height() + 2
            self.screen.blit(title_surf, (text_x, text_y))

            artist = item_data.get("artist", "")
            if artist:
                artist_surf = self.font_card_artist.render(artist, True, Colors.TEXT_DIM_CARD)
                artist_surf.set_alpha(alpha)
                self.screen.blit(artist_surf, (text_x, text_y + title_surf.get_height() + 2))

        for i in range(max_visible):
            item = upcoming[i]
            is_next = (i == 0)
            is_new = (i == 0 and now - self.new_card_time < self.new_card_duration)
            card_h = card_h_next if is_next else card_h_normal

            slide_offset = 0
            card_alpha = 255
            if is_new and self.dragging_idx is None:
                progress = (now - self.new_card_time) / self.new_card_duration
                if progress < 0.6:
                    slide_offset = int(60 * (1 - progress / 0.6))
                    card_alpha = int(255 * (progress / 0.6))
                elif progress < 1.0:
                    slide_offset = int(-4 * (1 - (progress - 0.6) / 0.4))

            card_x = x + slide_offset
            card_w = w - abs(slide_offset)
            card_rect = pygame.Rect(card_x, iy, card_w, card_h)
            self.queue_rects.append((i, card_rect))

            if i == self.dragging_idx:
                # Draw placeholder gap
                placeholder = pygame.Surface((card_w, card_h), pygame.SRCALPHA)
                pygame.draw.rect(placeholder, (255, 255, 255, 10), placeholder.get_rect(), border_radius=10)
                pygame.draw.rect(placeholder, (255, 255, 255, 30), placeholder.get_rect(), width=1, border_radius=10)
                self.screen.blit(placeholder, (card_x, iy))
            else:
                draw_card(i, item, card_x, iy, card_w, card_h, is_next, is_new, card_alpha)

            iy += card_h + gap

        # Draw the dragged item on top
        if self.dragging_idx is not None and self.dragging_idx < len(upcoming):
            item = upcoming[self.dragging_idx]
            is_next = (self.dragging_idx == 0)
            card_h = card_h_next if is_next else card_h_normal
            drag_y = self.mouse_y - self.drag_offset_y
            
            # Add a slight drop shadow for the dragged item
            shadow = pygame.Surface((w, card_h), pygame.SRCALPHA)
            pygame.draw.rect(shadow, (0, 0, 0, 100), shadow.get_rect(), border_radius=10)
            self.screen.blit(shadow, (x + 4, drag_y + 4))
            
            draw_card(self.dragging_idx, item, x, drag_y, w, card_h, is_next, False, 255)

        if len(upcoming) > max_visible:
            more = len(upcoming) - max_visible
            more_surf = self.font_small.render(
                f"+ {more} more...", True, Colors.TEXT_MUTED,
            )
            self.screen.blit(more_surf, (x + 10, iy + 5))

    def _draw_mini_album(self, x, y, size, track, index):
        uid = track.get("uid", f"idx_{index}")
        has_img = "img" if track.get("image_bytes") else "no"
        cache_key = f"{uid}_{size}_{has_img}"
        
        colors = [
            (34, 211, 238),   # cyan
            (139, 92, 246),   # violet
            (34, 211, 238),
            (139, 92, 246),
            (34, 211, 238),
        ]
        color = colors[index % len(colors)]

        if cache_key not in self._mini_art_cache:
            thumb = pygame.Surface((size, size), pygame.SRCALPHA)
            image_bytes = track.get("image_bytes")
            success = False
            
            if image_bytes:
                try:
                    import io
                    image = pygame.image.load(io.BytesIO(image_bytes)).convert_alpha()
                    w_img, h_img = image.get_size()
                    min_dim = min(w_img, h_img)
                    crop_rect = pygame.Rect((w_img - min_dim) // 2, (h_img - min_dim) // 2, min_dim, min_dim)
                    image = image.subsurface(crop_rect)
                    image = pygame.transform.smoothscale(image, (size, size))
                    
                    mask = pygame.Surface((size, size), pygame.SRCALPHA)
                    pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=6)
                    
                    thumb.blit(image, (0, 0))
                    thumb.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
                    success = True
                except Exception as e:
                    print("Failed to load mini album art:", e)
            
            if not success:
                # Fallback gradient
                for row in range(size):
                    t = row / size
                    r = int(color[0] * 0.15 * (1 - t) + Colors.BG_INDIGO[0] * t * 0.8)
                    g = int(color[1] * 0.15 * (1 - t) + Colors.BG_INDIGO[1] * t * 0.8)
                    b = int(color[2] * 0.4 * (1 - t) + Colors.BG_INDIGO[2] * t + 20)
                    pygame.draw.line(thumb, (r, g, min(255, b), 200), (0, row), (size, row))
                # rounded clip
                mask2 = pygame.Surface((size, size), pygame.SRCALPHA)
                pygame.draw.rect(mask2, (255, 255, 255, 255), mask2.get_rect(), border_radius=6)
                
                final = pygame.Surface((size, size), pygame.SRCALPHA)
                final.blit(thumb, (0, 0))
                final.blit(mask2, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
                thumb = final
                
            self._mini_art_cache[cache_key] = thumb

        self.screen.blit(self._mini_art_cache[cache_key], (x, y))

        # Border
        border_color = (34, 211, 238) if track.get("image_bytes") else color
        pygame.draw.rect(
            self.screen, (*border_color, 76),
            pygame.Rect(x, y, size, size),
            width=1, border_radius=6,
        )

    def _draw_bottom_bar(self):
        bar_y = self.height - 48
        bar_h = 48

        # Top border line
        border_surf = pygame.Surface((self.width, 1), pygame.SRCALPHA)
        border_surf.fill((255, 255, 255, 15))
        self.screen.blit(border_surf, (0, bar_y))

        content_y = bar_y + (bar_h - 16) // 2

        # Left side: keyboard shortcuts
        hints = [("[S] Skip", Colors.TEXT_MUTED), ("[ESC] Quit", Colors.TEXT_MUTED)]
        hx = 28
        for text, color in hints:
            h_surf = self.font_mono_small.render(text, True, color)
            self.screen.blit(h_surf, (hx, content_y))
            hx += h_surf.get_width() + 16

        # Center divider line
        div_start = hx + 16
        div_end = self.width - 280
        if div_end > div_start:
            div_surf = pygame.Surface((div_end - div_start, 1), pygame.SRCALPHA)
            div_surf.fill((255, 255, 255, 10))
            self.screen.blit(div_surf, (div_start, bar_y + bar_h // 2))

        # Right side: tap count + LIVE badge
        right_x = self.width - 28

        # LIVE badge
        live_w = 56
        live_h = 22
        live_x = right_x - live_w
        live_y = content_y - 2

        live_bg = pygame.Surface((live_w, live_h), pygame.SRCALPHA)
        pygame.draw.rect(live_bg, (34, 211, 238, 10), live_bg.get_rect(), border_radius=11)
        pygame.draw.rect(live_bg, (34, 211, 238, 50), live_bg.get_rect(), width=1, border_radius=11)
        self.screen.blit(live_bg, (live_x, live_y))

        # Cyan dot inside LIVE badge
        dot_x = live_x + 10
        dot_y = live_y + live_h // 2
        dot_alpha = int(200 + 55 * math.sin(self.pulse_phase * 3))
        dot_surf = pygame.Surface((10, 10), pygame.SRCALPHA)
        pygame.draw.circle(dot_surf, (*Colors.CYAN, min(255, dot_alpha)), (5, 5), 3)
        self.screen.blit(dot_surf, (dot_x - 5, dot_y - 5))

        # "LIVE" text
        live_text = self.font_live.render("LIVE", True, Colors.CYAN)
        live_text.set_alpha(180)
        self.screen.blit(live_text, (dot_x + 8, live_y + 4))

        # Tap count — render number in brighter color, rest in muted
        num_str = str(self.tap_count)
        suffix = " cards tapped"

        num_surf = self.font_tap_count.render(num_str, True, Colors.TEXT_SECONDARY)
        suffix_surf = self.font_tap_count.render(suffix, True, Colors.TEXT_MUTED)
        total_w = num_surf.get_width() + suffix_surf.get_width()
        count_x = live_x - total_w - 20
        self.screen.blit(num_surf, (count_x, content_y))
        self.screen.blit(suffix_surf, (count_x + num_surf.get_width(), content_y))

    def _draw_toast(self):
        if self.toast_alpha <= 0:
            return

        alpha = int(min(self.toast_alpha, 255))
        pad_x, pad_y = 24, 12
        text_surf = self.font_body.render(self.toast_message, True, Colors.TEXT)
        tw, th = text_surf.get_size()

        toast_w = tw + pad_x * 2
        toast_h = th + pad_y * 2
        toast_x = (self.width - toast_w) // 2
        toast_y = self.height - 100

        # Glass background
        bg = pygame.Surface((toast_w, toast_h), pygame.SRCALPHA)
        pygame.draw.rect(
            bg, (*Colors.TOAST_BG, min(alpha, 200)),
            bg.get_rect(), border_radius=12,
        )

        # Cyan accent border
        pygame.draw.rect(
            bg, (*Colors.CYAN, min(alpha, 80)),
            bg.get_rect(), width=1, border_radius=12,
        )

        self.screen.blit(bg, (toast_x, toast_y))

        text_with_alpha = text_surf.copy()
        text_with_alpha.set_alpha(alpha)
        self.screen.blit(text_with_alpha, (toast_x + pad_x, toast_y + pad_y))
