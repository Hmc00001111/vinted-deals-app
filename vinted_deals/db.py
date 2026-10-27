"""Stockage SQLite : recherches surveillées + annonces vues (sert aussi d'historique de prix)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    search_text TEXT DEFAULT '',
    brands TEXT DEFAULT '',
    price_min REAL,
    price_max REAL,
    status_ids TEXT DEFAULT '',
    sizes TEXT DEFAULT '',
    rare_keywords TEXT DEFAULT '',
    exclude_keywords TEXT DEFAULT '',
    ref_price REAL,
    min_discount REAL DEFAULT 0.3,
    min_score REAL DEFAULT 55,
    active INTEGER DEFAULT 1,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vinted_id INTEGER NOT NULL,
    watch_id INTEGER NOT NULL,
    title TEXT, brand TEXT, size TEXT, status TEXT,
    price REAL, total_price REAL, currency TEXT,
    url TEXT, photo TEXT, favourites INTEGER, seller TEXT,
    published_at REAL, seen_at REAL,
    matches INTEGER, score REAL, discount REAL, ref_price REAL,
    is_deal INTEGER, reasons TEXT,
    UNIQUE (vinted_id, watch_id)
);
CREATE INDEX IF NOT EXISTS idx_items_watch ON items (watch_id, seen_at);
"""

WATCH_FIELDS = [
    "name", "search_text", "brands", "price_min", "price_max", "status_ids", "sizes",
    "rare_keywords", "exclude_keywords", "ref_price", "min_discount", "min_score", "active",
]


class DB:
    def __init__(self, path: str = "vinted_deals.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    # ---------- recherches surveillées ----------
    def list_watches(self, active_only: bool = False) -> list[dict]:
        q = "SELECT * FROM watches" + (" WHERE active = 1" if active_only else "") + " ORDER BY id"
        with self.lock:
            return [dict(r) for r in self.conn.execute(q)]

    def get_watch(self, watch_id: int) -> dict | None:
        with self.lock:
            r = self.conn.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()
        return dict(r) if r else None

    def add_watch(self, data: dict) -> int:
        fields = [f for f in WATCH_FIELDS if f in data]
        with self.lock:
            cur = self.conn.execute(
                f"INSERT INTO watches ({', '.join(fields)}, created_at) "
                f"VALUES ({', '.join('?' for _ in fields)}, ?)",
                [data[f] for f in fields] + [time.time()],
            )
            self.conn.commit()
            return cur.lastrowid

    def update_watch(self, watch_id: int, data: dict) -> None:
        fields = [f for f in WATCH_FIELDS if f in data]
        if not fields:
            return
        with self.lock:
            self.conn.execute(
                f"UPDATE watches SET {', '.join(f + ' = ?' for f in fields)} WHERE id = ?",
                [data[f] for f in fields] + [watch_id],
            )
            self.conn.commit()

    def delete_watch(self, watch_id: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM items WHERE watch_id = ?", (watch_id,))
            self.conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
            self.conn.commit()

    # ---------- annonces ----------
    def known_ids(self, watch_id: int) -> set[int]:
        with self.lock:
            rows = self.conn.execute("SELECT vinted_id FROM items WHERE watch_id = ?", (watch_id,))
            return {r[0] for r in rows}

    def price_samples(self, watch_id: int, days: int = 30, limit: int = 500) -> list[float]:
        since = time.time() - days * 86400
        with self.lock:
            rows = self.conn.execute(
                "SELECT price FROM items WHERE watch_id = ? AND seen_at > ? "
                "ORDER BY seen_at DESC LIMIT ?", (watch_id, since, limit))
            return [r[0] for r in rows]

    def insert_item(self, row: dict) -> None:
        row = dict(row)
        row["reasons"] = json.dumps(row.get("reasons", []), ensure_ascii=False)
        cols = list(row)
        with self.lock:
            self.conn.execute(
                f"INSERT OR IGNORE INTO items ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' for _ in cols)})", [row[c] for c in cols])
            self.conn.commit()

    def feed(self, after_id: int = 0, only_deals: bool = True,
             watch_id: int | None = None, limit: int = 150) -> list[dict]:
        q = ("SELECT items.*, watches.name AS watch_name FROM items "
             "JOIN watches ON watches.id = items.watch_id "
             "WHERE items.matches = 1 AND items.id > ?")
        args: list = [after_id]
        if only_deals:
            q += " AND items.is_deal = 1"
        if watch_id:
            q += " AND items.watch_id = ?"
            args.append(watch_id)
        q += " ORDER BY items.id DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            out = [dict(r) for r in self.conn.execute(q, args)]
        for r in out:
            r["reasons"] = json.loads(r["reasons"] or "[]")
        return out

    def counts(self) -> dict:
        with self.lock:
            seen = self.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            deals = self.conn.execute(
                "SELECT COUNT(*) FROM items WHERE is_deal = 1 AND matches = 1").fetchone()[0]
        return {"seen": seen, "deals": deals}
