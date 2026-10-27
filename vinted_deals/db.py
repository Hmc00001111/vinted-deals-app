"""Stockage SQLite : recherches surveillées, annonces vues (historique de prix), réglages."""
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
    created_at REAL,
    sources TEXT DEFAULT 'vinted',
    interval INTEGER DEFAULT 60,
    categories TEXT DEFAULT '',
    ai INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL DEFAULT 'vinted',
    ext_id TEXT NOT NULL,
    watch_id INTEGER NOT NULL,
    title TEXT, brand TEXT, size TEXT, status TEXT,
    price REAL, orig_price REAL, total_price REAL, currency TEXT,
    url TEXT, photo TEXT, photos TEXT, favourites INTEGER, seller TEXT,
    published_at REAL, seen_at REAL, updated_at REAL,
    matches INTEGER, score REAL, discount REAL, ref_price REAL,
    is_deal INTEGER, reasons TEXT,
    ai_status TEXT, ai_verdict TEXT, ai_score REAL, ai_summary TEXT, ai_data TEXT,
    notified INTEGER DEFAULT 0,
    UNIQUE (source, ext_id, watch_id)
);
CREATE INDEX IF NOT EXISTS idx_items_watch ON items (watch_id, source, seen_at);
CREATE INDEX IF NOT EXISTS idx_items_updated ON items (updated_at);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

WATCH_FIELDS = [
    "name", "search_text", "brands", "price_min", "price_max", "status_ids", "sizes",
    "rare_keywords", "exclude_keywords", "ref_price", "min_discount", "min_score", "active",
    "sources", "interval", "categories", "ai",
]
WATCH_NEW_COLUMNS = {
    "sources": "TEXT DEFAULT 'vinted'", "interval": "INTEGER DEFAULT 60",
    "categories": "TEXT DEFAULT ''", "ai": "INTEGER DEFAULT 1",
}
ITEM_JSON = ("reasons", "photos", "ai_data")


class DB:
    def __init__(self, path: str = "vinted_deals.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self._migrate_v1()
            self.conn.executescript(SCHEMA)
            cols = {r[1] for r in self.conn.execute("PRAGMA table_info(watches)")}
            for col, decl in WATCH_NEW_COLUMNS.items():
                if col not in cols:
                    self.conn.execute(f"ALTER TABLE watches ADD COLUMN {col} {decl}")
            self.conn.commit()

    def _migrate_v1(self) -> None:
        """Convertit la base de la première version (Vinted seul) sans perdre l'historique."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(items)")}
        if not cols or "source" in cols:
            return
        self.conn.execute("ALTER TABLE items RENAME TO items_v1")
        self.conn.execute("DROP INDEX IF EXISTS idx_items_watch")
        self.conn.executescript(SCHEMA)
        self.conn.execute("""
            INSERT OR IGNORE INTO items (source, ext_id, watch_id, title, brand, size, status, price,
                orig_price, total_price, currency, url, photo, photos, favourites, seller, published_at,
                seen_at, updated_at, matches, score, discount, ref_price, is_deal, reasons, ai_status, notified)
            SELECT 'vinted', CAST(vinted_id AS TEXT), watch_id, title, brand, size, status, price,
                price, total_price, currency, url, photo,
                CASE WHEN photo IS NULL THEN '[]' ELSE json_array(photo) END,
                favourites, seller, published_at, seen_at, seen_at, matches, score, discount, ref_price,
                is_deal, reasons, 'skipped', 1
            FROM items_v1""")
        self.conn.execute("DROP TABLE items_v1")
        self.conn.commit()

    # ---------- réglages ----------
    def settings(self) -> dict:
        with self.lock:
            return {r[0]: json.loads(r[1]) for r in self.conn.execute("SELECT key, value FROM settings")}

    def get_setting(self, key: str, default=None):
        with self.lock:
            r = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def set_settings(self, values: dict) -> None:
        with self.lock:
            for k, v in values.items():
                self.conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                                  (k, json.dumps(v, ensure_ascii=False)))
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
    def known_ids(self, watch_id: int, source: str) -> set[str]:
        with self.lock:
            rows = self.conn.execute("SELECT ext_id FROM items WHERE watch_id = ? AND source = ?",
                                     (watch_id, source))
            return {r[0] for r in rows}

    def price_samples(self, watch_id: int, source: str | None = None,
                      days: int = 30, limit: int = 500) -> list[float]:
        since = time.time() - days * 86400
        q = "SELECT price FROM items WHERE watch_id = ? AND seen_at > ?"
        args: list = [watch_id, since]
        if source:
            q += " AND source = ?"
            args.append(source)
        q += " ORDER BY seen_at DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            return [r[0] for r in self.conn.execute(q, args)]

    def insert_item(self, row: dict) -> int | None:
        row = dict(row)
        for k in ITEM_JSON:
            if k in row and not isinstance(row[k], str):
                row[k] = json.dumps(row[k], ensure_ascii=False)
        row.setdefault("updated_at", time.time())
        cols = list(row)
        with self.lock:
            cur = self.conn.execute(
                f"INSERT OR IGNORE INTO items ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' for _ in cols)})", [row[c] for c in cols])
            self.conn.commit()
            return cur.lastrowid if cur.rowcount else None

    def update_item(self, item_id: int, fields: dict) -> None:
        fields = dict(fields)
        for k in ITEM_JSON:
            if k in fields and not isinstance(fields[k], str):
                fields[k] = json.dumps(fields[k], ensure_ascii=False)
        fields["updated_at"] = time.time()
        with self.lock:
            self.conn.execute(f"UPDATE items SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?",
                              [*fields.values(), item_id])
            self.conn.commit()

    @staticmethod
    def _decode(r: dict) -> dict:
        for k in ITEM_JSON:
            try:
                r[k] = json.loads(r[k]) if r.get(k) else ([] if k != "ai_data" else None)
            except ValueError:
                r[k] = [] if k != "ai_data" else None
        return r

    def get_item(self, item_id: int) -> dict | None:
        with self.lock:
            r = self.conn.execute("SELECT items.*, watches.name AS watch_name FROM items "
                                  "JOIN watches ON watches.id = items.watch_id WHERE items.id = ?",
                                  (item_id,)).fetchone()
        return self._decode(dict(r)) if r else None

    def items_with_ai_status(self, status: str, limit: int = 50) -> list[int]:
        with self.lock:
            return [r[0] for r in self.conn.execute(
                "SELECT id FROM items WHERE ai_status = ? ORDER BY id LIMIT ?", (status, limit))]

    def feed(self, since: float = 0, only_deals: bool = True, watch_id: int | None = None,
             source: str | None = None, limit: int = 200) -> list[dict]:
        q = ("SELECT items.*, watches.name AS watch_name FROM items "
             "JOIN watches ON watches.id = items.watch_id "
             "WHERE items.matches = 1 AND items.updated_at > ?")
        args: list = [since]
        if only_deals:
            q += " AND items.is_deal = 1"
        if watch_id:
            q += " AND items.watch_id = ?"
            args.append(watch_id)
        if source:
            q += " AND items.source = ?"
            args.append(source)
        q += " ORDER BY items.id DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            out = [dict(r) for r in self.conn.execute(q, args)]
        return [self._decode(r) for r in out]

    def counts(self) -> dict:
        with self.lock:
            seen = self.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            deals = self.conn.execute(
                "SELECT COUNT(*) FROM items WHERE is_deal = 1 AND matches = 1").fetchone()[0]
        return {"seen": seen, "deals": deals}
