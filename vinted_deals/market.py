"""Marché : toutes les annonces vues (surveillances + veille marché), leur valeur, leur demande,
le suivi des ventes sur Vinted et le score « pépite » (achat-revente)."""
from __future__ import annotations

import logging
import threading
import time

import requests

from product import fingerprint
from valuation import (DEFAULT_FLIP_SETTINGS, HISTORY_DAYS, MarketIndex, demand, flip, risk_flags,
                       value_of)
from vinted_client import VintedClient, VintedError

log = logging.getLogger("deals.market")

DEFAULT_MARKET_SETTINGS = {
    "market_terms": "",          # marques / mots-clés scannés en continu pour nourrir l'analyste
    "market_interval": 600,      # secondes entre deux scans d'un même terme
    "tracker_enabled": True,     # suivi des ventes (re-vérification des annonces Vinted)
    "tracker_per_hour": 120,     # vérifications par heure (données mobiles : rester raisonnable)
    "gems_notify_score": 75,     # alerte pépite à partir de ce score (0 = jamais)
    "gems_auto_ai": True,        # faire identifier automatiquement les meilleures pépites par Gemini
    "gems_ai_score": 65,
    **DEFAULT_FLIP_SETTINGS,
}

POPULAR_TERMS = ("Carhartt, Ralph Lauren, Lacoste, Stone Island, The North Face, Arc'teryx, Patagonia, "
                 "Levi's, Nike, Adidas, New Balance, Stüssy, Burberry, Tommy Hilfiger, Dr. Martens, "
                 "Salomon, Moncler, C.P. Company, Barbour, Polo Ralph Lauren cachemire")

CHECK_SCHEDULE_H = [3, 8, 24, 48, 72]  # délai avant la 1re, 2e… vérification, puis toutes les 72 h
AI_CONF = {"haute": 0.6, "moyenne": 0.35, "faible": 0.15}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class Market:
    def __init__(self, db):
        self.db = db
        self.idx = MarketIndex()
        self.idx.load(db.listings_for_index(HISTORY_DAYS))
        self.notify = None        # fonction(listing) : alerte pépite
        self.ai_enqueue = None    # fonction(listing_id) : identification Gemini
        self.ai_ready = lambda: False

    def settings(self) -> dict:
        s = dict(DEFAULT_MARKET_SETTINGS)
        s.update({k: v for k, v in self.db.settings().items() if k in DEFAULT_MARKET_SETTINGS})
        return s

    # ---------- entrée d'une annonce ----------
    def ingest(self, it, origin: str, notify: bool = True) -> dict | None:
        """notify=False : pas d'alerte pépite maintenant (l'appelant décide, cf. _after_new)."""
        try:
            fp = fingerprint(it.title, it.brand, getattr(it, "category", ""), it.status_id,
                             self.idx.known_brands())
            now = time.time()
            row = {
                "source": it.source, "ext_id": str(it.id), "title": it.title, "brand": it.brand,
                "brand_norm": fp["brand_norm"], "itype": fp["itype"], "model": " ".join(fp["model"]),
                "color": fp["color"], "size": it.size, "status": it.status, "status_id": it.status_id,
                "price": it.price, "total_price": it.total_price, "currency": it.currency, "url": it.url,
                "photo": it.photo, "photos": (it.photos or [])[:6], "seller": it.seller,
                "favourites": it.favourites, "views": it.views, "published_at": it.published_at,
                "last_seen": now, "origin": origin,
            }
            if it.source == "vinted":
                row["next_check"] = now + CHECK_SCHEDULE_H[0] * 3600
            lid, is_new = self.db.upsert_listing(row)
            listing = self.db.get_listing(lid)
            self.idx.upsert(listing)
            listing.update(self.rescore(listing))
            listing["_is_new"] = is_new
            if is_new and notify:
                self._after_new(listing)
            return listing
        except Exception:
            log.exception("Marché : annonce %s ignorée", getattr(it, "id", "?"))
            return None

    def _after_new(self, l: dict, alert: bool = True) -> None:
        s = self.settings()
        score = l.get("gem_score") or 0
        if s.get("gems_auto_ai") and self.ai_enqueue and self.ai_ready() and not l.get("ai_status") \
                and score >= float(s.get("gems_ai_score") or 65):
            self.db.update_listing(l["id"], {"ai_status": "pending"})
            self.ai_enqueue(l["id"])
        thr = float(s.get("gems_notify_score") or 0)
        if not alert:
            if thr and score >= thr:
                self.db.update_listing(l["id"], {"gem_notified": 1})
            return
        if thr and score >= thr and not l.get("gem_notified") and self.notify:
            self.db.update_listing(l["id"], {"gem_notified": 1})
            try:
                self.notify(l)
            except Exception as e:
                log.warning("Alerte pépite : %s", e)

    # ---------- évaluation ----------
    def assess(self, l: dict, settings: dict | None = None) -> dict:
        s = settings or self.settings()
        model = (l.get("model") or "").split()
        val = value_of(self.idx, l.get("brand_norm") or "", l.get("itype") or "", model, l.get("status_id"),
                       l.get("color") or "", exclude_id=l.get("id"))
        ai = l.get("ai_data") or {}
        ai_checked = False
        if ai and l.get("ai_status") == "done":
            occ = ai.get("prix_occasion_eur") or {}
            g = _num(occ.get("median")) if isinstance(occ, dict) else _num(occ)
            kg = AI_CONF.get(str(ai.get("confiance") or "").lower(), 0.25)
            if g and g > 0:
                g_low = _num(occ.get("bas")) if isinstance(occ, dict) else None
                g_high = _num(occ.get("haut")) if isinstance(occ, dict) else None
                if not val:
                    val = {"value": g, "low": g_low or g * 0.8, "high": g_high or g * 1.2, "n": 0, "n_eff": 0,
                           "n_sold": 0, "level": "gemini", "confidence": round(kg * 0.8, 3), "comps": []}
                else:
                    k = val["confidence"]
                    val = dict(val, value=round((k * val["value"] + kg * g) / (k + kg), 2),
                               confidence=round(min(1.0, k + kg * 0.5), 3), gemini=g)
            ai_checked = str(ai.get("authenticite") or "") == "probable"
        dem = demand(self.idx, l.get("brand_norm") or "", l.get("itype") or "", model)
        flags = risk_flags(l.get("price") or 0, l.get("brand_norm") or "", l.get("status_id"),
                           bool(l.get("photo")), val, ai_checked)
        if ai and str(ai.get("authenticite") or "") == "suspect":
            flags.append("suspect_ia")
        fl = flip(l.get("price") or 0, l.get("total_price"), val, dem, s, flags)
        if "suspect_ia" in flags:
            fl["gem_score"] = round(fl["gem_score"] * 0.3, 1)
        return {"val": val, "dem": dem, "flags": flags, "flip": fl}

    def rescore(self, l: dict, settings: dict | None = None) -> dict:
        a = self.assess(l, settings)
        val, dem, fl = a["val"], a["dem"], a["flip"]
        fields = {
            "value": val["value"] if val else None, "value_low": val["low"] if val else None,
            "value_high": val["high"] if val else None, "value_n": val["n"] if val else 0,
            "value_level": val["level"] if val else None, "confidence": val["confidence"] if val else 0,
            "comps": val["comps"] if val else [], "cost": fl["cost"], "profit": fl.get("profit"),
            "roi": fl.get("roi"), "p_sell": dem["p_sell"], "days_to_sell": dem["days"],
            "gem_score": fl["gem_score"], "flags": a["flags"],
        }
        old_v, old_g = l.get("value") or 0, l.get("gem_score") or 0
        if abs((fields["value"] or 0) - old_v) > 0.01 * max(1, old_v) or abs(fields["gem_score"] - old_g) >= 1 \
                or not l.get("scored_at"):
            fields["scored_at"] = time.time()
        self.db.update_listing(l["id"], fields)
        return fields

    def rescore_recent(self, hours: int = 72, limit: int = 3000) -> int:
        s = self.settings()
        rows = self.db.query_listings("state IN ('active', 'reserved') AND last_seen > ?",
                                      [time.time() - hours * 3600], order="last_seen DESC", limit=limit)
        for l in rows:
            self.rescore(l, s)
        return len(rows)

    def comparables(self, lid: int) -> dict | None:
        l = self.db.get_listing(lid)
        if not l:
            return None
        a = self.assess(l)
        comps = self.db.get_listings((a["val"] or {}).get("comps") or [])
        return {"listing": l, "valuation": a["val"], "demand": a["dem"], "flags": a["flags"], "flip": a["flip"],
                "comparables": comps}

    # ---------- suivi d'une vérification ----------
    def apply_check(self, l: dict, st: dict | None) -> None:
        now = time.time()
        checks = int(l.get("checks") or 0) + 1
        delay = CHECK_SCHEDULE_H[min(checks, len(CHECK_SCHEDULE_H) - 1)]
        fields: dict = {"checks": checks, "last_check": now, "next_check": now + delay * 3600}
        if st:
            state = st["state"]
            if st.get("favourites") is not None:
                fields["favourites"] = st["favourites"]
            if st.get("price") and l.get("price") and st["price"] < l["price"] - 0.01:
                fields["price_drops"] = int(l.get("price_drops") or 0) + 1
            if st.get("price"):
                fields["price"] = st["price"]
            last = l.get("last_active") or l.get("last_seen") or now
            if state in ("active", "reserved"):
                fields.update(state=state, last_active=now, missing=0)
            elif state == "sold":
                fields.update(state="sold", sold_at=(last + now) / 2, next_check=None, sold_how="statut")
            elif state == "missing":
                missing = int(l.get("missing") or 0) + 1
                fields["missing"] = missing
                if missing >= 2:  # absente deux fois de suite de la recherche : considérée vendue
                    fields.update(state="sold", sold_at=(last + now) / 2, next_check=None, sold_how="disparition")
                else:
                    fields["next_check"] = now + 3600  # on confirme dans une heure
            elif state == "removed":
                fields.update(state="removed", next_check=None)
        self.db.update_listing(l["id"], fields)
        fresh = self.db.get_listing(l["id"])
        if fresh:
            self.idx.upsert(fresh)


class SaleTracker(threading.Thread):
    """Re-vérifie régulièrement les annonces Vinted vues pour savoir lesquelles se vendent, et à quelle vitesse."""

    def __init__(self, market: Market, domain: str = "www.vinted.fr"):
        super().__init__(daemon=True, name="tracker")
        self.market = market
        self.client = VintedClient(domain, min_delay=2.5)
        self.status = {"checked": 0, "sold": 0, "unknown": 0, "last_error": None, "route": None,
                       "paused_until": None, "last_check": None}
        self._last_rescore = 0.0
        self._last_prune = time.time()
        self._unknown_streak = 0

    def run(self) -> None:
        time.sleep(20)
        while True:
            try:
                self._tick()
            except Exception as e:
                log.exception("Suivi des ventes")
                self.status["last_error"] = f"Erreur inattendue : {e}"
                time.sleep(30)

    def _tick(self) -> None:
        m = self.market
        now = time.time()
        if now - self._last_rescore > 600:
            self._last_rescore = now
            m.rescore_recent()
        if now - self._last_prune > 86400:
            self._last_prune = now
            m.idx.prune()
        s = m.settings()
        if not s.get("tracker_enabled") or (self.status["paused_until"] or 0) > now:
            time.sleep(15)
            return
        due = m.db.due_checks(now, 1)
        if not due:
            time.sleep(20)
            return
        l = due[0]
        try:
            if self.client.item_route is None:
                ref = m.db.query_listings("source = 'vinted' AND state = 'active'", [], order="last_seen DESC", limit=1)
                self.client.discover_item_route(ref[0]["ext_id"] if ref else l["ext_id"])
                self.status["route"] = self.client.item_route
            st = self.client.item_state(l["ext_id"], l.get("title") or "", l.get("price"))
        except VintedError as e:
            if self.client.item_route is None:  # page bloquée : nouvelle méthode au prochain tour
                self.status["last_error"] = f"Vinted bloque la page des annonces ({e}) : changement de méthode"
                return
            self.status.update(last_error=f"Vinted bloque la vérification ({e}) : pause 10 min",
                               paused_until=now + 600)
            return
        except requests.RequestException as e:
            self.status["last_error"] = f"Connexion : {e.__class__.__name__}"
            time.sleep(60)
            return
        if st is None:
            self.status["unknown"] += 1
            self._unknown_streak += 1
            if self._unknown_streak >= 5 and self.client.item_route != "html":
                self.client.item_route = None  # la route a peut-être changé : on la redécouvre
                self._unknown_streak = 0
        else:
            self._unknown_streak = 0
            if st["state"] == "sold":
                self.status["sold"] += 1
        m.apply_check(l, st)
        self.status.update(checked=self.status["checked"] + 1, last_check=time.time(), last_error=None)
        per_hour = max(10, min(1200, int(float(s.get("tracker_per_hour") or 120))))
        time.sleep(max(0.0, 3600 / per_hour - 2.5))
