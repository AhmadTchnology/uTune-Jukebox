"""Platform detection and path abstraction for Android / desktop."""
import os
import sys


def is_android():
    from kivy.utils import platform
    return platform == 'android'


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


def request_android_permissions(callback=None):
    """Request runtime permissions on Android 6+.

    Must be called AFTER the Activity is fully created (use Clock.schedule_once).
    NFC is an install-time permission (declared in manifest), not runtime.
    INTERNET is also install-time. But we request WRITE/READ_EXTERNAL_STORAGE
    for music file access on older Android versions.
    """
    if not is_android():
        if callback:
            callback([], [])
        return
    try:
        from android.permissions import request_permissions, Permission
        perms = [
            Permission.INTERNET,
            Permission.WRITE_EXTERNAL_STORAGE,
            Permission.READ_EXTERNAL_STORAGE,
            "android.permission.NFC",
            "android.permission.BLUETOOTH_CONNECT"
        ]
        # For Android 13+ (API 33+) we need these instead of READ_EXTERNAL_STORAGE
        try:
            perms.extend([Permission.READ_MEDIA_AUDIO, Permission.READ_MEDIA_IMAGES])
        except AttributeError:
            pass # Older android.permissions API doesn't have these constants yet

        request_permissions(perms, callback)
        print(f"[Platform] Requested permissions: {perms}")
    except Exception as e:
        print(f"[Platform] Permission request error: {e}")


def get_dpi_scale():
    """Get a font/UI scale factor based on screen density.

    Returns a multiplier: 1.0 for ~160dpi desktop, ~2.0 for typical tablets.
    On desktop, returns 1.0.
    """
    if is_android():
        try:
            from jnius import autoclass
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            activity = PythonActivity.mActivity
            metrics = activity.getResources().getDisplayMetrics()
            # density is 1.0 at 160dpi, 2.0 at 320dpi, etc.
            return max(1.0, metrics.density)
        except Exception as e:
            print(f"[Platform] Could not read DPI: {e}")
            return 1.5  # Safe fallback for tablets
    return 1.0
