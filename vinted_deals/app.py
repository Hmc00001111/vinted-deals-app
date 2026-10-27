"""Vinted Deals — interface web locale + surveillance continue.

Lancement :  python app.py            (ouvre http://127.0.0.1:5000)
Options   :  python app.py --interval 90 --domain www.vinted.be --port 8000
"""
from __future__ import annotations

import argparse
import logging
import threading
import webbrowser

from flask import Flask, jsonify, render_template, request

from db import DB
from monitor import Monitor
from scoring import DEFAULT_EXCLUDE_KEYWORDS, DEFAULT_RARE_KEYWORDS
from vinted_client import STATUS_LABELS, VintedClient

app = Flask(__name__)
db: DB
monitor: Monitor


def _num(v):
    if v in (None, ""):
        return None
    return float(str(v).replace(",", "."))


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
    if "active" in data:
        out["active"] = 1 if data["active"] else 0
    if not partial:
        if not out.get("name"):
            out["name"] = out.get("search_text") or out.get("brands") or "Recherche"
        if not (out.get("search_text") or out.get("brands")):
            raise ValueError("Indique au moins des mots-clés ou une marque.")
        out.setdefault("min_score", 55)
    return out


@app.get("/")
def index():
    return render_template("index.html", statuses=STATUS_LABELS,
                           rare_default=", ".join(DEFAULT_RARE_KEYWORDS),
                           exclude_default=", ".join(DEFAULT_EXCLUDE_KEYWORDS))


@app.get("/api/watches")
def list_watches():
    return jsonify(db.list_watches())


@app.post("/api/watches")
def add_watch():
    try:
        wid = db.add_watch(clean_watch(request.get_json(force=True)))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    monitor.scan_now()
    return jsonify(db.get_watch(wid)), 201


@app.patch("/api/watches/<int:wid>")
def update_watch(wid):
    db.update_watch(wid, clean_watch(request.get_json(force=True), partial=True))
    return jsonify(db.get_watch(wid))


@app.delete("/api/watches/<int:wid>")
def delete_watch(wid):
    db.delete_watch(wid)
    return "", 204


@app.get("/api/feed")
def feed():
    return jsonify(db.feed(
        after_id=int(request.args.get("after", 0)),
        only_deals=request.args.get("deals", "1") == "1",
        watch_id=int(request.args["watch"]) if request.args.get("watch") else None,
    ))


@app.get("/api/status")
def status():
    return jsonify({**monitor.status, **db.counts(), "interval": monitor.interval})


@app.post("/api/scan")
def scan():
    monitor.scan_now()
    return jsonify(ok=True)


def main():
    global db, monitor
    p = argparse.ArgumentParser(description="Surveillance des bonnes affaires Vinted")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--interval", type=int, default=60, help="secondes entre deux cycles")
    p.add_argument("--domain", default="www.vinted.fr")
    p.add_argument("--db", default="vinted_deals.db")
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    db = DB(args.db)
    monitor = Monitor(db, VintedClient(args.domain), interval=args.interval)
    monitor.start()

    url = f"http://127.0.0.1:{args.port}"
    print(f"\n  Vinted Deals tourne sur {url}  (Ctrl+C pour arrêter)\n")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
