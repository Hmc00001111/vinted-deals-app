"""Boucle de surveillance : interroge Vinted en continu et note chaque nouvelle annonce."""
from __future__ import annotations

import logging
import os
import random
import threading
import time

import requests

from db import DB
from scoring import brand_ok, evaluate, excluded_by, matches_filters, parse_list
from vinted_client import Item, VintedClient, VintedError

log = logging.getLogger("vinted.monitor")


# ---------- notifications externes (optionnelles) ----------
# Fonctions supplémentaires appelées pour chaque bonne affaire (ex. notification Android).
NOTIFY_HOOKS: list = []


def notify(item: Item, watch: dict, verdict) -> None:
    for hook in NOTIFY_HOOKS:
        try:
            hook(item, watch, verdict)
        except Exception as e:
            log.warning("Notification : %s", e)
    text =(f"🔥 {watch['name']} — {item.title}\n"
            f"{item.price:.2f} € | {item.brand} | {item.status} | {item.size}\n"
            f"Score {verdict.score:.0f}/100 — " + " · ".join(verdict.reasons[:2]) +
            f"\n{item.url}")
    discord = os.getenv("DISCORD_WEBHOOK_URL")
    if discord:
        try:
            requests.post(discord, json={"content": text[:1900]}, timeout=10)
        except requests.RequestException as e:
            log.warning("Discord : %s", e)
    tg_token, tg_chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat:
        try:
            requests.post(f"https://api.telegram.org/bot{tg_token}/sendMessage",
                          json={"chat_id": tg_chat, "text": text}, timeout=10)
        except requests.RequestException as e:
            log.warning("Telegram : %s", e)


class Monitor(threading.Thread):
    def __init__(self, db: DB, client: VintedClient, interval: int = 60,
                 first_scan_pages: int = 3):
        super().__init__(daemon=True)
        self.db = db
        self.client = client
        self.interval = interval
        self.first_scan_pages = first_scan_pages
        self.wake = threading.Event()
        self.stop_flag = threading.Event()
        self.status = {"running": True, "last_cycle": None, "last_error": None,
                       "cycles": 0, "scanning": None}

    def run(self) -> None:
        while not self.stop_flag.is_set():
            watches = self.db.list_watches(active_only=True)
            for w in watches:
                if self.stop_flag.is_set():
                    break
                self.status["scanning"] = w["name"]
                try:
                    self.scan_watch(w)
                    self.status["last_error"] = None
                except VintedError as e:
                    self.status["last_error"] = str(e)
                    log.error("[%s] %s", w["name"], e)
                except Exception as e:
                    self.status["last_error"] = f"Erreur inattendue : {e}"
                    log.exception("[%s] erreur", w["name"])
            self.status.update(scanning=None, last_cycle=time.time(),
                               cycles=self.status["cycles"] + 1)
            self.wake.wait(self.interval + random.uniform(0, self.interval * 0.3))
            self.wake.clear()

    def scan_now(self) -> None:
        self.wake.set()

    def scan_watch(self, watch: dict) -> int:
        """Retourne le nombre de bonnes affaires trouvées."""
        known = self.db.known_ids(watch["id"])
        first_scan = not known
        brands = [b.strip() for b in (watch.get("brands") or "").split(",") if b.strip()]
        queries = [f"{b} {watch.get('search_text') or ''}".strip() for b in brands] \
            or [(watch.get("search_text") or "").strip()]
        pages = self.first_scan_pages if first_scan else 1

        deals = 0
        for q in queries:
            for page in range(1, pages + 1):
                # Pas de prix max côté Vinted : les annonces chères servent au prix de référence.
                items = self.client.search(q, page=page)
                if not items:
                    break
                # Du plus ancien au plus récent pour que la référence se construise dans l'ordre
                for it in reversed(items):
                    if it.id in known or not brand_ok(it, watch):
                        continue
                    known.add(it.id)
                    if excluded_by(it, watch):
                        continue
                    samples = self.db.price_samples(watch["id"])
                    verdict = evaluate(it, watch, samples)
                    match = matches_filters(it, watch)
                    is_deal = verdict.is_deal and match
                    self.db.insert_item({
                        "vinted_id": it.id, "watch_id": watch["id"], "title": it.title,
                        "brand": it.brand, "size": it.size, "status": it.status,
                        "price": it.price, "total_price": it.total_price,
                        "currency": it.currency, "url": it.url, "photo": it.photo,
                        "favourites": it.favourites, "seller": it.seller,
                        "published_at": it.published_at, "seen_at": time.time(),
                        "matches": int(match), "score": verdict.score,
                        "discount": verdict.discount, "ref_price": verdict.ref_price,
                        "is_deal": int(is_deal),
                        "reasons": verdict.reasons,
                    })
                    if is_deal and not first_scan:
                        deals += 1
                        log.info("BONNE AFFAIRE [%s] %s — %.2f € (score %s)",
                                 watch["name"], it.title, it.price, verdict.score)
                        notify(it, watch, verdict)
        return deals
