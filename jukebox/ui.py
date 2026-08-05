import pygame
import math
import time as _time


# Color palette — rich dark theme with warm accents
class Colors:
    BG_DARK = (12, 12, 18)
    BG_CARD = (22, 22, 32)
    BG_CARD_HOVER = (30, 30, 44)
    SURFACE = (28, 28, 42)
    ACCENT = (138, 92, 246)       # Purple
    ACCENT_GLOW = (168, 122, 255)
    ACCENT_DIM = (80, 50, 150)
    WARM = (255, 165, 80)         # Orange
    SUCCESS = (72, 199, 142)
    ERROR = (255, 95, 95)
    TEXT = (235, 235, 245)
    TEXT_DIM = (140, 140, 165)
    TEXT_MUTED = (80, 80, 105)
    VINYL_DARK = (18, 18, 24)
    VINYL_GROOVE = (35, 35, 50)
    VINYL_LABEL = (138, 92, 246)
    QUEUE_NUM = (100, 80, 180)
    DIVIDER = (40, 40, 60)
    TOAST_BG = (35, 35, 55)


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

        # Fonts (pygame SysFont fallbacks)
        self._init_fonts()

        # Animation state
        self.vinyl_angle = 0.0
        self.toast_message = ""
        self.toast_time = 0
        self.toast_alpha = 0.0
        self.status_message = ""
        self.status_time = 0
        self.pulse_phase = 0.0
        self.idle_bob = 0.0

        # Register player status callback
        self.player.on_status_change = self._on_player_status

        # Pre-render surfaces
        self._vinyl_surface = None
        self._build_vinyl(120)

    def _init_fonts(self):
        preferred = ["Segoe UI", "Inter", "SF Pro Display", "Helvetica Neue", "Roboto"]
        mono_preferred = ["Cascadia Code", "JetBrains Mono", "Consolas", "Courier New"]

        def pick(names, size, bold=False):
            for name in names:
                f = pygame.font.SysFont(name, size, bold=bold)
                if f.get_height() > 0:
                    return f
            return pygame.font.SysFont(None, size, bold=bold)

        self.font_brand = pick(preferred, 28, bold=True)
        self.font_title = pick(preferred, 42, bold=True)
        self.font_subtitle = pick(preferred, 26)
        self.font_body = pick(preferred, 22)
        self.font_small = pick(preferred, 18)
        self.font_tiny = pick(preferred, 14)
        self.font_mono = pick(mono_preferred, 16)
        self.font_icon = pick(preferred, 36, bold=True)
        self.font_queue_num = pick(preferred, 20, bold=True)

    def _build_vinyl(self, radius):
        """Pre-render the vinyl record disc."""
        size = radius * 2 + 4
        self._vinyl_radius = radius
        self._vinyl_surface = pygame.Surface((size, size), pygame.SRCALPHA)
        cx, cy = size // 2, size // 2

        # Outer disc
        pygame.draw.circle(self._vinyl_surface, Colors.VINYL_DARK, (cx, cy), radius)

        # Groove rings
        for r in range(radius - 5, 25, -8):
            alpha = 40 + int(15 * math.sin(r * 0.3))
            groove_color = (*Colors.VINYL_GROOVE[:3], alpha)
            pygame.draw.circle(self._vinyl_surface, groove_color, (cx, cy), r, 1)

        # Label circle (center)
        pygame.draw.circle(self._vinyl_surface, Colors.VINYL_LABEL, (cx, cy), 24)
        pygame.draw.circle(self._vinyl_surface, Colors.VINYL_DARK, (cx, cy), 8)

        # Shiny highlight arc
        highlight = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.arc(
            highlight, (255, 255, 255, 20),
            pygame.Rect(cx - radius + 10, cy - radius + 10, (radius - 10) * 2, (radius - 10) * 2),
            math.radians(200), math.radians(340), 3,
        )
        self._vinyl_surface.blit(highlight, (0, 0))

    def _on_player_status(self, msg):
        self.status_message = msg
        self.status_time = pygame.time.get_ticks() + 5000

    def show_toast(self, message, duration=3.0):
        self.toast_message = message
        self.toast_time = pygame.time.get_ticks() + int(duration * 1000)
        self.toast_alpha = 255.0

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

            self._update(dt)
            self._draw()
            pygame.display.flip()

        pygame.quit()

    def _update(self, dt):
        self.pulse_phase += dt * 2.0
        self.idle_bob += dt * 0.8

        if self.player.is_playing:
            self.vinyl_angle += dt * 45.0  # 45 degrees per second ≈ 7.5 RPM visual
        else:
            # Slow deceleration when stopped
            self.vinyl_angle += dt * max(0, 5.0 - (self.vinyl_angle % 360) * 0.01)

        # Toast fade
        now = pygame.time.get_ticks()
        if now < self.toast_time:
            self.toast_alpha = min(255.0, self.toast_alpha + dt * 800)
        else:
            self.toast_alpha = max(0, self.toast_alpha - dt * 400)

    def _draw(self):
        self.screen.fill(Colors.BG_DARK)

        # Subtle gradient overlay at top
        self._draw_gradient_bar()

        # Header
        self._draw_header()

        # Main content area split: left = now playing + vinyl, right = queue
        left_w = int(self.width * 0.55)
        right_x = left_w + 20

        # Left panel: Now Playing
        self._draw_now_playing(20, 70, left_w - 40)

        # Divider line
        div_x = left_w
        pygame.draw.line(self.screen, Colors.DIVIDER, (div_x, 80), (div_x, self.height - 20), 1)

        # Right panel: Queue
        self._draw_queue(right_x, 70, self.width - right_x - 20)

        # Status bar at bottom
        self._draw_status_bar()

        # Toast overlay
        self._draw_toast()

    def _draw_gradient_bar(self):
        """Subtle accent gradient at the very top."""
        bar_h = 3
        for y in range(bar_h):
            alpha = int(180 * (1 - y / bar_h))
            color = (
                Colors.ACCENT[0],
                Colors.ACCENT[1],
                Colors.ACCENT[2],
            )
            s = pygame.Surface((self.width, 1), pygame.SRCALPHA)
            s.fill((*color, alpha))
            self.screen.blit(s, (0, y))

    def _draw_header(self):
        """Brand header."""
        # uTune logo text
        brand = self.font_brand.render("uTune", True, Colors.ACCENT)
        self.screen.blit(brand, (20, 18))

        # Time display on the right
        import datetime
        now_str = datetime.datetime.now().strftime("%H:%M")
        time_surf = self.font_body.render(now_str, True, Colors.TEXT_DIM)
        self.screen.blit(time_surf, (self.width - time_surf.get_width() - 20, 22))

    def _draw_now_playing(self, x, y, w):
        """Draw the now-playing section with vinyl animation."""
        track = self.player.current_track
        is_playing = self.player.is_playing

        # Section label
        label_color = Colors.ACCENT if is_playing else Colors.TEXT_MUTED
        label = self.font_small.render("NOW PLAYING", True, label_color)
        self.screen.blit(label, (x, y))

        # Vinyl record
        vinyl_cx = x + w // 2
        vinyl_cy = y + 60 + self._vinyl_radius

        if self._vinyl_surface:
            rotated = pygame.transform.rotate(self._vinyl_surface, -self.vinyl_angle)
            rot_rect = rotated.get_rect(center=(vinyl_cx, vinyl_cy))
            self.screen.blit(rotated, rot_rect)

            # Glow ring when playing
            if is_playing:
                glow_alpha = int(30 + 15 * math.sin(self.pulse_phase * 1.5))
                glow_surf = pygame.Surface(
                    (self._vinyl_radius * 2 + 20, self._vinyl_radius * 2 + 20),
                    pygame.SRCALPHA,
                )
                pygame.draw.circle(
                    glow_surf, (*Colors.ACCENT_GLOW, glow_alpha),
                    (self._vinyl_radius + 10, self._vinyl_radius + 10),
                    self._vinyl_radius + 6, 3,
                )
                self.screen.blit(
                    glow_surf,
                    (vinyl_cx - self._vinyl_radius - 10, vinyl_cy - self._vinyl_radius - 10),
                )

        # Track info below vinyl
        info_y = vinyl_cy + self._vinyl_radius + 20

        if track:
            title = track.get("title", "Unknown Track")
            # Truncate long titles
            if len(title) > 35:
                title = title[:32] + "..."
            title_surf = self.font_title.render(title, True, Colors.TEXT)
            title_rect = title_surf.get_rect(centerx=x + w // 2, top=info_y)
            self.screen.blit(title_surf, title_rect)

            # Playing indicator with pulsing dot
            dot_y = info_y + title_surf.get_height() + 12
            dot_alpha = int(180 + 75 * math.sin(self.pulse_phase * 3))
            dot_color = Colors.SUCCESS
            dot_surf = pygame.Surface((10, 10), pygame.SRCALPHA)
            pygame.draw.circle(dot_surf, (*dot_color, dot_alpha), (5, 5), 5)
            dot_x = x + w // 2 - 40
            self.screen.blit(dot_surf, (dot_x, dot_y))

            status_text = "Playing"
            if self.player.play_start_time:
                elapsed = _time.time() - self.player.play_start_time
                mins = int(elapsed) // 60
                secs = int(elapsed) % 60
                status_text = f"Playing  {mins}:{secs:02d}"

            status_surf = self.font_body.render(status_text, True, Colors.SUCCESS)
            self.screen.blit(status_surf, (dot_x + 16, dot_y - 3))

        else:
            # Idle state
            idle_offset = math.sin(self.idle_bob) * 3
            idle_text = "Scan a card to play"
            idle_surf = self.font_subtitle.render(idle_text, True, Colors.TEXT_MUTED)
            idle_rect = idle_surf.get_rect(centerx=x + w // 2, top=info_y + idle_offset)
            self.screen.blit(idle_surf, idle_rect)

            # Show error if there is one
            if self.player.last_error:
                err_y = info_y + 40
                err_text = f"⚠ {self.player.last_error[:60]}"
                err_surf = self.font_small.render(err_text, True, Colors.ERROR)
                err_rect = err_surf.get_rect(centerx=x + w // 2, top=err_y)
                self.screen.blit(err_surf, err_rect)

        # Keyboard shortcuts hint at bottom left
        hint_y = self.height - 55
        hints = [("[S] Skip", Colors.TEXT_MUTED), ("[ESC] Quit", Colors.TEXT_MUTED)]
        hx = x
        for text, color in hints:
            h_surf = self.font_tiny.render(text, True, color)
            self.screen.blit(h_surf, (hx, hint_y))
            hx += h_surf.get_width() + 20

    def _draw_queue(self, x, y, w):
        """Draw the upcoming queue panel."""
        upcoming = self.queue_mgr.get_upcoming()

        # Section label
        count_text = f"UP NEXT ({len(upcoming)})" if upcoming else "UP NEXT"
        label_color = Colors.WARM if upcoming else Colors.TEXT_MUTED
        label = self.font_small.render(count_text, True, label_color)
        self.screen.blit(label, (x, y))

        if not upcoming:
            # Empty state
            empty_y = y + 60
            empty_surf = self.font_body.render("Queue is empty", True, Colors.TEXT_MUTED)
            empty_rect = empty_surf.get_rect(centerx=x + w // 2, top=empty_y)
            self.screen.blit(empty_surf, empty_rect)

            sub_surf = self.font_small.render("Scan cards to add songs", True, Colors.TEXT_MUTED)
            sub_rect = sub_surf.get_rect(centerx=x + w // 2, top=empty_y + 30)
            self.screen.blit(sub_surf, sub_rect)
            return

        # Queue items
        item_h = 64
        max_visible = min(len(upcoming), 7)
        iy = y + 35

        for i in range(max_visible):
            item = upcoming[i]
            item_rect = pygame.Rect(x, iy, w, item_h - 4)

            # Card background
            card_surf = pygame.Surface((item_rect.width, item_rect.height), pygame.SRCALPHA)
            pygame.draw.rect(card_surf, (*Colors.BG_CARD, 180), card_surf.get_rect(), border_radius=8)
            self.screen.blit(card_surf, item_rect.topleft)

            # Number badge
            num_x = x + 14
            num_y = iy + item_h // 2 - 14
            badge_surf = pygame.Surface((28, 28), pygame.SRCALPHA)
            pygame.draw.circle(badge_surf, (*Colors.ACCENT_DIM, 120), (14, 14), 14)
            self.screen.blit(badge_surf, (num_x, num_y))
            num_text = self.font_queue_num.render(str(i + 1), True, Colors.ACCENT_GLOW)
            num_rect = num_text.get_rect(center=(num_x + 14, num_y + 14))
            self.screen.blit(num_text, num_rect)

            # Title
            title = item.get("title", "Unknown")
            if len(title) > 28:
                title = title[:25] + "..."
            title_surf = self.font_body.render(title, True, Colors.TEXT)
            self.screen.blit(title_surf, (num_x + 38, iy + (item_h - title_surf.get_height()) // 2 - 2))

            iy += item_h

        if len(upcoming) > max_visible:
            more = len(upcoming) - max_visible
            more_surf = self.font_small.render(f"+ {more} more...", True, Colors.TEXT_MUTED)
            self.screen.blit(more_surf, (x + 10, iy + 5))

    def _draw_status_bar(self):
        """Bottom status bar with player status messages."""
        bar_y = self.height - 28
        bar_rect = pygame.Rect(0, bar_y, self.width, 28)

        bar_surf = pygame.Surface((self.width, 28), pygame.SRCALPHA)
        bar_surf.fill((*Colors.SURFACE, 200))
        self.screen.blit(bar_surf, (0, bar_y))

        # Status message
        now = pygame.time.get_ticks()
        if self.status_message and now < self.status_time:
            st_surf = self.font_tiny.render(self.status_message, True, Colors.TEXT_DIM)
            self.screen.blit(st_surf, (12, bar_y + 6))

        # Connection indicator on the right
        dot_color = Colors.SUCCESS if self.player.is_playing else Colors.TEXT_MUTED
        pygame.draw.circle(self.screen, dot_color, (self.width - 18, bar_y + 14), 4)
        conn_text = "Active" if self.player.is_playing else "Idle"
        conn_surf = self.font_tiny.render(conn_text, True, Colors.TEXT_DIM)
        self.screen.blit(conn_surf, (self.width - 28 - conn_surf.get_width(), bar_y + 6))

    def _draw_toast(self):
        """Floating toast notification."""
        if self.toast_alpha <= 0:
            return

        alpha = int(min(self.toast_alpha, 255))
        pad_x, pad_y = 24, 12
        text_surf = self.font_body.render(self.toast_message, True, Colors.TEXT)
        tw, th = text_surf.get_size()

        toast_w = tw + pad_x * 2
        toast_h = th + pad_y * 2
        toast_x = (self.width - toast_w) // 2
        toast_y = self.height - 70

        # Background with rounded corners
        bg = pygame.Surface((toast_w, toast_h), pygame.SRCALPHA)
        pygame.draw.rect(bg, (*Colors.TOAST_BG, min(alpha, 220)), bg.get_rect(), border_radius=12)

        # Accent border
        pygame.draw.rect(bg, (*Colors.ACCENT, min(alpha, 100)), bg.get_rect(), width=1, border_radius=12)

        self.screen.blit(bg, (toast_x, toast_y))

        # Text with alpha
        text_with_alpha = text_surf.copy()
        text_with_alpha.set_alpha(alpha)
        self.screen.blit(text_with_alpha, (toast_x + pad_x, toast_y + pad_y))
