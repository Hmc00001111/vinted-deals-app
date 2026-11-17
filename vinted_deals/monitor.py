"""Boucle de surveillance : interroge les sites cochés, à la fréquence propre à chaque recherche,
note chaque nouvelle annonce, puis fait analyser les bonnes affaires par Gemini avant d'alerter."""
from __future__ import annotations

import logging
import os
import random
import threading
import time

import requests

from ai import DEFAULT_SETTINGS, VERDICTS, Analyzer
from categories import parse_categories
from db import DB
from brands import BrandCatalog
from market import Market, SaleTracker
from robot import Robot
from scoring import MIN_SAMPLES, brand_ok, evaluate, excluded_by, matches_filters
from sources import SOURCES, Blocked, SourceError
from sources.base import FX_TO_EUR

log = logging.getLogger("deals.monitor")

MIN_INTERVAL = 10  # secondes (moins de 30 s : réservé à 1 ou 2 recherches prioritaires, risque de blocage)

# Fonctions supplémentaires appelées pour chaque alerte : hook(row, ai_result, priority)
NOTIFY_HOOKS: list = []
# Notifications du robot : hook(action_id, titre, texte, url, priorité)
ROBOT_HOOKS: list = []

VERDICT_EMOJI = {"acheter": "✅", "a_verifier": "🔍", "eviter": "⛔"}


def alert_text(row: dict, ai: dict | None) -> str:
    head = f"{'🔥' if row.get('score', 0) >= 80 else '💡'} {row.get('watch_name', '')} — {row.get('title')}"
    line2 = f"{row.get('price', 0):.2f} € | {row.get('source_label') or row.get('source')} | " + \
            " | ".join(x for x in (row.get("brand"), row.get("status"), row.get("size")) if x)
    line3 = f"Score {row.get('score', 0):.0f}/100 — " + " · ".join((row.get("reasons") or [])[:2])
    parts = [head, line2, line3]
    if ai:
        v = ai.get("verdict")
        parts.append(f"Gemini {VERDICT_EMOJI.get(v, '')} {VERDICTS.get(v, v)}"
                     + (f" ({ai['note']:.0f}/10)" if isinstance(ai.get("note"), (int, float)) else "")
                     + (f" : {ai.get('resume')}" if ai.get("resume") else ""))
    parts.append(row.get("url", ""))
    return "\n".join(parts)


def notify(row: dict, ai: dict | None = None, priority: bool = False) -> None:
    for hook in NOTIFY_HOOKS:
        try:
            hook(row, ai, priority)
        except Exception as e:
            log.warning("Notification : %s", e)
    text = alert_text(row, ai)
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


def refresh_fx() -> None:
    """Met à jour les taux de change (BCE) ; en cas d'échec on garde les valeurs par défaut."""
    try:
        r = requests.get("https://api.frankfurter.app/latest", params={"from": "EUR"}, timeout=10)
        for cur, rate in (r.json().get("rates") or {}).items():
            if rate:
                FX_TO_EUR[cur] = 1 / float(rate)
    except Exception:
        pass


def split_list(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


class Monitor(threading.Thread):
    def __init__(self, db: DB, interval: int = 60, domain: str = "www.vinted.fr"):
        super().__init__(daemon=True, name="monitor")
        self.db = db
        self.default_interval = max(MIN_INTERVAL, int(interval))
        self.interval = self.default_interval  # compatibilité avec l'ancienne interface
        self.domain = domain
        self.clients: dict = {}
        self.next_run: dict[int, float] = {}
        self.wake = threading.Event()
        self.stop_flag = threading.Event()
        self.lock = threading.Lock()
        self.status: dict = {"running": True, "last_cycle": None, "last_error": None, "cycles": 0,
                             "scanning": None, "sources": {}}
        self.ai = Analyzer(db, self._ai_done)
        self.market = Market(db)
        self.market.notify = self._gem_alert
        self.market.ai_enqueue = lambda lid: self.ai.enqueue(("identify", lid))
        self.market.ai_ready = self.ai.ready
        self.ai.on_identified = self._identified
        self.tracker = SaleTracker(self.market, domain)
        self.brands = BrandCatalog(db)
        self.market.brands = self.brands
        self.robot = Robot(db, self.market)
        self.robot.notify = self._robot_notify
        self.market.on_new.append(self.robot.consider)
        self.market_next: dict[str, float] = {}
        self.status["market"] = {"scanning": None, "last": None, "error": None}

    # ---------- cycle de vie ----------
    def start(self) -> None:
        threading.Thread(target=refresh_fx, daemon=True).start()
        self.ai.start()
        self.tracker.start()
        super().start()

    def client(self, key: str):
        if key not in self.clients:
            cls = SOURCES[key]
            self.clients[key] = cls(self.domain) if key == "vinted" else cls()
        return self.clients[key]

    def run(self) -> None:
        while not self.stop_flag.is_set():
            now = time.time()
            due = [w for w in self.db.list_watches(active_only=True)
                   if self.next_run.get(w["id"], 0) <= now]
            for w in due:
                if self.stop_flag.is_set():
                    break
                self.status["scanning"] = w["name"]
                try:
                    self.scan_watch(w)
                except Exception as e:
                    log.exception("[%s] erreur", w["name"])
                    self.status["last_error"] = f"Erreur inattendue : {e}"
                iv = max(MIN_INTERVAL, int(w.get("interval") or self.default_interval))
                self.next_run[w["id"]] = time.time() + iv * random.uniform(1.0, 1.15)
            if due:
                self.status.update(last_cycle=time.time(), cycles=self.status["cycles"] + 1)
            self.status["scanning"] = None
            try:
                self._market_cycle()
            except Exception as e:
                log.exception("Veille marché")
                self.status["market"]["error"] = f"Erreur inattendue : {e}"
            self.wake.wait(2)
            self.wake.clear()

    def scan_now(self, watch_id: int | None = None) -> None:
        if watch_id:
            self.next_run[watch_id] = 0
        else:
            self.next_run.clear()
        self.wake.set()

    def next_runs(self) -> dict:
        return {str(k): v for k, v in self.next_run.items()}

    # ---------- scan ----------
    def scan_watch(self, watch: dict) -> int:
        keys = [k for k in split_list(watch.get("sources")) if k in SOURCES] or ["vinted"]
        brands = split_list(watch.get("brands"))
        text = (watch.get("search_text") or "").strip()
        queries = [f"{b} {text}".strip() for b in brands] or [text]
        cats = parse_categories(watch.get("categories"))
        native_cat = cats[0] if len(cats) == 1 else None
        deals, errors = 0, 0
        for key in keys:
            st = self.status["sources"].setdefault(key, {})
            st["scanning"] = True
            try:
                found, d = self._scan_source(watch, key, queries, native_cat)
                deals += d
                st.update(ok=True, blocked=False, error=None, last=time.time(), last_count=found)
            except Blocked as e:
                errors += 1
                st.update(ok=False, blocked=True, error=str(e), last=time.time())
                self.client(key).reset()
                log.warning("[%s] %s", watch["name"], e)
            except SourceError as e:
                errors += 1
                st.update(ok=False, blocked=False, error=str(e), last=time.time())
                log.warning("[%s] %s", watch["name"], e)
            except Exception as e:
                errors += 1
                st.update(ok=False, blocked=False, error=f"Erreur inattendue : {e}", last=time.time())
                log.exception("[%s] %s", watch["name"], key)
            finally:
                st["scanning"] = False
        self.status["last_error"] = None if not errors else f"{errors} site(s) en erreur"
        return deals

    def _scan_source(self, watch: dict, key: str, queries: list[str], native_cat: str | None) -> tuple[int, int]:
        client = self.client(key)
        wid = watch["id"]
        known = self.db.known_ids(wid, key)
        first_scan = not known
        pages = client.first_scan_pages if first_scan else 1
        found = deals = 0
        for q in queries:
            for page in range(1, pages + 1):
                items = client.search(q, page=page, category=native_cat)
                if not items:
                    break
                found += len(items)
                # du plus ancien au plus récent pour que la référence de prix se construise dans l'ordre
                for it in reversed(items):
                    if it.id in known or not brand_ok(it, watch):
                        continue
                    known.add(it.id)
                    if excluded_by(it, watch):
                        continue
                    listing = self.market.ingest(it, "watch", notify=False)
                    samples = self.db.price_samples(wid, key)
                    label = f" sur {client.label}"
                    if len(samples) < MIN_SAMPLES:
                        samples, label = self.db.price_samples(wid), " (tous sites)"
                    verdict = evaluate(it, watch, samples, label, listing)
                    match = matches_filters(it, watch)
                    is_deal = verdict.is_deal and match
                    alert = is_deal and not first_scan
                    use_ai = alert and bool(watch.get("ai", 1)) and self.ai.ready()
                    row_id = self.db.insert_item({
                        "source": key, "ext_id": it.id, "watch_id": wid, "title": it.title,
                        "brand": it.brand, "size": it.size, "status": it.status,
                        "price": it.price, "orig_price": it.orig_price, "total_price": it.total_price,
                        "currency": it.currency, "url": it.url, "photo": it.photo,
                        "photos": it.photos[:6], "favourites": it.favourites, "seller": it.seller,
                        "published_at": it.published_at, "seen_at": time.time(),
                        "matches": int(match), "score": verdict.score,
                        "discount": verdict.discount, "ref_price": verdict.ref_price,
                        "is_deal": int(is_deal), "reasons": verdict.reasons,
                        "ai_status": "pending" if use_ai else ("skipped" if is_deal else None),
                        "notified": 0 if alert else 1,
                    })
                    if listing and listing.get("_is_new"):
                        # pépite : alerte seulement si la surveillance n'alerte pas déjà (et pas au 1er scan)
                        self.market._after_new(listing, alert=not alert and not first_scan, robot=not first_scan)
                    if alert and row_id:
                        deals += 1
                        log.info("BONNE AFFAIRE [%s/%s] %s — %.2f € (score %s)",
                                 watch["name"], key, it.title, it.price, verdict.score)
                        if use_ai:
                            self.ai.enqueue(row_id)
                        else:
                            self._alert(row_id, None)
        return found, deals

    # ---------- veille marché (nourrit l'analyste et les pépites) ----------
    def _market_cycle(self) -> None:
        s = self.market.settings()
        terms = split_list(s.get("market_terms"))
        if not terms:
            return
        now = time.time()
        iv = max(120, int(float(s.get("market_interval") or 600)))
        for term in terms:
            if self.stop_flag.is_set() or self.market_next.get(term, 0) > now:
                continue
            if any(self.next_run.get(w["id"], 0) <= time.time() for w in self.db.list_watches(active_only=True)):
                return  # les surveillances passent en priorité
            first = term not in self.market_next
            self.status["market"]["scanning"] = term
            try:
                client = self.client("vinted")
                for page in range(1, (3 if first else 1) + 1):
                    items = client.search(term, page=page)
                    for it in reversed(items or []):
                        self.market.ingest(it, "market", notify=not first)  # 1er passage : pas d'alerte
                    if not items:
                        break
                self.status["market"].update(last=time.time(), error=None)
            except (Blocked, SourceError) as e:
                self.status["market"]["error"] = str(e)
                self.client("vinted").reset()
            finally:
                self.status["market"]["scanning"] = None
                self.market_next[term] = time.time() + iv * random.uniform(1.0, 1.2)

    def _gem_alert(self, l: dict) -> None:
        if l.get("state") not in (None, "active"):
            return
        val = l.get("value") or 0
        row = dict(l)
        row.update(
            id=1_000_000 + int(l["id"]), watch_name="💎 Pépite",
            source_label=SOURCES[l["source"]].label if l["source"] in SOURCES else l["source"],
            score=l.get("gem_score") or 0,
            discount=(1 - l["price"] / val) if val else None,
            reasons=[f"Revente estimée {val:.0f} € → +{(l.get('profit') or 0):.0f} € net "
                     f"({(l.get('roi') or 0):.0%})",
                     f"Se revend en ~{str(l.get('days_to_sell') or '?').replace('.', ',')} j · "
                     f"{(l.get('p_sell') or 0):.0%} de chances"],
        )
        notify(row, None, priority=(l.get("gem_score") or 0) >= 85)

    def _robot_notify(self, title: str, text: str, url: str, priority: bool, aid: int) -> None:
        for hook in ROBOT_HOOKS:
            try:
                hook(aid, title, text, url, priority)
            except Exception as e:
                log.warning("Notification robot : %s", e)
        notify({"title": title, "url": url, "price": 0, "reasons": [text], "watch_name": "Robot"}, None, False) \
            if not ROBOT_HOOKS else None

    def _identified(self, lid: int) -> None:
        l = self.db.get_listing(lid)
        if l:
            self.market.rescore(l)

    # ---------- alertes ----------
    def _ai_done(self, item_id: int, result: dict | None, error: str | None) -> None:
        self._alert(item_id, result)

    def _alert(self, item_id: int, ai: dict | None) -> None:
        with self.lock:
            row = self.db.get_item(item_id)
            if not row or row.get("notified"):
                return
            self.db.update_item(item_id, {"notified": 1})
        settings = dict(DEFAULT_SETTINGS)
        settings.update(self.db.settings())
        if ai and ai.get("verdict") == "eviter" and settings.get("notify_mode") == "ai_ok":
            return  # Gemini déconseille : pas d'alerte, l'annonce reste visible dans la liste
        row["source_label"] = SOURCES[row["source"]].label if row["source"] in SOURCES else row["source"]
        note = ai.get("note") if ai else None
        priority = bool(ai and ai.get("verdict") == "acheter" and
                        ((row.get("score") or 0) >= float(settings.get("priority_score") or 85)
                         or (isinstance(note, (int, float)) and note >= 8)))
        notify(row, ai, priority)
