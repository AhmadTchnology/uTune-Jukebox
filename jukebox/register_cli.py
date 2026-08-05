import os
import sys
import time
import threading
import math

# Ensure the working directory is the script directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import pygame

from config import config
from registry import Registry
from rfid_reader import RFIDReader
from player import AUDIO_EXTENSIONS


class Colors:
    BG = (6, 8, 16)
    BG_INDIGO = (18, 16, 58)
    BG_VIOLET_DEEP = (26, 11, 46)
    CYAN = (34, 211, 238)
    VIOLET = (139, 92, 246)
    VIOLET_DIM = (109, 40, 217)
    GLASS_BG = (30, 27, 75)
    TEXT = (241, 245, 249)
    TEXT_SECONDARY = (148, 163, 184)
    TEXT_MUTED = (71, 85, 105)
    TEXT_SLATE = (226, 232, 240)
    GREEN = (74, 222, 128)
    RED = (248, 113, 113)
    ORANGE = (251, 191, 36)


class RegisterUI:
    # Screen states
    STATE_HOME = "home"
    STATE_WAITING_SCAN = "waiting_scan"
    STATE_PICK_SOURCE = "pick_source"
    STATE_INPUT_URL = "input_url"
    STATE_DOWNLOADING = "downloading"
    STATE_PICK_FILE = "pick_file"
    STATE_INPUT_TITLE = "input_title"
    STATE_CONFIRM = "confirm"
    STATE_DONE = "done"
    STATE_LIST = "list"

    def __init__(self):
        pygame.init()
        self.width, self.height = config.ui_resolution
        flags = pygame.FULLSCREEN if config.ui_fullscreen else 0
        self.screen = pygame.display.set_mode((self.width, self.height), flags)
        pygame.display.set_caption("uTune — Card Registration")
        self.clock = pygame.time.Clock()

        self.registry = Registry(config.db_path)

        self._init_fonts()
        self._build_bg()
        pygame.key.set_repeat(400, 50)
        try:
            pygame.scrap.init()
        except Exception:
            pass

        # State
        self.state = self.STATE_HOME
        self.running = True
        self.pulse = 0.0
        self._scan_buffer = ""

        # Registration data
        self.scanned_uid = None
        self.existing_card = None
        self.selected_source = None  # "youtube" or "local"
        self.input_text = ""
        self.input_cursor_visible = True
        self.input_cursor_timer = 0.0
        self.reg_title = ""
        self.reg_url = ""
        self.reg_url = ""
        self.toast_msg = ""
        self.toast_time = 0
        self.download_progress = ""

        # File picker
        self.local_files = []
        self.file_scroll = 0
        self.file_selected = -1

        # Registered cards list
        self.cards_list = []
        self.cards_scroll = 0

        # RFID reader
        self._uid_buffer = None
        self.reader = RFIDReader(callback=self._on_scan)
        self.reader.start()

    def _on_scan(self, uid):
        self._uid_buffer = uid

    def _init_fonts(self):
        display = ["Space Grotesk", "Segoe UI", "Inter", "SF Pro Display", "Roboto"]
        mono = ["JetBrains Mono", "Cascadia Code", "Consolas", "Courier New"]

        def pick(names, size, bold=False):
            for name in names:
                f = pygame.font.SysFont(name, size, bold=bold)
                if f.get_height() > 0:
                    return f
            return pygame.font.SysFont(None, size, bold=bold)

        self.font_title = pick(display, 42, bold=True)
        self.font_heading = pick(display, 30, bold=True)
        self.font_body = pick(display, 22)
        self.font_small = pick(display, 18)
        self.font_label = pick(mono, 16, bold=True)
        self.font_mono = pick(mono, 18)
        self.font_input = pick(mono, 24)
        self.font_btn = pick(display, 20, bold=True)
        self.font_file = pick(display, 19)

    def _build_bg(self):
        w, h = self.width, self.height
        self._bg = pygame.Surface((w, h), pygame.SRCALPHA)

        for r in range(min(w, h) // 2, 0, -3):
            alpha = max(0, int(35 * (1 - r / (min(w, h) // 2))))
            cx, cy = int(w * 0.2), int(h * 0.5)
            s = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
            pygame.draw.ellipse(s, (*Colors.BG_INDIGO, alpha), s.get_rect())
            self._bg.blit(s, (cx - r, cy - r))

        for r in range(min(w, h) // 3, 0, -4):
            alpha = max(0, int(25 * (1 - r / (min(w, h) // 3))))
            cx, cy = int(w * 0.85), int(h * 0.35)
            s = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
            pygame.draw.ellipse(s, (*Colors.BG_VIOLET_DEEP, alpha), s.get_rect())
            self._bg.blit(s, (cx - r, cy - r))

        # Scanlines
        self._scanlines = pygame.Surface((w, h), pygame.SRCALPHA)
        for y in range(0, h, 4):
            pygame.draw.line(self._scanlines, (0, 0, 0, 30), (0, y + 3), (w, y + 3), 1)

    def _scan_local_files(self):
        folder = config.music_folder
        if not os.path.isdir(folder):
            self.local_files = []
            return
        files = []
        for f in sorted(os.listdir(folder)):
            ext = os.path.splitext(f)[1].lower()
            if ext in AUDIO_EXTENSIONS:
                files.append(f)
        self.local_files = files
        self.file_scroll = 0
        self.file_selected = -1

    def _load_cards_list(self):
        import sqlite3
        try:
            with sqlite3.connect(self.registry.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT uid, title, youtube_url FROM cards ORDER BY date_added DESC')
                self.cards_list = [
                    {'uid': r[0], 'title': r[1], 'url': r[2]} for r in cursor.fetchall()
                ]
        except Exception:
            self.cards_list = []
        self.cards_scroll = 0

    def run(self):
        while self.running:
            dt = self.clock.tick(config.ui_fps) / 1000.0
            self.pulse += dt * 2.0
            self.input_cursor_timer += dt
            if self.input_cursor_timer > 0.5:
                self.input_cursor_timer = 0
                self.input_cursor_visible = not self.input_cursor_visible

            # Check for scanned UID
            if self._uid_buffer and self.state == self.STATE_WAITING_SCAN:
                self.scanned_uid = str(self._uid_buffer)
                self._uid_buffer = None
                self.existing_card = self.registry.get_card(self.scanned_uid)
                self.state = self.STATE_PICK_SOURCE

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.KEYDOWN:
                    self._handle_key(event)
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    self._handle_click(event.pos)
                elif event.type == pygame.MOUSEWHEEL:
                    self._handle_scroll(event.y)

            self._draw()
            pygame.display.flip()

        self.reader.stop()
        pygame.quit()

    def _handle_scroll(self, direction):
        if self.state == self.STATE_PICK_FILE:
            self.file_scroll = max(0, self.file_scroll - direction * 3)
            max_scroll = max(0, len(self.local_files) - 10)
            self.file_scroll = min(self.file_scroll, max_scroll)
        elif self.state == self.STATE_LIST:
            self.cards_scroll = max(0, self.cards_scroll - direction * 3)
            max_scroll = max(0, len(self.cards_list) - 10)
            self.cards_scroll = min(self.cards_scroll, max_scroll)

    def _handle_key(self, event):
        if event.key == pygame.K_ESCAPE:
            if self.state in (self.STATE_HOME, self.STATE_DONE):
                self.running = False
            else:
                self.state = self.STATE_HOME
                self._reset_reg()
            return

        if self.state == self.STATE_HOME:
            if event.unicode.isdigit():
                # Might be RFID scanner emulating keyboard
                self._uid_buffer = None
                self.state = self.STATE_WAITING_SCAN
            return

        if self.state == self.STATE_WAITING_SCAN:
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if self._scan_buffer.strip():
                    self._uid_buffer = self._scan_buffer.strip()
                    self._scan_buffer = ""
            elif event.unicode.isdigit():
                self._scan_buffer += event.unicode
            return

        if self.state == self.STATE_PICK_SOURCE:
            if event.key == pygame.K_1:
                self.selected_source = "youtube"
                self.input_text = ""
                self.state = self.STATE_INPUT_URL
            elif event.key == pygame.K_2:
                self.selected_source = "local"
                self._scan_local_files()
                self.state = self.STATE_PICK_FILE
            return

        if self.state == self.STATE_INPUT_URL:
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if self.input_text.strip():
                    url = self.input_text.strip()
                    self.input_text = ""
                    self.state = self.STATE_DOWNLOADING
                    threading.Thread(target=self._download_youtube, args=(url,), daemon=True).start()
            elif event.key == pygame.K_BACKSPACE:
                self.input_text = self.input_text[:-1]
            elif event.key == pygame.K_v and (event.mod & pygame.KMOD_CTRL):
                # Paste from clipboard
                try:
                    clip = pygame.scrap.get(pygame.SCRAP_TEXT)
                    if clip:
                        self.input_text += clip.decode('utf-8', errors='ignore').strip('\x00')
                except Exception:
                    pass
            elif event.unicode and event.unicode.isprintable():
                self.input_text += event.unicode
            return

        if self.state == self.STATE_PICK_FILE:
            if event.key == pygame.K_UP and self.file_selected > 0:
                self.file_selected -= 1
                if self.file_selected < self.file_scroll:
                    self.file_scroll = self.file_selected
            elif event.key == pygame.K_DOWN and self.file_selected < len(self.local_files) - 1:
                self.file_selected += 1
                if self.file_selected >= self.file_scroll + 10:
                    self.file_scroll = self.file_selected - 9
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if 0 <= self.file_selected < len(self.local_files):
                    filename = self.local_files[self.file_selected]
                    self.reg_url = filename
                    
                    # Try to fetch title from metadata
                    title = self._get_title_from_file(filename)
                    if title:
                        self.input_text = title
                    else:
                        self.input_text = os.path.splitext(filename)[0]
                        
                    self.state = self.STATE_INPUT_TITLE
            return

        if self.state == self.STATE_INPUT_TITLE:
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if self.input_text.strip():
                    self.reg_title = self.input_text.strip()
                    self.state = self.STATE_CONFIRM
            elif event.key == pygame.K_BACKSPACE:
                self.input_text = self.input_text[:-1]
            elif event.unicode and event.unicode.isprintable():
                self.input_text += event.unicode
            return

        if self.state == self.STATE_CONFIRM:
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) or event.key == pygame.K_y:
                self.registry.register_card(self.scanned_uid, self.reg_title, self.reg_url)
                self.toast_msg = f"Registered: {self.reg_title}"
                self.toast_time = pygame.time.get_ticks() + 3000
                self.state = self.STATE_DONE
            elif event.key == pygame.K_n:
                self.state = self.STATE_HOME
                self._reset_reg()
            return

        if self.state == self.STATE_DONE:
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.state = self.STATE_HOME
                self._reset_reg()
            return

    def _handle_click(self, pos):
        mx, my = pos

        if self.state == self.STATE_HOME:
            # "Register Card" button
            btn_rect = self._btn_rect(self.width // 2, 280, 260, 50)
            if btn_rect.collidepoint(mx, my):
                self.state = self.STATE_WAITING_SCAN
                return
            # "View Cards" button
            btn2_rect = self._btn_rect(self.width // 2, 350, 260, 50)
            if btn2_rect.collidepoint(mx, my):
                self._load_cards_list()
                self.state = self.STATE_LIST
                return

        elif self.state == self.STATE_LIST:
            # Check for delete button clicks
            if not self.cards_list:
                return
            
            list_x = self.width // 2 - 280
            list_y = 138 # hr.bottom + 4 + cr height + 16 approx = 90 + 30 + 16 = 136, but let's calculate based on layout: hr.bottom=120, cr.bottom=144, list_y=160. Wait, better to compute it dynamically or just use the same math.
            # In _draw_cards_list:
            # hr = heading top=90, bottom=120
            # cr = count top=124, bottom=144
            # list_y = 160
            list_y = 160
            item_h = 48
            visible = min(9, len(self.cards_list) - self.cards_scroll)
            
            for i in range(visible):
                idx = self.cards_scroll + i
                if idx >= len(self.cards_list):
                    break
                iy = list_y + i * item_h
                
                # Delete button is at the right edge of the row: (list_x + 520, iy + 4, 30, 40)
                del_btn_rect = pygame.Rect(list_x + 520, iy, 40, item_h)
                if del_btn_rect.collidepoint(mx, my):
                    uid = self.cards_list[idx]['uid']
                    self.registry.delete_card(uid)
                    self.toast_msg = f"Deleted card {uid[:6]}..."
                    self.toast_time = pygame.time.get_ticks() + 3000
                    self._load_cards_list()
                    return

        elif self.state == self.STATE_PICK_SOURCE:
            btn1 = self._btn_rect(self.width // 2, 300, 300, 50)
            btn2 = self._btn_rect(self.width // 2, 370, 300, 50)
            if btn1.collidepoint(mx, my):
                self.selected_source = "youtube"
                self.input_text = ""
                self.state = self.STATE_INPUT_URL
            elif btn2.collidepoint(mx, my):
                self.selected_source = "local"
                self._scan_local_files()
                self.state = self.STATE_PICK_FILE

        elif self.state == self.STATE_PICK_FILE:
            # Click on file items
            list_x = self.width // 2 - 200
            list_y = 200
            item_h = 40
            visible = min(10, len(self.local_files) - self.file_scroll)
            for i in range(visible):
                idx = self.file_scroll + i
                iy = list_y + i * item_h
                item_rect = pygame.Rect(list_x, iy, 400, item_h - 4)
                if item_rect.collidepoint(mx, my):
                    self.file_selected = idx
                    filename = self.local_files[idx]
                    self.reg_url = filename
                    
                    title = self._get_title_from_file(filename)
                    if title:
                        self.input_text = title
                    else:
                        self.input_text = os.path.splitext(filename)[0]
                        
                    self.state = self.STATE_INPUT_TITLE
                    return

    def _download_youtube(self, url):
        import subprocess
        import shutil
        import glob
        self.download_progress = "Starting download..."
        ytdlp = shutil.which("yt-dlp") or "yt-dlp"
        
        # Snapshot music folder before download
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
            url
        ]

        cookies_file = getattr(config, "ytdlp_cookies_file", None)
        cookies_browser = getattr(config, "ytdlp_cookies_browser", None)
        if cookies_file and os.path.exists(cookies_file):
            cmd.extend(["--cookies", cookies_file])
        elif cookies_browser:
            if cookies_browser.lower() == "operagx":
                appdata = os.environ.get('APPDATA', '')
                cookies_browser = f"opera:{appdata}\\Opera Software\\Opera GX Stable"
            cmd.extend(["--cookies-from-browser", cookies_browser])

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    encoding='utf-8', errors='replace',
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            
            for line in iter(proc.stdout.readline, ''):
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
            
            # Compare folder to find new audio file
            after = set(os.listdir(music_dir))
            new_files = after - before
            
            # Find the audio file among new files (not .json, not image)
            audio_exts = {'.webm', '.mp3', '.m4a', '.opus', '.ogg', '.wav', '.flac', '.aac', '.wma', '.mp4'}
            audio_file = None
            for f in new_files:
                ext = os.path.splitext(f)[1].lower()
                if ext in audio_exts:
                    audio_file = f
                    break
            
            # If file already existed (re-download), it won't be in new_files.
            # Fall back: find most recently modified audio file in music dir.
            if not audio_file:
                candidates = []
                for f in os.listdir(music_dir):
                    ext = os.path.splitext(f)[1].lower()
                    if ext in audio_exts:
                        full = os.path.join(music_dir, f)
                        candidates.append((os.path.getmtime(full), f))
                if candidates:
                    candidates.sort(reverse=True)
                    audio_file = candidates[0][1]
            
            if audio_file:
                self.reg_url = audio_file
                title = self._get_title_from_file(audio_file)
                if title:
                    self.input_text = title
                else:
                    self.input_text = os.path.splitext(audio_file)[0]
                self.state = self.STATE_INPUT_TITLE
            else:
                self.toast_msg = "Download failed — no audio file found."
                self.toast_time = pygame.time.get_ticks() + 3000
                self.state = self.STATE_PICK_SOURCE
        except Exception as e:
            self.toast_msg = f"Error: {e}"
            self.toast_time = pygame.time.get_ticks() + 3000
            self.state = self.STATE_PICK_SOURCE


    def _get_title_from_file(self, filename):
        import subprocess
        import shutil
        import json
        file_path = os.path.join(config.music_folder, filename)
        
        # 1. Fallback metadata from yt-dlp json
        base_path = os.path.splitext(file_path)[0]
        json_path = base_path + ".info.json"
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                title = data.get("title")
                artist = data.get("artist") or data.get("uploader") or data.get("channel")
                if title and artist:
                    return f"{artist} - {title}"
                elif title:
                    return title
            except Exception:
                pass
                
        # 2. Try ffprobe
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return None
        cmd = [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", file_path]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                tags_raw = data.get("format", {}).get("tags", {})
                tags = {k.lower(): v for k, v in tags_raw.items()}
                title = tags.get("title")
                artist = tags.get("artist")
                if title and artist:
                    return f"{artist} - {title}"
                elif title:
                    return title
        except Exception:
            pass
        return None

    def _btn_rect(self, cx, cy, w, h):
        return pygame.Rect(cx - w // 2, cy - h // 2, w, h)

    def _reset_reg(self):
        self.scanned_uid = None
        self.existing_card = None
        self.selected_source = None
        self.input_text = ""
        self.reg_title = ""
        self.reg_url = ""
        self._uid_buffer = None

    def _draw(self):
        self.screen.fill(Colors.BG)
        self.screen.blit(self._bg, (0, 0))
        self.screen.blit(self._scanlines, (0, 0))

        # Header
        self._draw_header()

        if self.state == self.STATE_HOME:
            self._draw_home()
        elif self.state == self.STATE_WAITING_SCAN:
            self._draw_waiting_scan()
        elif self.state == self.STATE_PICK_SOURCE:
            self._draw_pick_source()
        elif self.state == self.STATE_INPUT_URL:
            self._draw_input("Enter YouTube URL to Download:", self.input_text)
        elif self.state == self.STATE_DOWNLOADING:
            self._draw_downloading()
        elif self.state == self.STATE_PICK_FILE:
            self._draw_pick_file()
        elif self.state == self.STATE_INPUT_TITLE:
            self._draw_input("Enter Song Title:", self.input_text)
        elif self.state == self.STATE_CONFIRM:
            self._draw_confirm()
        elif self.state == self.STATE_DONE:
            self._draw_done()
        elif self.state == self.STATE_LIST:
            self._draw_cards_list()

        # Toast
        self._draw_toast()

        # Bottom hint
        self._draw_bottom_hint()

    def _draw_header(self):
        # Logo bar
        title = self.font_title.render("uTune", True, Colors.CYAN)
        self.screen.blit(title, (28, 16))

        sub = self.font_label.render("CARD REGISTRATION", True, Colors.VIOLET)
        sub.set_alpha(180)
        self.screen.blit(sub, (28 + title.get_width() + 16, 28))

        # Divider
        div = pygame.Surface((self.width - 56, 1), pygame.SRCALPHA)
        div.fill((255, 255, 255, 20))
        self.screen.blit(div, (28, 70))

    def _draw_home(self):
        cx = self.width // 2
        cy = 200

        welcome = self.font_heading.render("Card Manager", True, Colors.TEXT)
        wr = welcome.get_rect(centerx=cx, top=cy)
        self.screen.blit(welcome, wr)

        sub = self.font_body.render(
            f"Cards registered: {self._card_count()}",
            True, Colors.TEXT_SECONDARY
        )
        sr = sub.get_rect(centerx=cx, top=wr.bottom + 12)
        self.screen.blit(sub, sr)

        # Buttons
        self._draw_button(cx, 300, 260, 50, "Register New Card", Colors.CYAN)
        self._draw_button(cx, 370, 260, 50, "View All Cards", Colors.VIOLET)

    def _draw_waiting_scan(self):
        cx = self.width // 2
        cy = self.height // 2 - 40

        # Pulsing ring
        radius = 50 + int(5 * math.sin(self.pulse * 2))
        ring_alpha = int(120 + 60 * math.sin(self.pulse * 3))
        ring_surf = pygame.Surface((radius * 2 + 20, radius * 2 + 20), pygame.SRCALPHA)
        pygame.draw.circle(ring_surf, (*Colors.CYAN, ring_alpha), (radius + 10, radius + 10), radius, 3)
        self.screen.blit(ring_surf, (cx - radius - 10, cy - radius - 10))

        inner_alpha = int(40 + 20 * math.sin(self.pulse * 2 + 1))
        inner = pygame.Surface((60, 60), pygame.SRCALPHA)
        pygame.draw.circle(inner, (*Colors.CYAN, inner_alpha), (30, 30), 28)
        self.screen.blit(inner, (cx - 30, cy - 30))

        text = self.font_heading.render("Scan RFID Card", True, Colors.TEXT)
        tr = text.get_rect(centerx=cx, top=cy + radius + 30)
        self.screen.blit(text, tr)

        sub = self.font_body.render("Place card on the reader...", True, Colors.TEXT_MUTED)
        sr = sub.get_rect(centerx=cx, top=tr.bottom + 10)
        self.screen.blit(sub, sr)

    def _draw_pick_source(self):
        cx = self.width // 2

        # Show UID
        uid_text = self.font_mono.render(f"Card UID: {self.scanned_uid}", True, Colors.CYAN)
        ur = uid_text.get_rect(centerx=cx, top=100)
        self.screen.blit(uid_text, ur)

        if self.existing_card:
            warn = self.font_body.render(
                f"Already registered: {self.existing_card['title']}", True, Colors.ORANGE
            )
            wr = warn.get_rect(centerx=cx, top=ur.bottom + 8)
            self.screen.blit(warn, wr)
            over = self.font_small.render("Continuing will overwrite", True, Colors.TEXT_MUTED)
            ovr = over.get_rect(centerx=cx, top=wr.bottom + 4)
            self.screen.blit(over, ovr)

        heading = self.font_heading.render("Choose Audio Source", True, Colors.TEXT)
        hr = heading.get_rect(centerx=cx, top=200)
        self.screen.blit(heading, hr)

        self._draw_button(cx, 300, 300, 50, "[1] YouTube URL", Colors.CYAN)
        self._draw_button(cx, 370, 300, 50, "[2] Local File", Colors.VIOLET)

    def _draw_input(self, label, text):
        cx = self.width // 2

        lbl = self.font_heading.render(label, True, Colors.TEXT)
        lr = lbl.get_rect(centerx=cx, top=180)
        self.screen.blit(lbl, lr)

        # Input box
        box_w = min(600, self.width - 100)
        box_h = 48
        box_x = cx - box_w // 2
        box_y = lr.bottom + 30

        box_surf = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
        pygame.draw.rect(box_surf, (*Colors.GLASS_BG, 160), box_surf.get_rect(), border_radius=8)
        pygame.draw.rect(box_surf, (*Colors.CYAN, 80), box_surf.get_rect(), width=1, border_radius=8)
        self.screen.blit(box_surf, (box_x, box_y))

        # Text content
        clip_w = box_w - 28
        txt_surf = self.font_input.render(text, True, Colors.TEXT)
        
        # If empty, show placeholder
        if not text:
            placeholder = "Paste link here..." if "YouTube" in label else "Type here..."
            p_surf = self.font_input.render(placeholder, True, Colors.TEXT_MUTED)
            self.screen.blit(p_surf, (box_x + 14, box_y + (box_h - p_surf.get_height()) // 2))
            
        # Determine scroll offset to keep cursor visible at the right edge
        offset_x = 0
        if txt_surf.get_width() > clip_w:
            offset_x = txt_surf.get_width() - clip_w
            
        # Draw clipped text
        if text:
            clip_rect = pygame.Rect(offset_x, 0, min(clip_w, txt_surf.get_width()), txt_surf.get_height())
            clipped_surf = txt_surf.subsurface(clip_rect)
            self.screen.blit(clipped_surf, (box_x + 14, box_y + (box_h - txt_surf.get_height()) // 2))
            
        # Draw cursor
        if self.input_cursor_visible:
            cursor_x = box_x + 14 + min(clip_w, txt_surf.get_width() if text else 0) + 2
            pygame.draw.line(self.screen, Colors.CYAN, 
                             (cursor_x, box_y + 12), 
                             (cursor_x, box_y + box_h - 12), 2)

        hint = self.font_small.render("Press ENTER to confirm  •  ESC to cancel", True, Colors.TEXT_MUTED)
        hr = hint.get_rect(centerx=cx, top=box_y + box_h + 16)
        self.screen.blit(hint, hr)

        if label.startswith("Enter YouTube"):
            paste_hint = self.font_small.render("Tip: Ctrl+V to paste", True, Colors.TEXT_MUTED)
            pr = paste_hint.get_rect(centerx=cx, top=hr.bottom + 6)
            self.screen.blit(paste_hint, pr)

    def _draw_downloading(self):
        cx = self.width // 2
        cy = self.height // 2

        # Spinner
        radius = 40
        spinner_surf = pygame.Surface((radius * 2 + 10, radius * 2 + 10), pygame.SRCALPHA)
        start_angle = self.pulse * 5 % (math.pi * 2)
        end_angle = start_angle + math.pi * 1.5
        
        # Pygame arc is a bit limited, so we draw a few circles forming an arc
        for i in range(20):
            a = start_angle + (end_angle - start_angle) * (i / 20.0)
            x = radius + 5 + int(radius * math.cos(a))
            y = radius + 5 + int(radius * math.sin(a))
            pygame.draw.circle(spinner_surf, Colors.CYAN, (x, y), 4 + int(i * 0.2))

        self.screen.blit(spinner_surf, (cx - radius - 5, cy - 80))

        heading = self.font_heading.render("Downloading Audio...", True, Colors.TEXT)
        hr = heading.get_rect(centerx=cx, top=cy + 10)
        self.screen.blit(heading, hr)
        
        prog = self.font_body.render(self.download_progress, True, Colors.TEXT_SECONDARY)
        pr = prog.get_rect(centerx=cx, top=hr.bottom + 10)
        self.screen.blit(prog, pr)

        hint = self.font_small.render("Please wait while yt-dlp processes the file...", True, Colors.TEXT_MUTED)
        hir = hint.get_rect(centerx=cx, top=pr.bottom + 20)
        self.screen.blit(hint, hir)

    def _draw_pick_file(self):
        cx = self.width // 2

        heading = self.font_heading.render("Select Audio File", True, Colors.TEXT)
        hr = heading.get_rect(centerx=cx, top=100)
        self.screen.blit(heading, hr)

        folder_text = self.font_small.render(f"Folder: {config.music_folder}", True, Colors.TEXT_MUTED)
        fr = folder_text.get_rect(centerx=cx, top=hr.bottom + 8)
        self.screen.blit(folder_text, fr)

        if not self.local_files:
            empty = self.font_body.render("No audio files found in music folder", True, Colors.RED)
            er = empty.get_rect(centerx=cx, top=250)
            self.screen.blit(empty, er)

            hint = self.font_small.render(
                f"Add .mp3, .flac, .wav, .ogg files to: {config.music_folder}",
                True, Colors.TEXT_MUTED
            )
            hir = hint.get_rect(centerx=cx, top=er.bottom + 10)
            self.screen.blit(hint, hir)
            return

        list_x = cx - 220
        list_y = 180
        item_h = 40
        visible = min(10, len(self.local_files) - self.file_scroll)

        for i in range(visible):
            idx = self.file_scroll + i
            iy = list_y + i * item_h
            is_selected = idx == self.file_selected

            # Item background
            item_surf = pygame.Surface((440, item_h - 4), pygame.SRCALPHA)
            bg_alpha = 140 if is_selected else 60
            border_color = Colors.CYAN if is_selected else Colors.VIOLET
            pygame.draw.rect(item_surf, (*Colors.GLASS_BG, bg_alpha), item_surf.get_rect(), border_radius=6)
            pygame.draw.rect(item_surf, (*border_color, 80 if is_selected else 30), item_surf.get_rect(), width=1, border_radius=6)
            self.screen.blit(item_surf, (list_x, iy))

            # File icon
            ext = os.path.splitext(self.local_files[idx])[1].lower()
            icon_color = Colors.CYAN if is_selected else Colors.TEXT_MUTED
            icon = self.font_label.render(ext.upper(), True, icon_color)
            self.screen.blit(icon, (list_x + 10, iy + (item_h - 4 - icon.get_height()) // 2))

            # Filename
            fname = self.local_files[idx]
            if len(fname) > 40:
                fname = fname[:37] + "..."
            txt_color = Colors.TEXT if is_selected else Colors.TEXT_SECONDARY
            name_surf = self.font_file.render(fname, True, txt_color)
            self.screen.blit(name_surf, (list_x + 60, iy + (item_h - 4 - name_surf.get_height()) // 2))

        # Scroll indicator
        if len(self.local_files) > 10:
            total = len(self.local_files)
            bar_h = max(20, int(10 / total * (visible * item_h)))
            bar_y = list_y + int(self.file_scroll / total * (visible * item_h))
            scroll_surf = pygame.Surface((4, bar_h), pygame.SRCALPHA)
            scroll_surf.fill((*Colors.CYAN, 80))
            self.screen.blit(scroll_surf, (list_x + 446, bar_y))

        hint = self.font_small.render("↑↓ Navigate  •  ENTER to select  •  Click to pick  •  Scroll wheel", True, Colors.TEXT_MUTED)
        hir = hint.get_rect(centerx=cx, top=list_y + visible * item_h + 16)
        self.screen.blit(hint, hir)

    def _draw_confirm(self):
        cx = self.width // 2

        heading = self.font_heading.render("Confirm Registration", True, Colors.TEXT)
        hr = heading.get_rect(centerx=cx, top=140)
        self.screen.blit(heading, hr)

        # Card with details
        card_w = min(500, self.width - 80)
        card_h = 180
        card_x = cx - card_w // 2
        card_y = hr.bottom + 30

        card = pygame.Surface((card_w, card_h), pygame.SRCALPHA)
        pygame.draw.rect(card, (*Colors.GLASS_BG, 140), card.get_rect(), border_radius=12)
        pygame.draw.rect(card, (*Colors.CYAN, 50), card.get_rect(), width=1, border_radius=12)
        self.screen.blit(card, (card_x, card_y))

        pad = 20
        fields = [
            ("UID", self.scanned_uid),
            ("TITLE", self.reg_title),
            ("SOURCE", self.reg_url[:50] + ("..." if len(self.reg_url) > 50 else "")),
            ("TYPE", self.selected_source.upper()),
        ]
        fy = card_y + pad
        for label, value in fields:
            lbl = self.font_label.render(label, True, Colors.VIOLET)
            self.screen.blit(lbl, (card_x + pad, fy))
            val = self.font_body.render(value, True, Colors.TEXT)
            self.screen.blit(val, (card_x + pad + 80, fy))
            fy += 36

        hint = self.font_body.render("Press Y or ENTER to confirm  •  N to cancel", True, Colors.TEXT_SECONDARY)
        hir = hint.get_rect(centerx=cx, top=card_y + card_h + 20)
        self.screen.blit(hint, hir)

    def _draw_done(self):
        cx = self.width // 2
        cy = self.height // 2 - 30

        # Checkmark circle
        circle_surf = pygame.Surface((80, 80), pygame.SRCALPHA)
        pygame.draw.circle(circle_surf, (*Colors.GREEN, 40), (40, 40), 38)
        pygame.draw.circle(circle_surf, (*Colors.GREEN, 180), (40, 40), 38, 3)
        self.screen.blit(circle_surf, (cx - 40, cy - 60))

        # Checkmark
        check_pts = [(cx - 12, cy - 22), (cx - 2, cy - 12), (cx + 16, cy - 34)]
        pygame.draw.lines(self.screen, Colors.GREEN, False, check_pts, 3)

        done = self.font_heading.render("Card Registered!", True, Colors.GREEN)
        dr = done.get_rect(centerx=cx, top=cy + 30)
        self.screen.blit(done, dr)

        title = self.font_body.render(self.reg_title, True, Colors.TEXT)
        tr = title.get_rect(centerx=cx, top=dr.bottom + 10)
        self.screen.blit(title, tr)

        hint = self.font_small.render("Press ENTER to register another  •  ESC to exit", True, Colors.TEXT_MUTED)
        hir = hint.get_rect(centerx=cx, top=tr.bottom + 30)
        self.screen.blit(hint, hir)

    def _draw_cards_list(self):
        cx = self.width // 2

        heading = self.font_heading.render("Registered Cards", True, Colors.TEXT)
        hr = heading.get_rect(centerx=cx, top=90)
        self.screen.blit(heading, hr)

        count = self.font_small.render(f"{len(self.cards_list)} cards", True, Colors.TEXT_MUTED)
        cr = count.get_rect(centerx=cx, top=hr.bottom + 4)
        self.screen.blit(count, cr)

        if not self.cards_list:
            empty = self.font_body.render("No cards registered yet", True, Colors.TEXT_MUTED)
            er = empty.get_rect(centerx=cx, top=250)
            self.screen.blit(empty, er)
            return

        list_x = cx - 280
        list_y = cr.bottom + 16
        item_h = 48
        visible = min(9, len(self.cards_list) - self.cards_scroll)

        for i in range(visible):
            idx = self.cards_scroll + i
            if idx >= len(self.cards_list):
                break
            card = self.cards_list[idx]
            iy = list_y + i * item_h

            row = pygame.Surface((560, item_h - 4), pygame.SRCALPHA)
            pygame.draw.rect(row, (*Colors.GLASS_BG, 80), row.get_rect(), border_radius=6)
            pygame.draw.rect(row, (*Colors.VIOLET, 25), row.get_rect(), width=1, border_radius=6)
            self.screen.blit(row, (list_x, iy))

            # UID
            uid_surf = self.font_label.render(card['uid'][:12], True, Colors.CYAN)
            self.screen.blit(uid_surf, (list_x + 10, iy + (item_h - 4 - uid_surf.get_height()) // 2))

            # Title
            title_text = card['title']
            if len(title_text) > 28:
                title_text = title_text[:25] + "..."
            t_surf = self.font_body.render(title_text, True, Colors.TEXT)
            self.screen.blit(t_surf, (list_x + 140, iy + (item_h - 4 - t_surf.get_height()) // 2))

            # Source hint
            is_url = card['url'].startswith("http")
            src_label = "YT" if is_url else "LOCAL"
            src_color = Colors.CYAN if is_url else Colors.VIOLET
            src = self.font_label.render(src_label, True, src_color)
            self.screen.blit(src, (list_x + 460, iy + (item_h - 4 - src.get_height()) // 2))

            # Delete button (X)
            del_rect = pygame.Rect(list_x + 520, iy + (item_h - 32) // 2, 32, 28)
            pygame.draw.rect(self.screen, (*Colors.RED, 50), del_rect, border_radius=4)
            pygame.draw.rect(self.screen, (*Colors.RED, 150), del_rect, width=1, border_radius=4)
            x_surf = self.font_btn.render("X", True, Colors.RED)
            self.screen.blit(x_surf, (del_rect.centerx - x_surf.get_width() // 2, del_rect.centery - x_surf.get_height() // 2))

        if len(self.cards_list) > 9:
            total = len(self.cards_list)
            bar_total_h = visible * item_h
            bar_h = max(20, int(9 / total * bar_total_h))
            bar_y = list_y + int(self.cards_scroll / total * bar_total_h)
            scroll_surf = pygame.Surface((4, bar_h), pygame.SRCALPHA)
            scroll_surf.fill((*Colors.VIOLET, 80))
            self.screen.blit(scroll_surf, (list_x + 566, bar_y))

    def _draw_button(self, cx, cy, w, h, text, color):
        rect = self._btn_rect(cx, cy, w, h)
        btn = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(btn, (*Colors.GLASS_BG, 140), btn.get_rect(), border_radius=10)
        pygame.draw.rect(btn, (*color, 100), btn.get_rect(), width=1, border_radius=10)

        # Top highlight
        highlight = pygame.Surface((w, 1), pygame.SRCALPHA)
        for hx in range(w):
            t = hx / w
            a = int(80 * math.sin(t * math.pi))
            highlight.set_at((hx, 0), (*color, a))
        btn.blit(highlight, (0, 0))

        self.screen.blit(btn, rect.topleft)

        txt = self.font_btn.render(text, True, color)
        tr = txt.get_rect(center=rect.center)
        self.screen.blit(txt, tr)

    def _draw_toast(self):
        now = pygame.time.get_ticks()
        if now < self.toast_time and self.toast_msg:
            remaining = self.toast_time - now
            alpha = min(255, remaining // 4)

            text = self.font_body.render(self.toast_msg, True, Colors.TEXT)
            tw, th = text.get_size()
            pad_x, pad_y = 24, 12
            toast_w = tw + pad_x * 2
            toast_h = th + pad_y * 2
            tx = (self.width - toast_w) // 2
            ty = self.height - 90

            bg = pygame.Surface((toast_w, toast_h), pygame.SRCALPHA)
            pygame.draw.rect(bg, (*Colors.GLASS_BG, min(alpha, 200)), bg.get_rect(), border_radius=12)
            pygame.draw.rect(bg, (*Colors.GREEN, min(alpha, 80)), bg.get_rect(), width=1, border_radius=12)
            self.screen.blit(bg, (tx, ty))

            text.set_alpha(alpha)
            self.screen.blit(text, (tx + pad_x, ty + pad_y))

    def _draw_bottom_hint(self):
        bar_y = self.height - 40
        div = pygame.Surface((self.width, 1), pygame.SRCALPHA)
        div.fill((255, 255, 255, 15))
        self.screen.blit(div, (0, bar_y))

        hint = self.font_small.render("[ESC] Back / Exit", True, Colors.TEXT_MUTED)
        self.screen.blit(hint, (28, bar_y + 12))

    def _card_count(self):
        import sqlite3
        try:
            with sqlite3.connect(self.registry.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT COUNT(*) FROM cards')
                return cursor.fetchone()[0]
        except Exception:
            return 0


def main():
    try:
        pygame.scrap.init()
    except Exception:
        pass

    app = RegisterUI()
    app.run()


if __name__ == "__main__":
    main()
