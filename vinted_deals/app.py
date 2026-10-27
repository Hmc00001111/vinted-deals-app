"""Deals Mode — interface web locale + surveillance continue de plusieurs sites.

Lancement :  python app.py            (ouvre http://127.0.0.1:5000)
Options   :  python app.py --interval 90 --domain www.vinted.be --port 8000
"""
from __future__ import annotations

import argparse
import logging
import threading
import time
import webbrowser

from flask import Flask, jsonify, render_template, request

from ai import DEFAULT_MODEL, DEFAULT_SETTINGS, test_key
from categories import CATEGORIES, category_meta
from db import DB
from monitor import MIN_INTERVAL, Monitor
from scoring import DEFAULT_EXCLUDE_KEYWORDS, DEFAULT_RARE_KEYWORDS
from sources import SOURCES, STATUS_LABELS, source_meta
from sources.base import has_cffi

app = Flask(__name__)
db: DB
monitor: Monitor

INTERVALS = [(30, "30 s"), (60, "1 min"), (120, "2 min"), (300, "5 min"), (600, "10 min"),
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
                   default_interval=monitor.default_interval, cffi=has_cffi(), default_model=DEFAULT_MODEL)


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
                    "ai": monitor.ai.status | {"ready": monitor.ai.ready()}})


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
