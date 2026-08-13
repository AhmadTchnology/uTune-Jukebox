import yaml
import os
from platform_utils import get_config_path, get_db_path, get_storage_dir


class Config:
    def __init__(self):
        self.config = self._load()

    def _load(self):
        config_path = get_config_path()
        if not os.path.exists(config_path):
            # Return sensible defaults if config file missing (e.g. first run on Android)
            return {}
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    @property
    def db_path(self):
        return get_db_path(self.config.get("db_path", "jukebox.db"))

    @property
    def rfid_mode(self):
        return self.config.get("rfid", {}).get("mode", "nfc_android")

    @property
    def rfid_debounce(self):
        return self.config.get("rfid", {}).get("debounce_seconds", 3.0)

    @property
    def rfid_serial_port(self):
        return self.config.get("rfid", {}).get("serial_port", "/dev/ttyUSB0")

    @property
    def rfid_baud_rate(self):
        return self.config.get("rfid", {}).get("baud_rate", 9600)

    @property
    def ytdlp_format(self):
        return self.config.get("player", {}).get("ytdlp_format", "bestaudio/best")

    @property
    def music_folder(self):
        path = self.config.get("player", {}).get("music_folder", "music")
        if not os.path.isabs(path):
            path = os.path.join(get_storage_dir(), path)
        os.makedirs(path, exist_ok=True)
        return path

    @property
    def ytdlp_cookies_browser(self):
        return self.config.get("player", {}).get("cookies_from_browser", "")

    @property
    def ytdlp_cookies_file(self):
        path = self.config.get("player", {}).get("cookies_file", "")
        if path and not os.path.isabs(path):
            path = os.path.join(get_storage_dir(), path)
        return path

    @property
    def ui_resolution(self):
        return tuple(self.config.get("ui", {}).get("resolution", [1024, 600]))

    @property
    def ui_fullscreen(self):
        return self.config.get("ui", {}).get("fullscreen", True)

    @property
    def ui_fps(self):
        return self.config.get("ui", {}).get("fps", 30)


config = Config()
