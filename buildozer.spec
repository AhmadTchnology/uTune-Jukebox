[app]

# Application metadata
title = uTune Jukebox
package.name = utune
package.domain = com.ahmadtchnology

# Source configuration
source.dir = ./jukebox
source.include_exts = py,yaml,txt,png,jpg,jpeg,webp,json,db
source.exclude_dirs = __pycache__,.git,.agents,.vscode
source.exclude_patterns = deno.exe,*.pyc,cookies.txt

# Version
version = 1.0.0

# Requirements (python-for-android recipes + pure-python packages)
requirements = python3,kivy,pyjnius,pyyaml,yt-dlp,sqlite3,pillow

# Android permissions
android.permissions = INTERNET, NFC

# Android API levels (Android 11 = API 30)
android.api = 30
android.minapi = 21
android.ndk = 25b

# Architecture (ARM 32-bit for older tablets)
android.archs = armeabi-v7a

# App display
fullscreen = 1
orientation = landscape

# Presplash and icon (can be customized later)
# presplash.filename = %(source.dir)s/data/presplash.png
# icon.filename = %(source.dir)s/data/icon.png

# Android specific
android.accept_sdk_license = True
android.enable_androidx = True

# Don't copy these to the APK
android.skip_update = False

# Gradle dependencies (none needed for now)
# android.gradle_dependencies =

# Logcat filter
android.logcat_filters = *:S python:D

# Build settings
# (uncomment for release builds)
# android.release_artifact = aab
# android.keystore = %(source.dir)s/../keystore.jks
# android.keyalias = utune

[buildozer]

# Build log level (0=error, 1=info, 2=debug)
log_level = 2

# Warn on root
warn_on_root = 1
