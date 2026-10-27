"""Point d'entrée Android.

Démarre exactement le même programme que sur PC (app.py + monitor.py + scoring.py…),
mais :
  - la base SQLite est rangée dans le dossier privé de l'application ;
  - l'interface HTML est fournie par l'application (assets) ;
  - chaque bonne affaire déclenche une notification Android native.
"""
from __future__ import annotations

import logging
import os
import threading

_lock = threading.Lock()
_started = False
log = logging.getLogger("vinted.android")


def _android_notify(row, ai, priority) -> None:
    from java import jclass  # fourni par Chaquopy

    notifier = jclass("com.houssem.vinteddeals.Notifier")
    discount = f" (−{row['discount']:.0%})" if row.get("discount") and row["discount"] > 0 else ""
    title = f"{'⭐ ' if priority else ''}{row['price']:.2f} €{discount} · {row['title']}"[:120]
    details = " · ".join(x for x in (row.get("source_label"), row.get("brand"), row.get("size"), row.get("status")) if x)
    lines = [details]
    if ai:
        from ai import VERDICTS
        note = f" {ai['note']:.0f}/10" if isinstance(ai.get("note"), (int, float)) else ""
        lines.append(f"Gemini : {VERDICTS.get(ai.get('verdict'), '')}{note} — {ai.get('resume') or ''}")
    lines.append(" · ".join((row.get("reasons") or [])[:2]))
    lines.append(f"Score {row.get('score', 0):.0f}/100 — {row.get('watch_name', '')}")
    notifier.deal(int(row["id"]), title, "\n".join(x for x in lines if x), row["url"], bool(priority))


def start(files_dir: str, html: str, port: int,
          domain: str = "www.vinted.fr", interval: int = 60) -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True

    os.chdir(files_dir)  # debug_last_response.json atterrit ici
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    from jinja2 import DictLoader

    import app as web
    import monitor as mon
    from db import DB

    web.app.jinja_loader = DictLoader({"index.html": html})
    web.db = DB(os.path.join(files_dir, "vinted_deals.db"))
    web.monitor = mon.Monitor(web.db, interval=interval, domain=domain)
    mon.NOTIFY_HOOKS.append(_android_notify)
    web.monitor.start()

    server = threading.Thread(
        target=web.app.run, name="flask",
        kwargs={"host": "127.0.0.1", "port": port, "threaded": True,
                "debug": False, "use_reloader": False},
        daemon=True,
    )
    server.start()
    log.info("Vinted Deals démarré sur le port %s", port)
