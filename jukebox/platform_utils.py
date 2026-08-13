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

    NFC and INTERNET are 'normal' permissions — auto-granted at install time.
    BLUETOOTH_CONNECT only exists on API 31+ (Android 12).
    READ_MEDIA_AUDIO/IMAGES replace READ_EXTERNAL_STORAGE on API 33+ (Android 13).
    """
    if not is_android():
        if callback:
            callback([], [])
        return
    try:
        from android.permissions import request_permissions, Permission
        from jnius import autoclass

        Build = autoclass('android.os.Build$VERSION')
        sdk_int = Build.SDK_INT

        perms = []

        if sdk_int >= 33:
            # Android 13+: granular media permissions
            perms.append("android.permission.READ_MEDIA_AUDIO")
            perms.append("android.permission.READ_MEDIA_IMAGES")
        else:
            # Android 12 and below: legacy storage
            perms.append(Permission.WRITE_EXTERNAL_STORAGE)
            perms.append(Permission.READ_EXTERNAL_STORAGE)

        if sdk_int >= 31:
            # Android 12+: new Bluetooth permissions
            perms.append("android.permission.BLUETOOTH_CONNECT")

        request_permissions(perms, callback)
        print(f"[Platform] SDK={sdk_int}, requesting: {perms}")
    except Exception as e:
        print(f"[Platform] Permission request error: {e}")
        import traceback
        traceback.print_exc()



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
