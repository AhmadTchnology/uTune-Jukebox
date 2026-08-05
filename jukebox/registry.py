import sqlite3
from datetime import datetime

class Registry:
    def __init__(self, db_path="jukebox.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS cards (
                    uid TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    youtube_url TEXT NOT NULL,
                    date_added TIMESTAMP
                )
            ''')
            conn.commit()

    def get_card(self, uid):
        """Returns a dict with title and url if found, else None"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT title, youtube_url FROM cards WHERE uid = ?', (uid,))
            row = cursor.fetchone()
            if row:
                return {'title': row[0], 'youtube_url': row[1]}
            return None

    def register_card(self, uid, title, url):
        """Adds or updates a card in the registry."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO cards (uid, title, youtube_url, date_added)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(uid) DO UPDATE SET
                    title=excluded.title,
                    youtube_url=excluded.youtube_url,
                    date_added=excluded.date_added
            ''', (uid, title, url, datetime.now()))
            conn.commit()

if __name__ == '__main__':
    # Simple manual test
    r = Registry("test.db")
    r.register_card("12345", "Test Song", "https://youtube.com/watch?v=dQw4w9WgXcQ")
    print("Fetched:", r.get_card("12345"))
    print("Fetched unknown:", r.get_card("99999"))
