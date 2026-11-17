"""Analyste : quels articles se vendent le plus (et le plus vite) sur une période,
et quelles annonces très similaires sont à acheter maintenant ; plus la liste des « pépites »."""
from __future__ import annotations

import statistics
import time
from collections import Counter, defaultdict

from product import norm, type_label
from valuation import DAY, FLAG_LABELS

SORTS = {
    "score": "gem_score DESC",
    "profit": "profit DESC",
    "roi": "roi DESC",
    "speed": "days_to_sell ASC, gem_score DESC",
    "recent": "first_seen DESC",
    "price": "price ASC",
}


def _median(xs):
    return statistics.median(xs) if xs else None


def _quant(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def _filters(brand: str | None, itype: str | None, source: str | None) -> tuple[str, list]:
    where, args = [], []
    if brand:
        where.append("brand_norm = ?")
        args.append(brand)
    if itype:
        where.append("itype = ?")
        args.append(itype)
    if source:
        where.append("source = ?")
        args.append(source)
    return (" AND " + " AND ".join(where)) if where else "", args


def _display_brand(rows: list[dict]) -> str:
    c = Counter((r.get("brand") or r.get("brand_norm") or "").strip() for r in rows)
    c.pop("", None)
    return c.most_common(1)[0][0] if c else "Sans marque"


def _clusters(rows: list[dict], idx) -> dict[tuple, list[dict]]:
    """Regroupe par marque + type + modèle (mots distinctifs partagés par plusieurs articles)."""
    by_group: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_group[(r.get("brand_norm") or "", r.get("itype") or "")].append(r)
    out: dict[tuple, list[dict]] = defaultdict(list)
    for (brand, itype), items in by_group.items():
        counts = Counter(t for r in items for t in set((r.get("model") or "").split()))
        for r in items:
            toks = [t for t in set((r.get("model") or "").split()) if counts[t] >= 2]
            if not toks:
                out[(brand, itype, "")].append(r)
                continue
            t1 = max(toks, key=lambda t: (counts[t], idx.idf(t)))
            pair = Counter(t for x in items if t1 in (x.get("model") or "").split()
                           for t in set((x.get("model") or "").split()) if t != t1)
            others = [t for t in toks if t != t1 and pair[t] >= 2]
            key = t1 if not others else " ".join(sorted([t1, max(others, key=lambda t: pair[t])]))
            out[(brand, itype, key)].append(r)
    return out


def _model_label(model: str, itype: str) -> str:
    skip = set(norm(type_label(itype)).split()) | {itype}
    return " ".join(t for t in model.split() if t not in skip).title()


def top_sellers(db, idx, days: float = 7, brand: str | None = None, itype: str | None = None,
                source: str | None = None, limit: int = 30) -> dict:
    now = time.time()
    since = now - days * DAY
    extra, args = _filters(brand, itype, source)
    sold = db.query_listings("state = 'sold' AND sold_at > ?" + extra, [since, *args],
                             order="sold_at DESC", limit=6000)
    basis = "ventes"
    rows = sold
    if len(sold) < 5:  # pas encore assez de ventes détectées : tendance d'après les favoris
        basis = "favoris"
        rows = db.query_listings("state IN ('active', 'reserved') AND first_seen > ? AND favourites > 0" + extra,
                                 [since, *args], order="favourites DESC", limit=4000)

    groups = []
    for (b, t, model), items in _clusters(rows, idx).items():
        if basis == "ventes" and len(items) < 2:
            continue
        prices = [r["price"] for r in items if r.get("price")]
        tts = [max(0.05, (r["sold_at"] - min(r["first_seen"], r.get("published_at") or r["first_seen"])) / DAY)
               for r in items if basis == "ventes" and r.get("sold_at")]
        toks = set(model.split())
        active = [e for e in idx.group(b, t) if e.state in ("active", "reserved") and e.first_seen > since
                  and e.source == "vinted" and e.checks > 0 and toks <= e.model]
        n = len(items)
        sell_through = n / (n + len(active)) if basis == "ventes" else None
        med_tts = _median(tts)
        if basis == "ventes":
            demand_score = n * (0.5 + (sell_through or 0)) / (1 + (med_tts or 7) / 7)
        else:
            vel = [r["favourites"] / max(1 / 24, (now - (r.get("published_at") or r["first_seen"])) / DAY)
                   for r in items]
            demand_score = sum(vel)
        trend = None
        if basis == "ventes" and n >= 4:
            mid = since + (now - since) / 2
            early = [r["price"] for r in items if r["sold_at"] < mid]
            late = [r["price"] for r in items if r["sold_at"] >= mid]
            if len(early) >= 2 and len(late) >= 2:
                trend = round(_median(late) / _median(early) - 1, 3)
        sample = sorted(items, key=lambda r: -(r.get("favourites") or 0))[:4]
        groups.append({
            "key": f"{b}|{t}|{model}", "brand": b, "itype": t, "model": model,
            "label": " · ".join(x for x in (_display_brand(items), type_label(t), _model_label(model, t)) if x),
            "n": n, "median": round(_median(prices), 2) if prices else None,
            "low": _quant(prices, 0.25), "high": _quant(prices, 0.75),
            "days_to_sell": round(med_tts, 1) if med_tts is not None else None,
            "sell_through": round(sell_through, 2) if sell_through is not None else None,
            "active_now": len(active), "trend": trend,
            "favourites": round(sum(r.get("favourites") or 0 for r in items) / n, 1),
            "demand": round(demand_score, 2),
            "samples": [{"id": r["id"], "title": r["title"], "price": r["price"], "photo": r.get("photo"),
                         "url": r.get("url"), "status": r.get("status")} for r in sample],
        })
    groups.sort(key=lambda g: -g["demand"])

    def top_by(keyf, label_f):
        c = Counter()
        for r in rows:
            k = keyf(r)
            if k:
                c[k] += 1
        return [{"key": k, "label": label_f(k), "n": n} for k, n in c.most_common(8)]

    brand_names = {}
    for r in rows:
        if r.get("brand_norm") and r.get("brand"):
            brand_names.setdefault(r["brand_norm"], r["brand"])
    fastest = sorted([g for g in groups if g["days_to_sell"] is not None and g["n"] >= 3],
                     key=lambda g: g["days_to_sell"])[:6]
    return {
        "basis": basis, "days": days, "count": len(rows), "sold_total": len(sold),
        "groups": groups[:limit],
        "top_brands": top_by(lambda r: r.get("brand_norm"), lambda k: brand_names.get(k, k.title())),
        "top_types": top_by(lambda r: r.get("itype"), type_label),
        "fastest": [{"label": g["label"], "days": g["days_to_sell"], "n": g["n"], "key": g["key"]} for g in fastest],
    }


def similar_to_buy(db, idx, key: str, ref_price: float | None, settings: dict, limit: int = 12) -> list[dict]:
    """Annonces en vente, très similaires à un groupe qui se vend bien, classées par marge attendue."""
    brand, itype, model = (key.split("|") + ["", "", ""])[:3]
    toks = frozenset(model.split())
    rows = db.query_listings("brand_norm = ? AND itype = ? AND state IN ('active', 'reserved') AND last_seen > ?",
                             [brand, itype, time.time() - 7 * DAY], order="first_seen DESC", limit=1500)
    resale_factor = float(settings.get("resale_factor") or 0.95)
    out = []
    for r in rows:
        m = frozenset((r.get("model") or "").split())
        sim = 1.0 if toks and toks <= m else idx.model_sim(toks, m) if toks else 0.5
        if toks and sim < 0.45:
            continue
        cost = r.get("cost") or r["price"]
        margin = (ref_price * resale_factor - cost) if ref_price else r.get("profit")
        r["similarity"] = round(sim, 2)
        r["group_margin"] = round(margin, 2) if margin is not None else None
        out.append(r)
    out.sort(key=lambda r: (-(r["similarity"] >= 0.99), -(r["group_margin"] or -999)))
    return [_public(r) for r in out[:limit]]


def gems(db, budget: float | None = None, min_profit: float | None = None, itype: str | None = None,
         brand: str | None = None, source: str | None = None, min_conf: float = 0.0, sort: str = "score",
         fresh_days: float = 3, limit: int = 120) -> list[dict]:
    where = "state = 'active' AND last_seen > ? AND gem_score > 0 AND profit IS NOT NULL"
    args: list = [time.time() - fresh_days * DAY]
    if min_profit is not None:
        where += " AND profit >= ?"
        args.append(min_profit)
    if budget:
        where += " AND price <= ?"
        args.append(budget)
    if min_conf:
        where += " AND confidence >= ?"
        args.append(min_conf)
    extra, a2 = _filters(brand, itype, source)
    rows = db.query_listings(where + extra, args + a2, order=SORTS.get(sort, SORTS["score"]), limit=limit)
    return [_public(r) for r in rows]


def _public(r: dict) -> dict:
    keep = ("id", "source", "ext_id", "title", "brand", "brand_norm", "itype", "model", "size", "status",
            "status_id", "price", "total_price", "url", "photo", "favourites", "first_seen", "last_seen",
            "published_at", "state", "price_drops", "value", "value_low", "value_high", "value_n", "value_level",
            "confidence", "cost", "profit", "roi", "p_sell", "days_to_sell", "gem_score", "flags", "ai_status",
            "ai_data", "similarity", "group_margin", "sold_at", "checks")
    out = {k: r.get(k) for k in keep}
    out["type_label"] = type_label(r.get("itype") or "")
    out["flag_labels"] = [FLAG_LABELS.get(f, f) for f in (r.get("flags") or [])]
    return out


public_listing = _public
