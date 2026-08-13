"""Platform detection and path abstraction for Android / desktop."""
import os
import sys


def is_android():
    try:
        import android  # noqa: F401
        return True
    except ImportError:
        return False


def is_desktop():
    return not is_android()


def get_storage_dir():
    if is_android():
        from android.storage import app_storage_path
        return app_storage_path()
    return os.path.dirname(os.path.abspath(__file__))


def get_music_dir():
    music_dir = os.path.join(get_storage_dir(), "music")
    os.makedirs(music_dir, exist_ok=True)
    return music_dir


def get_db_path(configured_path="jukebox.db"):
    if is_android() and not os.path.isabs(configured_path):
        return os.path.join(get_storage_dir(), configured_path)
    if not os.path.isabs(configured_path):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), configured_path)
    return configured_path


def get_config_path():
    if is_android():
        return os.path.join(get_storage_dir(), "config.yaml")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


def get_subprocess_flags():
    if os.name == "nt":
        import subprocess
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def request_android_permissions():
    if not is_android():
        return
    try:
        from android.permissions import request_permissions, Permission
        request_permissions([
            Permission.INTERNET,
            Permission.NFC
        ])
    except Exception as e:
        print(f"[Platform] Permission request error: {e}")
