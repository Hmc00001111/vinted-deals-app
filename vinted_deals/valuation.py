"""Valeur d'un article par comparaison avec des articles SIMILAIRES (et non la moyenne de la marque).

Pour un article donné, on prend les annonces de la même marque ET du même type (pull, jean, veste…),
puis on pondère chacune selon sa ressemblance :
  - modèle : mots distinctifs communs (detroit, 501, nuptse…), les mots rares comptent plus (IDF) ;
  - état   : le prix du comparable est ramené à l'état de l'article (neuf avec étiquette ≠ satisfaisant) ;
  - couleur, ancienneté de l'observation ;
  - VENDU  : un article réellement vendu vaut deux annonces en vente (c'est un vrai prix de marché),
             et les prix demandés sont corrigés par l'écart constaté entre prix demandés et prix vendus.

Si au moins 3 comparables du même modèle existent, seuls les articles proches comptent (niveau « modèle »),
sinon on retombe sur marque + type (niveau « type »). La valeur est la médiane pondérée,
avec une fourchette (25e–75e centile) et un indice de confiance.

On calcule aussi la demande (probabilité de revendre, délai) et la rentabilité d'un achat-revente
(score « pépite »).
"""
from __future__ import annotations

import math
import statistics
import threading
import time
from collections import Counter, defaultdict

from product import condition_mult

DAY = 86400.0
HISTORY_DAYS = 90

DEFAULT_FLIP_SETTINGS = {
    "buy_fee_fixed": 0.70,     # protection acheteur Vinted (part fixe, €)
    "buy_fee_pct": 5.0,        # protection acheteur Vinted (%)
    "ship_in": 4.0,            # frais de port payés pour recevoir l'article (€)
    "sell_fee_pct": 0.0,       # frais vendeur à la revente (0 % sur Vinted France)
    "resale_factor": 0.95,     # on revend un peu sous la valeur pour vendre vite
    "min_profit": 10.0,        # bénéfice minimum pour qu'une annonce soit une « pépite »
}

RISKY_BRANDS = {"stone island", "moncler", "louis vuitton", "gucci", "chanel", "dior", "prada",
                "balenciaga", "off white", "supreme", "canada goose", "hermes", "yves saint laurent",
                "saint laurent", "burberry", "fendi", "goyard", "chrome hearts", "jordan", "yeezy", "rolex",
                "cartier", "palace", "bape", "a bathing ape", "trapstar", "corteiz", "essentials", "fear of god"}


class Entry:
    __slots__ = ("id", "source", "brand", "itype", "model", "color", "status_id", "price", "state",
                 "first_seen", "last_seen", "published_at", "sold_at", "favourites", "checks")

    def __init__(self, row: dict):
        self.id = row["id"]
        self.source = row.get("source") or ""
        self.brand = row.get("brand_norm") or ""
        self.itype = row.get("itype") or ""
        m = row.get("model") or []
        self.model = frozenset(m.split() if isinstance(m, str) else m)
        self.color = row.get("color") or ""
        self.status_id = row.get("status_id")
        self.price = float(row.get("price") or 0)
        self.state = row.get("state") or "active"
        self.first_seen = float(row.get("first_seen") or time.time())
        self.last_seen = float(row.get("last_seen") or self.first_seen)
        self.published_at = row.get("published_at")
        self.sold_at = row.get("sold_at")
        self.favourites = int(row.get("favourites") or 0)
        self.checks = int(row.get("checks") or 0)

    @property
    def listed_at(self) -> float:
        p = self.published_at
        return float(p) if p and p < self.first_seen + 3600 else self.first_seen


class MarketIndex:
    """Index en mémoire de toutes les annonces observées (90 derniers jours), groupées par marque + type."""

    def __init__(self):
        self.lock = threading.RLock()
        self.entries: dict[int, Entry] = {}
        self.groups: dict[tuple[str, str], set[int]] = defaultdict(set)
        self.by_brand: dict[str, set[int]] = defaultdict(set)
        self.df: Counter = Counter()
        self.brand_count: Counter = Counter()
        self._demand_cache: dict = {}

    # ---------- alimentation ----------
    def load(self, rows) -> None:
        for r in rows:
            self.upsert(r)

    def upsert(self, row: dict) -> Entry:
        with self.lock:
            old = self.entries.get(row["id"])
            if old:
                self._unlink(old)
            e = Entry(row)
            self.entries[e.id] = e
            self.groups[(e.brand, e.itype)].add(e.id)
            if e.brand:
                self.by_brand[e.brand].add(e.id)
                self.brand_count[e.brand] += 1
            for t in e.model:
                self.df[t] += 1
            self._demand_cache.pop((e.brand, e.itype), None)
            return e

    def _unlink(self, e: Entry) -> None:
        self.groups[(e.brand, e.itype)].discard(e.id)
        if e.brand:
            self.by_brand[e.brand].discard(e.id)
            self.brand_count[e.brand] -= 1
        for t in e.model:
            self.df[t] -= 1

    def known_brands(self) -> set[str]:
        with self.lock:
            return {b for b, n in self.brand_count.items() if n >= 2 and b}

    def prune(self, days: int = HISTORY_DAYS) -> None:
        limit = time.time() - days * DAY
        with self.lock:
            for e in [e for e in self.entries.values() if e.last_seen < limit and (e.sold_at or 0) < limit]:
                self._unlink(e)
                del self.entries[e.id]

    def group(self, brand: str, itype: str) -> list[Entry]:
        with self.lock:
            return [self.entries[i] for i in self.groups.get((brand, itype), ())]

    # ---------- similarité ----------
    def idf(self, tok: str) -> float:
        n = max(1, len(self.entries))
        return math.log((n + 1) / (self.df.get(tok, 0) + 1)) + 1.0

    def model_sim(self, a: frozenset, b: frozenset) -> float:
        if not a and not b:
            return 0.4
        if not a or not b:
            return 0.1
        inter = sum(self.idf(t) for t in a & b)
        union = sum(self.idf(t) for t in a | b)
        return inter / union if union else 0.0


# ---------------------------------------------------------------- outils statistiques
def wquantile(values: list[tuple[float, float]], q: float) -> float:
    """Quantile pondéré ; values = [(valeur, poids)]."""
    vals = sorted(v for v in values if v[1] > 0)
    total = sum(w for _, w in vals)
    if not vals or total <= 0:
        return 0.0
    acc = 0.0
    for v, w in vals:
        acc += w
        if acc >= q * total:
            return v
    return vals[-1][0]


def n_eff(weights: list[float]) -> float:
    s, s2 = sum(weights), sum(w * w for w in weights)
    return (s * s / s2) if s2 else 0.0


# ---------------------------------------------------------------- valeur
def _ask_to_sold_ratio(comps: list[Entry]) -> float:
    sold = [c.price for c in comps if c.state == "sold"]
    active = [c.price for c in comps if c.state != "sold"]
    if len(sold) >= 3 and len(active) >= 3:
        r = statistics.median(sold) / max(1.0, statistics.median(active))
        return max(0.6, min(1.0, r))
    return 0.88


def value_of(idx: MarketIndex, brand: str, itype: str, model, status_id: int | None, color: str = "",
             exclude_id: int | None = None, now: float | None = None) -> dict | None:
    """Valeur de revente estimée d'un article, d'après des articles similaires."""
    now = now or time.time()
    model = frozenset(model or [])
    if not brand:
        return None
    pool = [c for c in idx.group(brand, itype) if c.id != exclude_id and c.price > 0
            and now - max(c.last_seen, c.sold_at or 0) < HISTORY_DAYS * DAY]
    level = "type" if itype else "marque"
    if not pool:
        return None
    if len(pool) > 700:  # gros groupe (ex. Nike baskets) : on garde les plus pertinents, pour rester rapide
        sharing = [c for c in pool if c.model & model]
        rest = sorted((c for c in pool if not (c.model & model)), key=lambda c: -c.last_seen)
        pool = sorted(sharing, key=lambda c: -c.last_seen)[:500] + rest[:200]
    target_mult = condition_mult(status_id)
    ratio = _ask_to_sold_ratio(pool)

    scored = []
    for c in pool:
        sim = idx.model_sim(model, c.model) if model or c.model else 0.4
        scored.append((c, sim))

    close = [(c, s) for c, s in scored if s >= 0.5]
    close_w = [1.0 + (1.0 if c.state == "sold" else 0.0) for c, _ in close]
    if model and len(close) >= 3 and n_eff(close_w) >= 2.5:
        level = "modele"
        scored = [(c, s) for c, s in scored if s >= 0.35]

    data = []
    for c, sim in scored:
        w = (0.15 + sim) ** 2 if level != "modele" else sim ** 2
        if color and c.color:
            w *= 1.15 if c.color == color else 0.9
        if status_id and c.status_id:
            w *= 1.0 - 0.06 * abs(_cond_rank(status_id) - _cond_rank(c.status_id))
        age = (now - (c.sold_at or c.last_seen)) / DAY
        w *= math.exp(-max(0.0, age) / 45)
        price = c.price * target_mult / condition_mult(c.status_id)
        if c.state == "sold":
            w *= 2.0
        else:
            price *= ratio
        data.append((price, w, c))

    if not data:
        return None
    med = wquantile([(p, w) for p, w, _ in data], 0.5)
    data = [d for d in data if med / 3 <= d[0] <= med * 3]  # valeurs aberrantes (lots, accessoires…)
    if not data:
        return None
    pw = [(p, w) for p, w, _ in data]
    value = wquantile(pw, 0.5)
    low, high = wquantile(pw, 0.25), wquantile(pw, 0.75)
    ne = n_eff([w for _, w in pw])
    n_sold = sum(1 for _, _, c in data if c.state == "sold")
    spread = (high - low) / value if value else 1.0
    level_f = {"modele": 1.0, "type": 0.75, "marque": 0.4}[level]
    confidence = level_f * (1 - math.exp(-ne / 4)) * max(0.3, min(1.0, 1.3 - spread))
    comps = sorted(data, key=lambda d: -d[1])[:12]
    return {
        "value": round(value, 2), "low": round(low, 2), "high": round(high, 2),
        "n": len(data), "n_eff": round(ne, 1), "n_sold": n_sold, "level": level,
        "confidence": round(confidence, 3), "ask_ratio": round(ratio, 2),
        "comps": [c.id for _, _, c in comps],
    }


def _cond_rank(sid: int | None) -> int:
    return {6: 0, 1: 1, 2: 2, 3: 3, 4: 4}.get(sid or -1, 2)


def basis_text(v: dict) -> str:
    if v["level"] == "gemini":
        return "estimation Gemini + recherche Google (pas encore d'articles comparables)"
    lvl = {"modele": "même modèle", "type": "même marque et même type", "marque": "même marque"}[v["level"]]
    sold = f", dont {v['n_sold']} vendus" if v.get("n_sold") else ""
    return f"{v['n']} articles similaires ({lvl}, état ajusté{sold})"


# ---------------------------------------------------------------- demande
def demand(idx: MarketIndex, brand: str, itype: str, model=(), now: float | None = None) -> dict:
    """Probabilité de revendre (≈ 3 semaines) et délai médian, d'après les ventes détectées sur Vinted."""
    now = now or time.time()
    model = frozenset(model or [])

    def stats(entries: list[Entry]) -> tuple[int, int, list[float]]:
        sold, active, tts = 0, 0, []
        for e in entries:
            if e.source != "vinted":
                continue
            if e.state == "sold" and e.sold_at and now - e.sold_at < 30 * DAY:
                sold += 1
                tts.append(max(0.05, (e.sold_at - e.listed_at) / DAY))
            elif e.state in ("active", "reserved") and e.checks > 0 and now - e.listed_at > 3 * DAY \
                    and now - e.first_seen < 30 * DAY:
                active += 1
        return sold, active, tts

    key = (brand, itype)
    cached = idx._demand_cache.get(key)
    if cached and now - cached[0] < 300:
        g = cached[1]
    else:
        g = stats(idx.group(brand, itype))
        idx._demand_cache[key] = (now, g)
    sold, active, tts = g
    if model:
        close = [e for e in idx.group(brand, itype) if idx.model_sim(model, e.model) >= 0.5]
        ms, ma, mt = stats(close)
        if ms >= 3:  # assez de ventes du modèle lui-même : il prime
            sold, active, tts = ms + sold * 0.3, ma + active * 0.3, mt + tts[: max(0, 6 - len(mt))]
    p = (sold + 1) / (sold + active + 4)  # a priori prudent (Beta 1,3)
    days = statistics.median(tts) if len(tts) >= 2 else 12.0
    return {"p_sell": round(p, 3), "days": round(days, 1), "sold_30d": int(sold), "active": int(active)}


# ---------------------------------------------------------------- achat-revente
def flip(price: float, total_price: float | None, val: dict | None, dem: dict, settings: dict,
         flags: list[str]) -> dict:
    s = dict(DEFAULT_FLIP_SETTINGS)
    s.update({k: v for k, v in settings.items() if k in DEFAULT_FLIP_SETTINGS and v is not None})
    fee = float(s["buy_fee_fixed"]) + price * float(s["buy_fee_pct"]) / 100
    cost = (total_price if total_price and total_price >= price else price + fee) + float(s["ship_in"])
    if not val:
        return {"cost": round(cost, 2), "profit": None, "roi": None, "gem_score": 0.0}
    resale = val["value"] * float(s["resale_factor"]) * (1 - float(s["sell_fee_pct"]) / 100)
    profit = resale - cost
    roi = profit / cost if cost else 0.0
    p = dem["p_sell"]
    expected = p * profit - (1 - p) * 0.12 * cost  # risque : devoir brader si ça ne part pas
    speed = 1.0 if dem["days"] <= 3 else max(0.55, 1 - (dem["days"] - 3) / 60)
    score = 100 * val["confidence"] ** 0.7 * (1 - math.exp(-max(0.0, expected) / 15)) \
        * min(1.0, 0.45 + roi) * speed
    for f in flags:
        score *= {"prix_suspect": 0.45, "etat_moyen": 0.85, "contrefacons": 0.85, "sans_photo": 0.7,
                  "peu_de_donnees": 0.8}.get(f, 1.0)
    if profit < float(s["min_profit"]):
        score = min(score, 25.0)
    return {"cost": round(cost, 2), "resale": round(resale, 2), "profit": round(profit, 2),
            "roi": round(roi, 3), "expected": round(expected, 2), "gem_score": round(max(0.0, score), 1)}


def risk_flags(price: float, brand: str, status_id: int | None, has_photo: bool, val: dict | None,
               ai_checked: bool = False) -> list[str]:
    flags = []
    if val and price < 0.2 * val["value"]:
        flags.append("prix_suspect")
    if status_id == 4:
        flags.append("etat_moyen")
    if brand in RISKY_BRANDS and not ai_checked:
        flags.append("contrefacons")
    if not has_photo:
        flags.append("sans_photo")
    if val and val["n_eff"] < 3:
        flags.append("peu_de_donnees")
    return flags


FLAG_LABELS = {
    "prix_suspect": "Prix anormalement bas (arnaque ? accessoire seul ?)",
    "etat_moyen": "État « satisfaisant »",
    "contrefacons": "Marque très contrefaite : vérifier l'authenticité",
    "sans_photo": "Pas de photo",
    "peu_de_donnees": "Peu d'articles comparables",
    "suspect_ia": "Gemini a un doute sérieux sur l'authenticité",
}
