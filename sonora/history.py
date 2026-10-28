"""Histórico de downloads (SQLite) - evita baixar a mesma faixa duas vezes."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .models import TrackMeta


class History:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS downloads (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   key TEXT NOT NULL,
                   title TEXT, artists TEXT, album TEXT,
                   path TEXT NOT NULL,
                   created_at REAL NOT NULL)"""
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_key ON downloads(key)")
        self._db.commit()

    def find(self, *keys: str) -> str | None:
        """Retorna o caminho de um download anterior (se o arquivo ainda existir)."""
        keys = tuple(k for k in keys if k)
        if not keys:
            return None
        marks = ",".join("?" * len(keys))
        with self._lock:
            rows = self._db.execute(
                f"SELECT path FROM downloads WHERE key IN ({marks}) ORDER BY id DESC", keys
            ).fetchall()
        for (p,) in rows:
            if Path(p).exists():
                return p
        return None

    def add(self, meta: TrackMeta, path: Path) -> None:
        keys = {k for k in (meta.spotify_id, meta.youtube_id) if k}
        with self._lock:
            for k in keys:
                self._db.execute(
                    "INSERT INTO downloads(key,title,artists,album,path,created_at) VALUES (?,?,?,?,?,?)",
                    (k, meta.title, ", ".join(meta.artists), meta.album, str(path), time.time()),
                )
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()
