"""Deuxième tri par IA : Gemini regarde les photos et donne son avis sur l'affaire.

Seules les annonces qui ont passé le premier tri (score + décote + filtres) sont envoyées,
dans la limite d'un nombre d'analyses par heure réglable (quota gratuit de Gemini).
"""
from __future__ import annotations

import base64
import json
import logging
import queue
import re
import threading
import time
from typing import Callable

import requests

log = logging.getLogger("deals.ai")

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-3.8-flash"
FALLBACK_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-2.5-flash"]
MAX_IMAGE_BYTES = 3_500_000

DEFAULT_SETTINGS = {
    "gemini_key": "",
    "gemini_model": DEFAULT_MODEL,
    "ai_enabled": True,
    "ai_max_per_hour": 30,
    "notify_mode": "all",        # "all" = toutes les bonnes affaires, "ai_ok" = sauf celles que Gemini déconseille
    "priority_score": 85,
}

VERDICTS = {"acheter": "Acheter", "a_verifier": "À vérifier", "eviter": "À éviter"}

PROMPT = """Tu es un expert de la revente de mode de seconde main (authentification, état, cote du marché).
Un programme a repéré cette annonce comme une bonne affaire potentielle. Examine les photos jointes et les
informations ci-dessous, puis donne ton avis d'acheteur averti.

Annonce :
{context}

Réponds UNIQUEMENT avec un objet JSON valide, sans texte autour, avec exactement ces clés :
{{
  "verdict": "acheter" | "a_verifier" | "eviter",
  "note": entier de 0 à 10 (intérêt de l'affaire, 10 = excellente),
  "authenticite": "probable" | "doute" | "suspect" | "impossible_a_dire",
  "etat_photos": "état visible sur les photos, en quelques mots",
  "coherence": "les photos correspondent-elles au titre, à la marque et à l'état annoncés ? (court)",
  "valeur_estimee_eur": nombre (prix de revente réaliste d'occasion) ou null,
  "points_forts": ["2 à 4 points courts"],
  "points_attention": ["2 à 4 points courts : signes de contrefaçon, défauts, photos de catalogue, prix suspect…"],
  "resume": "1 ou 2 phrases en français, utiles dans une notification"
}}
Sois prudent : si les photos ne permettent pas de juger, dis-le et choisis "a_verifier"."""


class AIError(Exception):
    pass


class ModelNotFound(AIError):
    pass


class RateLimited(AIError):
    pass


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise AIError("Réponse de Gemini illisible")


def call_gemini(key: str, model: str, prompt: str, images: list[tuple[str, bytes]] | None = None,
                json_mode: bool = True, timeout: int = 60) -> str:
    parts: list[dict] = [{"text": prompt}]
    for mime, data in images or []:
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}})
    body: dict = {"contents": [{"role": "user", "parts": parts}],
                  "generationConfig": {"temperature": 0.2}}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    r = requests.post(API.format(model=model), json=body, timeout=timeout,
                      headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    if r.status_code == 404:
        raise ModelNotFound(f"Modèle « {model} » introuvable")
    if r.status_code == 429:
        raise RateLimited("Quota Gemini atteint, nouvelle tentative plus tard")
    if r.status_code in (400, 401, 403):
        msg = ""
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            pass
        if "API key" in msg or r.status_code in (401, 403):
            raise AIError("Clé Gemini refusée : vérifie-la dans les réglages")
        if "model" in msg.lower() and ("not found" in msg.lower() or "not supported" in msg.lower()):
            raise ModelNotFound(msg)
        raise AIError(f"Gemini : {msg or 'requête refusée'}")
    if r.status_code >= 400:
        raise AIError(f"Gemini : HTTP {r.status_code}")
    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        reason = (data.get("promptFeedback") or {}).get("blockReason")
        raise AIError(f"Gemini n'a pas répondu ({reason or 'réponse vide'})")
    texts = [p.get("text", "") for p in (cands[0].get("content") or {}).get("parts", [])
             if not p.get("thought")]
    return "".join(texts)


def fetch_image(url: str) -> tuple[str, bytes] | None:
    try:
        r = requests.get(url, timeout=15, stream=True,
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36",
                                  "Accept": "image/avif,image/webp,image/jpeg,image/png,*/*"})
        if r.status_code != 200:
            return None
        data = r.raw.read(MAX_IMAGE_BYTES + 1, decode_content=True)
        if len(data) > MAX_IMAGE_BYTES or len(data) < 500:
            return None
        mime = (r.headers.get("content-type") or "").split(";")[0].strip()
        if mime not in ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"):
            head = data[:12]
            mime = ("image/png" if head.startswith(b"\x89PNG") else
                    "image/webp" if head[8:12] == b"WEBP" else "image/jpeg")
        return mime, data
    except Exception:
        return None


class Analyzer(threading.Thread):
    """File d'attente d'analyses Gemini, traitée en arrière-plan."""

    def __init__(self, db, on_done: Callable[[int, dict | None, str | None], None]):
        super().__init__(daemon=True, name="gemini")
        self.db = db
        self.on_done = on_done
        self.q: queue.Queue[int] = queue.Queue()
        self.recent: list[float] = []
        self.status = {"last_error": None, "model": None, "analysed": 0, "queued": 0}

    def settings(self) -> dict:
        s = dict(DEFAULT_SETTINGS)
        s.update(self.db.settings())
        return s

    def ready(self) -> bool:
        s = self.settings()
        return bool(s.get("ai_enabled") and s.get("gemini_key"))

    def enqueue(self, item_id: int) -> None:
        self.q.put(item_id)
        self.status["queued"] = self.q.qsize()

    def run(self) -> None:
        for item_id in self.db.items_with_ai_status("pending"):  # reprise après redémarrage
            self.q.put(item_id)
        while True:
            item_id = self.q.get()
            self.status["queued"] = self.q.qsize()
            try:
                self._process(item_id)
            except Exception as e:  # ne jamais tuer le fil
                log.exception("Analyse %s", item_id)
                self.status["last_error"] = str(e)

    def _process(self, item_id: int) -> None:
        row = self.db.get_item(item_id)
        if not row:
            return
        s = self.settings()
        if not (s.get("ai_enabled") and s.get("gemini_key")):
            self.db.update_item(item_id, {"ai_status": "skipped"})
            self.on_done(item_id, None, "Analyse Gemini désactivée")
            return
        now = time.time()
        self.recent = [t for t in self.recent if now - t < 3600]
        if len(self.recent) >= int(s.get("ai_max_per_hour") or 30):
            self.db.update_item(item_id, {"ai_status": "skipped", "ai_summary": "Limite d'analyses par heure atteinte"})
            self.on_done(item_id, None, "limite horaire atteinte")
            return
        self.recent.append(now)
        try:
            result = self.analyse(row, s)
        except RateLimited as e:
            self.status["last_error"] = str(e)
            self.db.update_item(item_id, {"ai_status": "error", "ai_summary": str(e)})
            self.on_done(item_id, None, str(e))
            return
        except AIError as e:
            self.status["last_error"] = str(e)
            self.db.update_item(item_id, {"ai_status": "error", "ai_summary": str(e)})
            self.on_done(item_id, None, str(e))
            return
        self.status["last_error"] = None
        self.status["analysed"] += 1
        verdict = str(result.get("verdict") or "a_verifier").lower().replace("à", "a").replace(" ", "_")
        if verdict not in VERDICTS:
            verdict = "a_verifier"
        try:
            note = float(result.get("note"))
        except (TypeError, ValueError):
            note = None
        self.db.update_item(item_id, {"ai_status": "done", "ai_verdict": verdict, "ai_score": note,
                                      "ai_summary": str(result.get("resume") or "")[:400], "ai_data": result})
        self.on_done(item_id, result | {"verdict": verdict, "note": note}, None)

    def analyse(self, row: dict, s: dict) -> dict:
        photos = (row.get("photos") or [])[:3] or ([row["photo"]] if row.get("photo") else [])
        images = [img for img in (fetch_image(u) for u in photos) if img]
        reasons = row.get("reasons") or []
        lines = [
            f"- Site : {row.get('source')}",
            f"- Titre : {row.get('title')}",
            f"- Marque : {row.get('brand') or 'non renseignée'}",
            f"- Taille : {row.get('size') or 'non renseignée'}",
            f"- État déclaré : {row.get('status') or 'non renseigné'}",
            f"- Prix : {row.get('price'):.2f} €" + (f" (soit {row.get('orig_price')} {row.get('currency')})"
                                                     if row.get('currency') not in (None, 'EUR') else ""),
        ]
        if row.get("ref_price"):
            lines.append(f"- Prix de référence calculé : {row['ref_price']:.0f} € (décote {row.get('discount') or 0:.0%})")
        if reasons:
            lines.append("- Pourquoi le programme l'a retenue : " + " ; ".join(reasons))
        if not images:
            lines.append("- (aucune photo n'a pu être téléchargée : juge sur le texte et choisis plutôt a_verifier)")
        prompt = PROMPT.format(context="\n".join(lines))

        models = [s.get("gemini_model") or DEFAULT_MODEL] + FALLBACK_MODELS
        if self.status.get("model"):
            models.insert(0, self.status["model"])
        tried = []
        for model in dict.fromkeys(models):
            try:
                text = call_gemini(s["gemini_key"], model, prompt, images)
                self.status["model"] = model
                return _extract_json(text)
            except ModelNotFound:
                tried.append(model)
                continue
        raise AIError("Aucun modèle Gemini disponible (essayés : " + ", ".join(tried) + ")")


def test_key(key: str, model: str) -> dict:
    """Vérifie la clé avec une requête minuscule. Renvoie {'ok': bool, 'model': ..., 'message': ...}."""
    for m in dict.fromkeys([model or DEFAULT_MODEL] + FALLBACK_MODELS):
        try:
            txt = call_gemini(key, m, "Réponds uniquement par le mot OK.", json_mode=False, timeout=30)
            return {"ok": True, "model": m, "message": f"Clé valide — modèle {m} ({txt.strip()[:20]})"}
        except ModelNotFound:
            continue
        except AIError as e:
            return {"ok": False, "model": m, "message": str(e)}
        except requests.RequestException as e:
            return {"ok": False, "model": m, "message": f"Connexion impossible : {e}"}
    return {"ok": False, "model": None, "message": "Aucun modèle Gemini disponible pour cette clé"}
