"""
Export YouTube cookies from your browser to a cookies.txt file.

Usage:
    1. Install the Chrome/Edge extension "Get cookies.txt LOCALLY"
       https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc
    2. Navigate to https://www.youtube.com in your browser
    3. Click the extension icon and export cookies
    4. Save the file as 'cookies.txt' in this jukebox folder
    
OR use this script (requires browser to be CLOSED):
    py export_cookies.py
"""
import subprocess
import sys
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

COOKIES_FILE = "cookies.txt"
BROWSERS = ["chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium"]


def try_export(browser):
    print(f"  Trying {browser}...")
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--cookies-from-browser", browser,
                "--cookies", COOKIES_FILE,
                "--skip-download",
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and os.path.isfile(COOKIES_FILE):
            size = os.path.getsize(COOKIES_FILE)
            if size > 100:
                print(f"  ✓ Success! Exported cookies from {browser} ({size} bytes)")
                return True
        if result.stderr:
            err = result.stderr.strip().split("\n")[0]
            print(f"  ✗ {err[:100]}")
    except FileNotFoundError:
        print("  ✗ yt-dlp not found")
    except subprocess.TimeoutExpired:
        print("  ✗ Timed out")
    return False


def main():
    print("=" * 50)
    print("YouTube Cookie Exporter for uTune Jukebox")
    print("=" * 50)
    print()
    print("NOTE: Close your browser first! The cookie database")
    print("      is locked while the browser is running.")
    print()

    for browser in BROWSERS:
        if try_export(browser):
            print()
            print(f"Cookies saved to: {os.path.abspath(COOKIES_FILE)}")
            print("Your jukebox should now be able to play YouTube audio!")
            return

    print()
    print("=" * 50)
    print("AUTOMATIC EXPORT FAILED")
    print("=" * 50)
    print()
    print("Manual method:")
    print("1. Install 'Get cookies.txt LOCALLY' browser extension")
    print("2. Go to https://www.youtube.com and log in")
    print("3. Click the extension icon → Export")
    print("4. Save as 'cookies.txt' in this folder:")
    print(f"   {os.path.dirname(os.path.abspath(__file__))}")


if __name__ == "__main__":
    main()
