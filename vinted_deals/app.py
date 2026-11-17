"""Deals Mode — interface web locale + surveillance continue de plusieurs sites.

Lancement :  python app.py            (ouvre http://127.0.0.1:5000)
Options   :  python app.py --interval 90 --domain www.vinted.be --port 8000
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import logging
import threading
import time
import webbrowser

from flask import Flask, jsonify, render_template, request

import analyst
from ai import DEFAULT_MODEL, DEFAULT_SETTINGS, test_key
from categories import CATEGORIES, category_meta
from db import DB
from market import DEFAULT_MARKET_SETTINGS, POPULAR_TERMS
from monitor import MIN_INTERVAL, Monitor
from product import ITEM_TYPES
from robot import DEFAULT_ROBOT_SETTINGS, suggest_offer
from valuation import FLAG_LABELS, basis_text
from scoring import DEFAULT_EXCLUDE_KEYWORDS, DEFAULT_RARE_KEYWORDS
from sources import SOURCES, STATUS_LABELS, source_meta
from sources.base import has_cffi

app = Flask(__name__)
db: DB
monitor: Monitor

INTERVALS = [(10, "10 s ⚡ (1-2 recherches max)"), (15, "15 s ⚡"), (30, "30 s"), (60, "1 min"), (120, "2 min"), (300, "5 min"), (600, "10 min"),
             (900, "15 min"), (1800, "30 min"), (3600, "1 h")]


def _num(v):
    if v in (None, ""):
        return None
    return float(str(v).replace(",", "."))


def _join(value, allowed=None) -> str:
    if isinstance(value, str):
        value = value.split(",")
    vals = [str(v).strip() for v in (value or []) if str(v).strip()]
    if allowed is not None:
        vals = [v for v in vals if v in allowed]
    return ",".join(dict.fromkeys(vals))


def clean_watch(data: dict, partial: bool = False) -> dict:
    out = {}
    for key in ("name", "search_text", "brands", "sizes", "rare_keywords", "exclude_keywords"):
        if key in data:
            out[key] = (data[key] or "").strip()
    for key in ("price_min", "price_max", "ref_price", "min_score"):
        if key in data:
            out[key] = _num(data[key])
    if "min_discount" in data:  # saisi en %
        v = _num(data["min_discount"])
        out["min_discount"] = (v / 100 if v and v > 1 else v) or 0
    if "status_ids" in data:
        ids = data["status_ids"]
        out["status_ids"] = ",".join(str(int(i)) for i in ids) if isinstance(ids, list) else str(ids or "")
    if "sources" in data:
        out["sources"] = _join(data["sources"], SOURCES)
    if "categories" in data:
        out["categories"] = _join(data["categories"], CATEGORIES)
    if "interval" in data:
        out["interval"] = max(MIN_INTERVAL, int(_num(data["interval"]) or 60))
    for key in ("active", "ai"):
        if key in data:
            out[key] = 1 if data[key] in (True, 1, "1", "true", "on") else 0
    if not partial:
        if not out.get("name"):
            out["name"] = out.get("search_text") or out.get("brands") or "Recherche"
        if not (out.get("search_text") or out.get("brands")):
            raise ValueError("Indique au moins des mots-clés ou une marque.")
        if not out.get("sources"):
            raise ValueError("Coche au moins un site.")
        out.setdefault("min_score", 55)
        out.setdefault("interval", monitor.default_interval)
        out.setdefault("ai", 1)
    elif "sources" in out and not out["sources"]:
        raise ValueError("Coche au moins un site.")
    return out


def public_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    s.update(DEFAULT_MARKET_SETTINGS)
    s.update(DEFAULT_ROBOT_SETTINGS)
    s.update(db.settings())
    key = s.pop("gemini_key", "") or ""
    s["gemini_key_set"] = bool(key)
    s["gemini_key_hint"] = ("••••" + key[-4:]) if len(key) >= 8 else ""
    return s


@app.get("/")
def index():
    return render_template("index.html", statuses=STATUS_LABELS,
                           rare_default=", ".join(DEFAULT_RARE_KEYWORDS),
                           exclude_default=", ".join(DEFAULT_EXCLUDE_KEYWORDS))


@app.get("/api/meta")
def meta():
    return jsonify(sources=source_meta(), categories=category_meta(),
                   intervals=[{"value": v, "label": l} for v, l in INTERVALS],
                   statuses=[{"id": k, "label": v} for k, v in STATUS_LABELS.items()],
                   default_interval=monitor.default_interval, cffi=has_cffi(), default_model=DEFAULT_MODEL,
                   types=[{"key": k, "label": v["label"]} for k, v in ITEM_TYPES.items()],
                   flags=FLAG_LABELS, popular_terms=POPULAR_TERMS)


@app.get("/api/watches")
def list_watches():
    nxt = monitor.next_runs()
    return jsonify([w | {"next_run": nxt.get(str(w["id"]))} for w in db.list_watches()])


@app.post("/api/watches")
def add_watch():
    try:
        wid = db.add_watch(clean_watch(request.get_json(force=True)))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    monitor.scan_now(wid)
    return jsonify(db.get_watch(wid)), 201


@app.patch("/api/watches/<int:wid>")
def update_watch(wid):
    try:
        db.update_watch(wid, clean_watch(request.get_json(force=True), partial=True))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    monitor.scan_now(wid)
    return jsonify(db.get_watch(wid))


@app.delete("/api/watches/<int:wid>")
def delete_watch(wid):
    db.delete_watch(wid)
    return "", 204


@app.get("/api/feed")
def feed():
    now = time.time()
    since = float(request.args.get("since") or 0)
    items = db.feed(
        since=since,
        only_deals=request.args.get("deals", "1") == "1",
        watch_id=int(request.args["watch"]) if request.args.get("watch") else None,
        source=request.args.get("source") or None,
    )
    return jsonify(now=now, items=items)


@app.get("/api/status")
def status():
    st = dict(monitor.status)
    st["sources"] = {k: dict(v) for k, v in monitor.status["sources"].items()}
    return jsonify({**st, **db.counts(), "interval": monitor.default_interval,
                    "ai": monitor.ai.status | {"ready": monitor.ai.ready()},
                    "market_counts": db.market_counts(), "tracker": dict(monitor.tracker.status)})


@app.post("/api/scan")
def scan():
    data = request.get_json(silent=True) or {}
    monitor.scan_now(int(data["watch"]) if data.get("watch") else None)
    return jsonify(ok=True)


@app.get("/api/settings")
def get_settings():
    return jsonify(public_settings())


@app.post("/api/settings")
def save_settings():
    data = request.get_json(force=True) or {}
    out = {}
    if data.get("clear_key"):
        out["gemini_key"] = ""
    elif (data.get("gemini_key") or "").strip():
        out["gemini_key"] = data["gemini_key"].strip()
    if "gemini_model" in data:
        out["gemini_model"] = (data["gemini_model"] or DEFAULT_MODEL).strip()
    if "ai_enabled" in data:
        out["ai_enabled"] = bool(data["ai_enabled"])
    if "ai_max_per_hour" in data:
        out["ai_max_per_hour"] = max(1, min(600, int(_num(data["ai_max_per_hour"]) or 30)))
    if "notify_mode" in data and data["notify_mode"] in ("all", "ai_ok"):
        out["notify_mode"] = data["notify_mode"]
    if "priority_score" in data:
        out["priority_score"] = max(0, min(100, float(_num(data["priority_score"]) or 85)))
    # veille marché, suivi des ventes, pépites
    if "market_terms" in data:
        out["market_terms"] = _join(data["market_terms"])
    bounds = {"market_interval": (120, 86400), "tracker_per_hour": (10, 1200), "gems_notify_score": (0, 100),
              "gems_ai_score": (0, 100), "buy_fee_fixed": (0, 50), "buy_fee_pct": (0, 50), "ship_in": (0, 100),
              "sell_fee_pct": (0, 60), "resale_factor": (0.3, 1.5), "min_profit": (0, 10000)}
    for k, (lo, hi) in bounds.items():
        if k in data and data[k] not in (None, ""):
            out[k] = max(lo, min(hi, float(_num(data[k]))))
    for k in ("tracker_enabled", "gems_auto_ai", "robot_require_ai"):
        if k in data:
            out[k] = bool(data[k])
    # robot
    if data.get("robot_mode") in ("off", "offers", "auto"):
        out["robot_mode"] = data["robot_mode"]
    if data.get("robot_pay") in ("manual", "auto"):
        out["robot_pay"] = data["robot_pay"]
    rb = {"robot_min_score": (0, 100), "robot_min_profit": (0, 10000), "robot_min_conf": (0, 1),
          "robot_max_item": (1, 100000), "robot_day_budget": (0, 1000000), "robot_day_items": (0, 1000),
          "robot_day_offers": (0, 1000), "robot_pause_until": (0, 1e11)}
    for k, (lo, hi) in rb.items():
        if k in data and data[k] not in (None, ""):
            out[k] = max(lo, min(hi, float(_num(data[k]))))
    if "robot_brands" in data:
        out["robot_brands"] = _join(data["robot_brands"])
    if "robot_offer_template" in data:
        out["robot_offer_template"] = (data["robot_offer_template"] or "").strip()[:500] or \
            DEFAULT_ROBOT_SETTINGS["robot_offer_template"]
    db.set_settings(out)
    return jsonify(public_settings())


@app.post("/api/settings/test-ai")
def test_ai():
    data = request.get_json(silent=True) or {}
    key = (data.get("gemini_key") or "").strip() or db.get_setting("gemini_key", "")
    if not key:
        return jsonify(ok=False, message="Aucune clé Gemini enregistrée")
    res = test_key(key, data.get("gemini_model") or db.get_setting("gemini_model", DEFAULT_MODEL))
    if res["ok"] and res.get("model"):
        db.set_settings({"gemini_model": res["model"]})
    return jsonify(res)


# ---------- analyste & pépites ----------
def _arg(name):
    v = request.args.get(name)
    return v.strip() if v and v.strip() else None


@app.get("/api/analyst")
def analyst_view():
    days = max(1 / 24, min(90.0, float(_num(request.args.get("days")) or 7)))
    res = analyst.top_sellers(db, monitor.market.idx, days, brand=_arg("brand"), itype=_arg("type"),
                              source=_arg("source"))
    return jsonify(res)


@app.get("/api/analyst/similar")
def analyst_similar():
    ref = _num(request.args.get("ref"))
    return jsonify(analyst.similar_to_buy(db, monitor.market.idx, request.args.get("key", ""), ref,
                                          monitor.market.settings()))


@app.get("/api/gems")
def gems_view():
    return jsonify(analyst.gems(
        db, budget=_num(request.args.get("budget")), min_profit=_num(request.args.get("min_profit")),
        itype=_arg("type"), brand=_arg("brand"), source=_arg("source"),
        min_conf=float(_num(request.args.get("min_conf")) or 0), sort=request.args.get("sort") or "score",
        fresh_days=float(_num(request.args.get("fresh")) or 3)))


@app.get("/api/brands")
def brands_view():
    idx = monitor.market.idx
    with idx.lock:
        top = [b for b, n in idx.brand_count.most_common(300) if n > 0 and b]
    return jsonify(top)


# ---------- catalogue de marques ----------
_verify_src = None
_verify_pool = ThreadPoolExecutor(max_workers=2)

@app.get("/api/brands/suggest")
def brands_suggest():
    return jsonify(monitor.brands.suggest(request.args.get("q", ""), int(request.args.get("limit") or 12)))


@app.get("/api/brands/all")
def brands_all():
    return jsonify(monitor.brands.all())


@app.post("/api/brands/verify")
def brands_verify():
    name = (request.get_json(force=True) or {}).get("name", "")

    def search(q):
        global _verify_src
        if _verify_src is None:
            from sources.vinted import VintedSource
            _verify_src = VintedSource(monitor.domain)
        fut = _verify_pool.submit(_verify_src.search, q)
        try:
            return fut.result(timeout=10)  # pas de réponse de Vinted : on se contente de la liste connue
        except Exception:
            return []
    return jsonify(monitor.brands.verify(name, search))


# ---------- robot ----------
@app.get("/api/robot")
def robot_view():
    r = monitor.robot
    s = r.settings()
    return jsonify(settings={k: s[k] for k in DEFAULT_ROBOT_SETTINGS}, today=r.today(), actions=r.actions(60),
                   executor=r.executor, portfolio=r.portfolio())


@app.get("/api/listings/<int:lid>/offer")
def listing_offer(lid):
    l = db.get_listing(lid)
    if not l:
        return jsonify(error="Annonce introuvable"), 404
    l = analyst.public_listing(l)
    s = monitor.robot.settings()
    return jsonify(suggest_offer(l, s) | {"rules": monitor.robot.check_rules(l, s)})


@app.post("/api/listings/<int:lid>/action")
def listing_action(lid):
    data = request.get_json(force=True) or {}
    l = db.get_listing(lid)
    if not l:
        return jsonify(error="Annonce introuvable"), 404
    kind = data.get("kind")
    if kind not in ("offer", "buy", "message"):
        return jsonify(error="Action inconnue"), 400
    l = analyst.public_listing(l)
    sug = suggest_offer(l, monitor.robot.settings())
    amount = _num(data.get("amount")) or (sug["offer"] if kind == "offer" else l.get("price"))
    message = data.get("message") or (sug["message"] if kind != "buy" else None)
    return jsonify(monitor.robot.create(l, kind, amount, message, "user"))


@app.get("/api/robot/next")
def robot_next():
    r = monitor.robot
    r.executor.update(last_poll=time.time(), platform=request.args.get("platform"),
                      version=request.args.get("version"))
    if request.args.get("logged_in") in ("0", "1"):
        r.executor["logged_in"] = request.args.get("logged_in") == "1"
    a = r.next_action(background=request.args.get("background") == "1")
    return jsonify(a or {})


@app.post("/api/robot/actions/<int:aid>")
def robot_report(aid):
    d = request.get_json(force=True) or {}
    a = monitor.robot.report(aid, d.get("status", "failed"), d.get("detail", ""), d.get("debug"), d.get("checkout_url"))
    return jsonify(a or {})


@app.post("/api/robot/actions/<int:aid>/cancel")
def robot_cancel(aid):
    monitor.robot.cancel(aid)
    return jsonify(ok=True)


@app.post("/api/robot/actions/<int:aid>/paid")
def robot_paid(aid):
    a = monitor.robot.report(aid, "paid", "Paiement confirmé par toi")
    return jsonify(a or {})


@app.post("/api/robot/executor")
def robot_executor():
    d = request.get_json(force=True) or {}
    if "logged_in" in d:
        monitor.robot.executor["logged_in"] = bool(d["logged_in"])
    monitor.robot.executor["last_poll"] = time.time()
    return jsonify(ok=True)


@app.post("/api/portfolio")
def portfolio_add():
    return jsonify(id=monitor.robot.add_manual(request.get_json(force=True) or {}))


@app.patch("/api/portfolio/<int:pid>")
def portfolio_update(pid):
    d = request.get_json(force=True) or {}
    for k in ("sale_price", "sale_fees", "buy_price", "buy_cost", "est_value"):
        if k in d:
            d[k] = _num(d[k])
    monitor.robot.update_item(pid, d)
    return jsonify(ok=True)


@app.delete("/api/portfolio/<int:pid>")
def portfolio_delete(pid):
    monitor.robot.delete_item(pid)
    return "", 204


@app.get("/api/listings/<int:lid>")
def listing_view(lid):
    res = monitor.market.comparables(lid)
    if not res:
        return jsonify(error="Annonce introuvable"), 404
    val = res["valuation"]
    return jsonify(listing=analyst.public_listing(res["listing"]), valuation=val,
                   basis=basis_text(val) if val else None, demand=res["demand"], flip=res["flip"],
                   flag_labels=[FLAG_LABELS.get(f, f) for f in res["flags"]],
                   comparables=[analyst.public_listing(c) for c in res["comparables"]])


@app.post("/api/listings/<int:lid>/identify")
def listing_identify(lid):
    if not monitor.ai.ready():
        return jsonify(ok=False, message="Ajoute ta clé Gemini dans les réglages pour utiliser l'identification."), 400
    if not db.get_listing(lid):
        return jsonify(ok=False, message="Annonce introuvable"), 404
    db.update_listing(lid, {"ai_status": "pending"})
    monitor.ai.enqueue(("identify", lid))
    return jsonify(ok=True, message="Identification lancée (Gemini + recherche Google)…")


def main():
    global db, monitor
    p = argparse.ArgumentParser(description="Surveillance des bonnes affaires mode")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--interval", type=int, default=60, help="fréquence par défaut des nouvelles recherches (s)")
    p.add_argument("--domain", default="www.vinted.fr")
    p.add_argument("--db", default="vinted_deals.db")
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    db = DB(args.db)
    monitor = Monitor(db, interval=args.interval, domain=args.domain)
    monitor.start()

    url = f"http://127.0.0.1:{args.port}"
    print(f"\n  Deals Mode tourne sur {url}  (Ctrl+C pour arrêter)")
    if not has_cffi():
        print("  Conseil : « pip install curl_cffi » aide à passer les protections de certains sites.")
    print()
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
