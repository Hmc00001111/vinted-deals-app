"""Robot d'achat : prix d'offre conseillé, règles d'achat automatique, budgets, file d'actions
exécutées par l'appli Android (navigateur intégré connecté à ton compte Vinted), suivi du stock
(achats → reventes) et des bénéfices réels.

Modes :
  off     : le robot ne fait rien tout seul (tu peux toujours lancer une offre / un achat à la main)
  offers  : il envoie lui-même des offres sur les pépites qui passent tes règles
  auto    : il achète lui-même (ou fait une offre si le prix est trop haut) — paiement validé
            par toi (par défaut) ou automatiquement si tu l'autorises
"""
from __future__ import annotations

import json
import random
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER, source TEXT, ext_id TEXT, url TEXT, title TEXT, photo TEXT,
    kind TEXT NOT NULL,            -- offer | buy | message
    amount REAL, price REAL, message TEXT,
    origin TEXT,                   -- robot | user
    status TEXT DEFAULT 'pending', -- pending | running | sent | checkout | paid | accepted | refused | failed | cancelled
    detail TEXT, debug TEXT, checkout_url TEXT,
    created_at REAL, updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_actions_status ON actions (status, created_at);
CREATE TABLE IF NOT EXISTS portfolio (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER, title TEXT, brand TEXT, itype TEXT, photo TEXT, url TEXT,
    buy_price REAL, buy_cost REAL, bought_at REAL, est_value REAL,
    status TEXT DEFAULT 'achete',  -- achete | recu | en_vente | vendu | retourne
    sale_price REAL, sale_fees REAL, sold_at REAL, notes TEXT
);
"""

DEFAULT_ROBOT_SETTINGS = {
    "robot_mode": "off",          # off | offers | auto
    "robot_pay": "manual",        # manual : tu valides le paiement ; auto : le robot paie
    "robot_min_score": 75,        # score pépite minimum
    "robot_min_profit": 15,       # bénéfice net minimum (€)
    "robot_min_conf": 0.5,        # confiance minimum sur la valeur
    "robot_max_item": 80,         # prix maximum par article (€)
    "robot_day_budget": 200,      # dépenses maximum par jour (€)
    "robot_day_items": 5,         # achats maximum par jour
    "robot_day_offers": 30,       # offres maximum par jour
    "robot_brands": "",           # marques autorisées (vide = toutes)
    "robot_require_ai": True,     # marques très contrefaites : exiger l'avis Gemini « authentique probable »
    "robot_offer_template": "Bonjour ! Votre {article} m'intéresse beaucoup. Seriez-vous d'accord pour {offre} € ? "
                            "Je peux payer tout de suite 🙂",
    "robot_pause_until": 0,
}

TEMPLATES = [
    "Bonjour ! Votre {article} m'intéresse beaucoup. Seriez-vous d'accord pour {offre} € ? Je peux payer tout de suite 🙂",
    "Hello, je suis intéressé(e) par votre {article}. Je vous propose {offre} €, paiement immédiat si ça vous va !",
    "Bonjour, est-ce que {offre} € vous irait pour votre {article} ? Je peux acheter dans la foulée. Merci !",
]

DAY = 86400.0


def _today_start() -> float:
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


# ---------------------------------------------------------------- prix d'offre
def max_buy_price(l: dict, settings: dict, min_profit: float) -> float | None:
    """Prix d'annonce maximum qui laisse encore le bénéfice minimum (frais et port compris)."""
    value = l.get("value")
    if not value:
        return None
    resale = value * float(settings.get("resale_factor") or 0.95) * (1 - float(settings.get("sell_fee_pct") or 0) / 100)
    budget = resale - min_profit - float(settings.get("ship_in") or 0) - float(settings.get("buy_fee_fixed") or 0.7)
    return max(0.0, budget / (1 + float(settings.get("buy_fee_pct") or 5) / 100))


def suggest_offer(l: dict, settings: dict, min_profit: float | None = None) -> dict:
    """Offre la plus basse ayant de bonnes chances d'être acceptée, sans dépasser le prix max rentable."""
    price = float(l.get("price") or 0)
    now = time.time()
    listed = l.get("published_at") or l.get("first_seen") or now
    age_days = max(0.0, (now - float(listed)) / DAY)
    favs = int(l.get("favourites") or 0)
    reasons = []
    # remise demandée selon l'ancienneté de l'annonce
    if age_days < 1:
        factor = 0.90
        reasons.append("annonce toute récente : petite remise")
    elif age_days < 7:
        factor = 0.84
        reasons.append(f"en ligne depuis {age_days:.0f} j")
    elif age_days < 30:
        factor = 0.78
        reasons.append(f"en ligne depuis {age_days:.0f} j : le vendeur attend")
    else:
        factor = 0.70
        reasons.append("en ligne depuis plus d'un mois : vendeur sans doute motivé")
    if favs >= 5:
        bump = min(0.08, favs / 100 + 0.02)
        factor += bump
        reasons.append(f"{favs} favoris : d'autres acheteurs sont intéressés")
    if l.get("price_drops"):
        factor -= 0.03
        reasons.append("le prix a déjà baissé")
    offer = price * factor
    mp = max_buy_price(l, settings, min_profit if min_profit is not None else float(settings.get("min_profit") or 10))
    if mp is not None and offer > mp:
        offer = mp
        reasons.append("plafonnée pour garder ton bénéfice")
    offer = max(price * 0.6, offer)  # trop bas = refus quasi certain (et vendeur vexé)
    offer = round(offer) if offer >= 10 else round(offer, 1)
    article = (l.get("type_label") or "").lower() if l.get("type_label") and l.get("type_label") != "Autre" else "article"
    tpl = settings.get("robot_offer_template") or random.choice(TEMPLATES)
    try:
        message = tpl.format(article=article, offre=f"{offer:g}".replace(".", ","), prix=f"{price:g}".replace(".", ","))
    except (KeyError, IndexError, ValueError):
        message = TEMPLATES[0].format(article=article, offre=f"{offer:g}".replace(".", ","))
    return {"offer": offer, "max_price": round(mp, 2) if mp is not None else None, "price": price,
            "discount": round(1 - offer / price, 3) if price else 0, "message": message, "reasons": reasons,
            "worth_buying_now": mp is not None and price <= mp}


# ---------------------------------------------------------------- robot
class Robot:
    def __init__(self, db, market):
        self.db = db
        self.market = market
        self.lock = threading.Lock()
        self.notify = None   # fonction(texte, titre, url, priorité)
        self.executor = {"last_poll": None, "logged_in": None, "platform": None, "version": None}
        with db.lock:
            db.conn.executescript(SCHEMA)
            db.conn.commit()

    # ---------- réglages ----------
    def settings(self) -> dict:
        s = dict(DEFAULT_ROBOT_SETTINGS)
        s.update(self.market.settings())
        s.update({k: v for k, v in self.db.settings().items() if k in DEFAULT_ROBOT_SETTINGS})
        return s

    # ---------- SQL ----------
    def _q(self, sql: str, args=()) -> list[dict]:
        with self.db.lock:
            return [dict(r) for r in self.db.conn.execute(sql, args)]

    def _exec(self, sql: str, args=()) -> int:
        with self.db.lock:
            c = self.db.conn.execute(sql, args)
            self.db.conn.commit()
            return c.lastrowid

    def actions(self, limit: int = 100) -> list[dict]:
        return self._q("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,))

    def get_action(self, aid: int) -> dict | None:
        r = self._q("SELECT * FROM actions WHERE id = ?", (aid,))
        return r[0] if r else None

    # ---------- budgets ----------
    def today(self) -> dict:
        t0 = _today_start()
        spent = self._q("SELECT COALESCE(SUM(amount), 0) AS s, COUNT(*) AS n FROM actions WHERE kind = 'buy' "
                        "AND status IN ('checkout', 'paid', 'running', 'pending') AND created_at > ?", (t0,))[0]
        offers = self._q("SELECT COUNT(*) AS n FROM actions WHERE kind = 'offer' AND status NOT IN ('failed', 'cancelled') "
                         "AND created_at > ?", (t0,))[0]
        return {"spent": round(spent["s"] or 0, 2), "buys": spent["n"], "offers": offers["n"]}

    # ---------- décision automatique ----------
    def check_rules(self, l: dict, s: dict) -> list[str]:
        """Raisons de NE PAS agir (liste vide = l'annonce passe toutes les règles)."""
        why = []
        if l.get("state") not in (None, "active"):
            why.append("plus en vente")
        if (l.get("gem_score") or 0) < float(s["robot_min_score"]):
            why.append("score pépite trop bas")
        if (l.get("profit") or 0) < float(s["robot_min_profit"]):
            why.append("bénéfice trop faible")
        if (l.get("confidence") or 0) < float(s["robot_min_conf"]):
            why.append("valeur pas assez sûre")
        if l.get("source") != "vinted":
            why.append("pas sur Vinted")
        brands = [b.strip().lower() for b in str(s.get("robot_brands") or "").split(",") if b.strip()]
        if brands and not any(b in (l.get("brand") or "").lower() or b == l.get("brand_norm") for b in brands):
            why.append("marque hors de ta liste")
        flags = l.get("flags") or []
        if "prix_suspect" in flags or "suspect_ia" in flags:
            why.append("risque d'arnaque")
        if s.get("robot_require_ai") and "contrefacons" in flags:
            why.append("authenticité non vérifiée par Gemini")
        return why

    def consider(self, l: dict) -> dict | None:
        """Appelée pour chaque nouvelle annonce valorisée : crée une action si le mode et les règles le permettent."""
        s = self.settings()
        mode = s.get("robot_mode") or "off"
        if mode == "off" or float(s.get("robot_pause_until") or 0) > time.time():
            return None
        if self.check_rules(l, s):
            return None
        if self._q("SELECT id FROM actions WHERE listing_id = ? AND status NOT IN ('failed', 'cancelled')", (l["id"],)):
            return None
        sug = suggest_offer(l, s, float(s["robot_min_profit"]))
        day = self.today()
        price = float(l.get("price") or 0)
        if mode == "auto" and sug["worth_buying_now"] and price <= float(s["robot_max_item"]) \
                and day["spent"] + price <= float(s["robot_day_budget"]) and day["buys"] < int(s["robot_day_items"]):
            return self.create(l, "buy", price, None, "robot")
        if day["offers"] < int(s["robot_day_offers"]) and sug["offer"] <= float(s["robot_max_item"]) \
                and day["spent"] + sug["offer"] <= float(s["robot_day_budget"]):
            return self.create(l, "offer", sug["offer"], sug["message"], "robot")
        return None

    # ---------- actions ----------
    def create(self, l: dict, kind: str, amount: float | None, message: str | None, origin: str) -> dict:
        now = time.time()
        aid = self._exec(
            "INSERT INTO actions (listing_id, source, ext_id, url, title, photo, kind, amount, price, message, origin, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (l.get("id"), l.get("source"), l.get("ext_id"), l.get("url"), l.get("title"), l.get("photo"), kind,
             amount, l.get("price"), message, origin, now, now))
        a = self.get_action(aid)
        if origin == "robot" and self.notify:
            verb = "achète" if kind == "buy" else f"propose {amount:g} € pour"
            self.notify(f"🤖 Le robot {verb} : {l.get('title')}", f"Prix {l.get('price'):g} € · bénéfice estimé "
                        f"+{(l.get('profit') or 0):.0f} €", l.get("url") or "", False, aid)
        return a

    def next_action(self, background: bool) -> dict | None:
        """Prochaine action pour l'exécuteur. En arrière-plan : seulement celles du robot."""
        with self.lock:
            q = "SELECT * FROM actions WHERE status = 'pending'"
            if background:
                q += " AND origin = 'robot'"
            # une action bloquée depuis > 5 min en « running » est remise en file
            self._exec("UPDATE actions SET status = 'pending' WHERE status = 'running' AND updated_at < ?",
                       (time.time() - 300,))
            r = self._q(q + " ORDER BY (kind = 'buy') DESC, id LIMIT 1")
            if not r:
                return None
            a = r[0]
            self._exec("UPDATE actions SET status = 'running', updated_at = ? WHERE id = ?", (time.time(), a["id"]))
            a["status"] = "running"
            a["pay"] = self.settings().get("robot_pay") == "auto" and a["kind"] == "buy"
            return a

    def report(self, aid: int, status: str, detail: str = "", debug=None, checkout_url: str | None = None) -> dict | None:
        a = self.get_action(aid)
        if not a:
            return None
        allowed = {"running", "sent", "checkout", "paid", "accepted", "refused", "failed", "cancelled", "pending"}
        status = status if status in allowed else "failed"
        self._exec("UPDATE actions SET status = ?, detail = ?, debug = ?, checkout_url = COALESCE(?, checkout_url), "
                   "updated_at = ? WHERE id = ?",
                   (status, (detail or "")[:500], json.dumps(debug, ensure_ascii=False)[:8000] if debug else a.get("debug"),
                    checkout_url, time.time(), aid))
        if status == "paid":
            self.add_purchase(a)
        if self.notify and status in ("checkout", "paid", "failed") and a.get("origin") == "robot":
            msg = {"checkout": "💳 Paiement prêt : touche pour valider", "paid": "✅ Acheté !",
                   "failed": "⚠️ Le robot n'a pas réussi"}[status]
            self.notify(f"{msg} — {a.get('title')}", detail or "", checkout_url or a.get("url") or "",
                        status == "checkout", aid)
        return self.get_action(aid)

    def cancel(self, aid: int) -> None:
        self._exec("UPDATE actions SET status = 'cancelled', updated_at = ? WHERE id = ? AND status IN "
                   "('pending', 'running', 'checkout', 'sent')", (time.time(), aid))

    # ---------- stock & bénéfices ----------
    def add_purchase(self, a: dict, price: float | None = None) -> int:
        l = self.db.get_listing(a["listing_id"]) if a.get("listing_id") else None
        if self._q("SELECT id FROM portfolio WHERE listing_id = ? AND listing_id IS NOT NULL", (a.get("listing_id"),)):
            return 0
        price = price or a.get("amount") or (l or {}).get("price") or 0
        s = self.settings()
        cost = price + float(s.get("buy_fee_fixed") or 0.7) + price * float(s.get("buy_fee_pct") or 5) / 100 \
            + float(s.get("ship_in") or 0)
        return self._exec(
            "INSERT INTO portfolio (listing_id, title, brand, itype, photo, url, buy_price, buy_cost, bought_at, est_value, "
            "status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'achete')",
            (a.get("listing_id"), a.get("title") or (l or {}).get("title"), (l or {}).get("brand"), (l or {}).get("itype"),
             a.get("photo") or (l or {}).get("photo"), a.get("url") or (l or {}).get("url"), price, round(cost, 2),
             time.time(), (l or {}).get("value")))

    def portfolio(self) -> dict:
        rows = self._q("SELECT * FROM portfolio ORDER BY id DESC")
        sold = [r for r in rows if r["status"] == "vendu"]
        stock = [r for r in rows if r["status"] not in ("vendu", "retourne")]
        invested = sum(r["buy_cost"] or 0 for r in rows if r["status"] != "retourne")
        revenue = sum((r["sale_price"] or 0) - (r["sale_fees"] or 0) for r in sold)
        profit = sum((r["sale_price"] or 0) - (r["sale_fees"] or 0) - (r["buy_cost"] or 0) for r in sold)
        days = [(r["sold_at"] - r["bought_at"]) / DAY for r in sold if r["sold_at"] and r["bought_at"]]
        return {
            "items": rows,
            "stats": {
                "bought": len(rows), "sold": len(sold), "in_stock": len(stock),
                "invested": round(invested, 2), "revenue": round(revenue, 2), "profit": round(profit, 2),
                "roi": round(profit / sum(r["buy_cost"] or 0 for r in sold), 3) if sold and any(r["buy_cost"] for r in sold) else None,
                "stock_cost": round(sum(r["buy_cost"] or 0 for r in stock), 2),
                "stock_value": round(sum(r["est_value"] or 0 for r in stock), 2),
                "avg_days": round(sum(days) / len(days), 1) if days else None,
            },
        }

    def add_manual(self, data: dict) -> int:
        return self._exec(
            "INSERT INTO portfolio (title, brand, photo, url, buy_price, buy_cost, bought_at, est_value, status, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (data.get("title") or "Article", data.get("brand"), data.get("photo"), data.get("url"),
             float(data.get("buy_price") or 0), float(data.get("buy_cost") or data.get("buy_price") or 0),
             time.time(), data.get("est_value"), data.get("status") or "achete", data.get("notes")))

    def update_item(self, pid: int, data: dict) -> None:
        fields = {k: data[k] for k in ("status", "sale_price", "sale_fees", "notes", "buy_price", "buy_cost", "title",
                                       "est_value") if k in data}
        if fields.get("status") == "vendu" and "sold_at" not in data:
            fields["sold_at"] = time.time()
        if not fields:
            return
        self._exec(f"UPDATE portfolio SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), pid))

    def delete_item(self, pid: int) -> None:
        self._exec("DELETE FROM portfolio WHERE id = ?", (pid,))
