import yaml
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")


class Config:
    def __init__(self):
        self.config = self._load()

    def _load(self):
        if not os.path.exists(CONFIG_PATH):
            raise FileNotFoundError(f"Missing config file at {CONFIG_PATH}")
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    @property
    def db_path(self):
        return self.config.get("db_path", "jukebox.db")

    @property
    def rfid_mode(self):
        return self.config.get("rfid", {}).get("mode", "keyboard")

    @property
    def rfid_debounce(self):
        return self.config.get("rfid", {}).get("debounce_seconds", 3.0)

    @property
    def rfid_serial_port(self):
        return self.config.get("rfid", {}).get("serial_port", "COM3")

    @property
    def rfid_baud_rate(self):
        return self.config.get("rfid", {}).get("baud_rate", 9600)

    @property
    def mpv_path(self):
        return self.config.get("player", {}).get("mpv_path", "mpv")

    @property
    def ytdlp_format(self):
        return self.config.get("player", {}).get("ytdlp_format", "bestaudio/best")

    @property
    def music_folder(self):
        path = self.config.get("player", {}).get("music_folder", "music")
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(__file__), path)
        return path

    @property
    def ytdlp_cookies_browser(self):
        return self.config.get("player", {}).get("cookies_from_browser", "")

    @property
    def ytdlp_cookies_file(self):
        path = self.config.get("player", {}).get("cookies_file", "")
        if path and not os.path.isabs(path):
            path = os.path.join(os.path.dirname(__file__), path)
        return path

    @property
    def ui_resolution(self):
        return tuple(self.config.get("ui", {}).get("resolution", [1024, 600]))

    @property
    def ui_fullscreen(self):
        return self.config.get("ui", {}).get("fullscreen", False)

    @property
    def ui_fps(self):
        return self.config.get("ui", {}).get("fps", 30)


config = Config()
