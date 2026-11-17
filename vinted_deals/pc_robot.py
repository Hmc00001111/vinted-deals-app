"""Robot sur PC : une fenêtre Chrome dédiée (profil mémorisé) où tu te connectes une fois à Vinted,
puis le robot y exécute les offres et les achats (même script que sur Android : templates/robot.js).

Nécessite :  pip install playwright   puis   playwright install chromium
(si Google Chrome est installé, il est utilisé en priorité : il passe mieux les protections de Vinted).
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from pathlib import Path

log = logging.getLogger("deals.pcrobot")

SCRIPT_PATH = Path(__file__).with_name("templates") / "robot.js"
VINTED = "https://www.vinted.fr/"
INSTALL_HELP = "Pour le robot sur PC : « pip install playwright » puis « playwright install chromium », et relance l'appli."


def available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:
        return False


class PCExecutor(threading.Thread):
    """Exécuteur PC : toutes les commandes Playwright passent par ce seul fil."""

    def __init__(self, robot, profile_dir: str):
        super().__init__(daemon=True, name="pc-robot")
        self.robot = robot
        self.profile_dir = profile_dir
        self.cmds: queue.Queue = queue.Queue()
        self.pw = None
        self.ctx = None
        self.open_pages: list = []       # pages de paiement laissées ouvertes (on surveille la confirmation)
        self.states: dict = {}           # id(page) → {"action", "results"}
        self.last_login_check = 0.0
        self.status = {"available": available(), "browser": None, "error": None if available() else INSTALL_HELP}
        robot.executor.update(platform="pc", pc=self.status)

    # ---------- commandes depuis l'interface ----------
    def open_login(self) -> None:
        self.cmds.put(("open", VINTED))

    def open_url(self, url: str) -> None:
        self.cmds.put(("open", url))

    # ---------- navigateur ----------
    def _browser(self):
        if self.ctx:
            try:
                self.ctx.pages  # encore vivant ?
                return self.ctx
            except Exception:
                self.ctx = None
        from playwright.sync_api import sync_playwright
        if self.pw is None:
            self.pw = sync_playwright().start()
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True)
        opts = dict(user_data_dir=self.profile_dir, headless=os.environ.get("VD_ROBOT_HEADLESS") == "1", locale="fr-FR",
                    viewport={"width": 1280, "height": 900}, args=["--disable-blink-features=AutomationControlled"])
        try:
            self.ctx = self.pw.chromium.launch_persistent_context(channel="chrome", **opts)
            self.status["browser"] = "Google Chrome"
        except Exception:
            self.ctx = self.pw.chromium.launch_persistent_context(**opts)
            self.status["browser"] = "Chromium"
        self.ctx.on("close", lambda *_: setattr(self, "ctx", None))
        return self.ctx

    def _new_page(self, action: dict | None):
        ctx = self._browser()
        page = ctx.new_page()
        if action is not None:
            script = SCRIPT_PATH.read_text(encoding="utf-8")
            state = {"action": action, "results": []}
            page.expose_function("vdReport", lambda s: state["results"].append(json.loads(s)))
            stub = ("if (window.top === window) { window.RobotBridge = { action: () => %s, "
                    "report: (s) => window.vdReport(s) }; }" % json.dumps(json.dumps(action)))
            page.add_init_script(stub + "\nif (window.top === window) {\n" + script + "\n}")
            self.states[id(page)] = state
            page.on("close", lambda *_: self.states.pop(id(page), None))
        return page

    # ---------- exécution ----------
    def _run(self, action: dict, timeout: float = 120) -> dict:
        page = self._new_page(action)
        state = self.states[id(page)]
        try:
            page.goto(action.get("url") or VINTED, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            page.close()
            return {"status": "failed", "detail": f"Page inaccessible : {e.__class__.__name__}"}
        t0 = time.time()
        while time.time() - t0 < timeout:
            page.wait_for_timeout(500)
            for r in state["results"]:
                if r.get("status") not in ("running", "info", "checkout"):
                    page.close()
                    return r
                if r.get("status") == "info":
                    page.close()
                    return r
                if r.get("status") == "checkout":
                    # paiement à valider par toi : on laisse la page ouverte et on surveille la confirmation
                    state["results"] = []
                    self.open_pages.append((page, action, time.time()))
                    try:
                        page.bring_to_front()
                    except Exception:
                        pass
                    return r
        page.close()
        return {"status": "failed", "detail": "Délai dépassé (page trop lente ou bloquée)"}

    def _watch_open_pages(self) -> None:
        keep = []
        for page, action, t0 in self.open_pages:
            try:
                if page.is_closed():
                    continue
                page.wait_for_timeout(100)
                res = self.states.get(id(page), {}).get("results", [])
                paid = next((r for r in res if r.get("status") == "paid"), None)
                if paid:
                    self.robot.report(action["id"], "paid", paid.get("detail") or "Paiement effectué")
                    continue
                if time.time() - t0 < 30 * 60:
                    keep.append((page, action, t0))
            except Exception:
                continue
        self.open_pages = keep

    def _login_check(self) -> None:
        r = self._run({"kind": "login_check", "url": VINTED}, timeout=40)
        if "logged_in" in r:
            self.robot.executor["logged_in"] = bool(r["logged_in"])

    def run(self) -> None:
        if not self.status["available"]:
            log.info(INSTALL_HELP)
            return
        while True:
            try:
                self._tick()
            except Exception as e:
                log.exception("Robot PC")
                self.status["error"] = f"{e.__class__.__name__} : {e}"[:300]
                self.ctx = None
                time.sleep(10)

    def _tick(self) -> None:
        r = self.robot
        r.executor.update(last_poll=time.time(), platform="pc")
        # commandes de l'interface (connexion, ouvrir une page)
        try:
            cmd, arg = self.cmds.get(timeout=0.1)
            if cmd == "open":
                page = self._new_page(None)
                page.goto(arg, wait_until="domcontentloaded", timeout=45000)
                page.bring_to_front()
                self.last_login_check = time.time() - 30 * 60 + 90  # on revérifie la connexion dans 1 min 30
        except queue.Empty:
            pass
        if self.open_pages:
            self._watch_open_pages()
        need = r.settings().get("robot_mode") != "off" or self.ctx is not None
        if need and time.time() - self.last_login_check > 30 * 60:
            self.last_login_check = time.time()
            self._login_check()
            self.status["error"] = None
        a = r.next_action(background=False)
        if not a:
            time.sleep(3)
            return
        res = self._run(a, timeout=150)
        if "logged_in" in res:
            r.executor["logged_in"] = bool(res["logged_in"])
        r.report(a["id"], res.get("status", "failed"), res.get("detail", ""), res.get("debug"), res.get("checkout_url"))
        self.status["error"] = None
