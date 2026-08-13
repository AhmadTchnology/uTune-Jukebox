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
                    file_path TEXT NOT NULL,
                    date_added TIMESTAMP
                )
            ''')
            # Check if youtube_url column exists and migrate it
            cursor.execute("PRAGMA table_info(cards)")
            columns = [info[1] for info in cursor.fetchall()]
            if 'youtube_url' in columns and 'file_path' not in columns:
                cursor.execute('ALTER TABLE cards RENAME COLUMN youtube_url TO file_path')
            conn.commit()

    def get_card(self, uid):
        """Returns a dict with title and file_path if found, else None"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT title, file_path FROM cards WHERE uid = ?', (uid,))
            row = cursor.fetchone()
            if row:
                return {'title': row[0], 'file_path': row[1]}
            return None

    def register_card(self, uid, title, file_path):
        """Adds or updates a card in the registry."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO cards (uid, title, file_path, date_added)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(uid) DO UPDATE SET
                    title=excluded.title,
                    file_path=excluded.file_path,
                    date_added=excluded.date_added
            ''', (uid, title, file_path, datetime.now()))
            conn.commit()

    def delete_card(self, uid):
        """Removes a card from the registry."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM cards WHERE uid = ?', (uid,))
            conn.commit()

if __name__ == '__main__':
    # Simple manual test
    r = Registry("test.db")
    r.register_card("12345", "Test Song", "song.mp3")
    print("Fetched:", r.get_card("12345"))
    print("Fetched unknown:", r.get_card("99999"))
