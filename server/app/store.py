"""SQLite slot for the one adjourned game. Nothing else is kept on the server."""
import json
import sqlite3
from pathlib import Path


class Store:
    def __init__(self, data_dir: str):
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(Path(data_dir) / "chessfish.db")
        self.db.execute("CREATE TABLE IF NOT EXISTS adjourned (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT)")

    def save(self, data: dict):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO adjourned VALUES (1, ?)", (json.dumps(data),))

    def load(self) -> dict | None:
        row = self.db.execute("SELECT data FROM adjourned WHERE id = 1").fetchone()
        return json.loads(row[0]) if row else None

    def clear(self):
        with self.db:
            self.db.execute("DELETE FROM adjourned")
