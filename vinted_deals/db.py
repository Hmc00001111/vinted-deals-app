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
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL, ext_id TEXT NOT NULL,
    title TEXT, brand TEXT, brand_norm TEXT, itype TEXT, model TEXT, color TEXT,
    size TEXT, status TEXT, status_id INTEGER,
    price REAL, first_price REAL, total_price REAL, currency TEXT,
    url TEXT, photo TEXT, photos TEXT, seller TEXT,
    favourites INTEGER, first_favourites INTEGER, views INTEGER,
    published_at REAL, first_seen REAL, last_seen REAL, last_active REAL,
    last_check REAL, next_check REAL, checks INTEGER DEFAULT 0,
    state TEXT DEFAULT 'active', sold_at REAL, price_drops INTEGER DEFAULT 0, origin TEXT,
    missing INTEGER DEFAULT 0, sold_how TEXT, seen_by_check INTEGER DEFAULT 0,
    value REAL, value_low REAL, value_high REAL, value_n INTEGER, value_level TEXT,
    confidence REAL, comps TEXT, cost REAL, profit REAL, roi REAL, p_sell REAL, days_to_sell REAL,
    gem_score REAL, flags TEXT, scored_at REAL, gem_notified INTEGER DEFAULT 0,
    ai_status TEXT, ai_data TEXT, ai_at REAL,
    UNIQUE (source, ext_id)
);
CREATE INDEX IF NOT EXISTS idx_listings_state ON listings (state, last_seen);
CREATE INDEX IF NOT EXISTS idx_listings_group ON listings (brand_norm, itype);
CREATE INDEX IF NOT EXISTS idx_listings_check ON listings (source, state, next_check);
CREATE INDEX IF NOT EXISTS idx_listings_gem ON listings (gem_score);
CREATE INDEX IF NOT EXISTS idx_listings_sold ON listings (sold_at);
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
LISTING_JSON = ("photos", "comps", "flags", "ai_data")


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
            lcols = {r[1] for r in self.conn.execute("PRAGMA table_info(listings)")}
            for col, decl in {"missing": "INTEGER DEFAULT 0", "sold_how": "TEXT",
                              "seen_by_check": "INTEGER DEFAULT 0"}.items():
                if col not in lcols:
                    self.conn.execute(f"ALTER TABLE listings ADD COLUMN {col} {decl}")
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
                CASE WHEN photo IS NULL THEN '[]' ELSE '["' || photo || '"]' END,
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
        q = ("SELECT items.*, watches.name AS watch_name, l.id AS listing_id, l.value AS value, "
             "l.value_low AS value_low, l.value_high AS value_high, l.value_n AS value_n, "
             "l.value_level AS value_level, l.profit AS profit, l.roi AS roi, l.gem_score AS gem_score, "
             "l.state AS market_state FROM items "
             "JOIN watches ON watches.id = items.watch_id "
             "LEFT JOIN listings l ON l.source = items.source AND l.ext_id = items.ext_id "
             "WHERE items.matches = 1 AND (items.updated_at > ? OR l.scored_at > ?)")
        args: list = [since, since]
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

    # ---------- marché (toutes les annonces vues, tous sites confondus) ----------
    @staticmethod
    def _enc(fields: dict) -> dict:
        fields = dict(fields)
        for k in LISTING_JSON:
            if k in fields and not isinstance(fields[k], str) and fields[k] is not None:
                fields[k] = json.dumps(fields[k], ensure_ascii=False)
        return fields

    @staticmethod
    def _dec_listing(r: dict) -> dict:
        for k in LISTING_JSON:
            try:
                r[k] = json.loads(r[k]) if r.get(k) else ([] if k != "ai_data" else None)
            except ValueError:
                r[k] = [] if k != "ai_data" else None
        return r

    def upsert_listing(self, row: dict) -> tuple[int, bool]:
        """Insère ou met à jour une annonce du marché. Renvoie (id, nouvelle ?)."""
        row = self._enc(row)
        now = row.get("last_seen") or time.time()
        with self.lock:
            cur = self.conn.execute("SELECT id, price, state FROM listings WHERE source = ? AND ext_id = ?",
                                    (row["source"], row["ext_id"])).fetchone()
            if cur:
                upd = {k: row[k] for k in ("title", "brand", "brand_norm", "itype", "model", "color", "size",
                                           "status", "status_id", "price", "total_price", "photo", "photos",
                                           "favourites", "views") if row.get(k) not in (None, "", "[]")}
                upd["last_seen"] = upd["last_active"] = now
                if cur["state"] in ("removed",):
                    upd["state"] = "active"
                if row.get("price") and cur["price"] and row["price"] < cur["price"] - 0.01:
                    self.conn.execute("UPDATE listings SET price_drops = price_drops + 1 WHERE id = ?", (cur["id"],))
                self.conn.execute(f"UPDATE listings SET {', '.join(k + ' = ?' for k in upd)} WHERE id = ?",
                                  [*upd.values(), cur["id"]])
                self.conn.commit()
                return cur["id"], False
            row.setdefault("first_seen", now)
            row.setdefault("last_seen", now)
            row.setdefault("last_active", now)
            row.setdefault("first_price", row.get("price"))
            row.setdefault("first_favourites", row.get("favourites"))
            cols = list(row)
            c = self.conn.execute(f"INSERT INTO listings ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                                  [row[k] for k in cols])
            self.conn.commit()
            return c.lastrowid, True

    def update_listing(self, lid: int, fields: dict) -> None:
        fields = self._enc(fields)
        if not fields:
            return
        with self.lock:
            self.conn.execute(f"UPDATE listings SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?",
                              [*fields.values(), lid])
            self.conn.commit()

    def get_listing(self, lid: int) -> dict | None:
        with self.lock:
            r = self.conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
        return self._dec_listing(dict(r)) if r else None

    def get_listings(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        with self.lock:
            rows = self.conn.execute(f"SELECT * FROM listings WHERE id IN ({', '.join('?' for _ in ids)})",
                                     list(ids)).fetchall()
        by_id = {r["id"]: self._dec_listing(dict(r)) for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    def listings_for_index(self, days: int = 90) -> list[dict]:
        since = time.time() - days * 86400
        with self.lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT id, source, brand_norm, itype, model, color, status_id, price, state, first_seen, "
                "last_seen, published_at, sold_at, favourites, checks FROM listings "
                "WHERE last_seen > ? OR sold_at > ?", (since, since))]

    def query_listings(self, where: str, args: list, order: str = "id DESC", limit: int = 200) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(f"SELECT * FROM listings WHERE {where} ORDER BY {order} LIMIT ?",
                                     [*args, limit]).fetchall()
        return [self._dec_listing(dict(r)) for r in rows]

    def due_checks(self, now: float, limit: int = 1) -> list[dict]:
        return self.query_listings(
            "source = 'vinted' AND state IN ('active', 'reserved') AND next_check <= ? AND first_seen > ?",
            [now, now - 30 * 86400], order="(checks > 0) DESC, favourites DESC, first_seen DESC", limit=limit)

    def market_counts(self) -> dict:
        with self.lock:
            r = self.conn.execute(
                "SELECT COUNT(*), SUM(state = 'sold'), SUM(source = 'vinted' AND checks > 0), "
                "SUM(state = 'removed') FROM listings").fetchone()
        return {"listings": r[0] or 0, "sold": r[1] or 0, "tracked": r[2] or 0, "removed": r[3] or 0}
