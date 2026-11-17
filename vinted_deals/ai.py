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


IDENTIFY_PROMPT = """Tu es un expert de l'achat-revente de mode d'occasion (Vinted, Vestiaire, Grailed, eBay).
Identifie précisément cet article à partir des PHOTOS jointes et du texte de l'annonce, puis estime sa
valeur de revente d'occasion. Utilise la recherche Google : cherche le modèle exact (nom, référence,
collection, année) et des prix réels d'articles identiques ou très proches, d'occasion, en Europe.
Tiens compte de l'état annoncé et de l'état visible.

Annonce :
{context}

Réponds UNIQUEMENT avec un objet JSON valide (pas de texte autour, pas de ```), avec ces clés :
{{
  "marque": "marque exacte",
  "type": "type d'article (pull, jean, veste, baskets…)",
  "modele_exact": "nom précis du modèle, ou null si impossible à dire",
  "reference": "référence / collection / année si trouvée, sinon null",
  "matiere": "matière principale si visible ou indiquée, sinon null",
  "prix_neuf_eur": nombre ou null,
  "prix_occasion_eur": {{"bas": nombre, "median": nombre, "haut": nombre}},
  "confiance": "haute" | "moyenne" | "faible",
  "authenticite": "probable" | "doute" | "suspect" | "impossible_a_dire",
  "demande": "forte" | "moyenne" | "faible",
  "prix_achat_max_eur": nombre (prix maximum à payer pour revendre avec profit),
  "prix_revente_conseille_eur": nombre,
  "points_attention": ["2 à 4 points courts"],
  "resume": "2 phrases max en français : ce que c'est, ce que ça vaut, faut-il l'acheter pour revendre"
}}"""


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


class SlowModel(AIError):
    pass


def _thinking(model: str, effort: str) -> dict | None:
    """Réglage de « réflexion » : on la réduit, sinon un modèle récent peut mettre 30 s+ à répondre."""
    m = model.lower()
    if m.startswith("gemini-2.5"):
        return {"thinkingBudget": 0 if "flash" in m else 128}
    if m.startswith("gemini-3") or m.startswith("gemini-4"):
        return {"thinkingLevel": effort}
    return None


class NoGrounding(AIError):
    pass


def call_gemini(key: str, model: str, prompt: str, images: list[tuple[str, bytes]] | None = None,
                json_mode: bool = True, timeout: int = 90, effort: str = "low") -> str:
    data = generate(key, model, prompt, images, json_mode, timeout, effort)
    return response_text(data)


def response_text(data: dict) -> str:
    cands = data.get("candidates") or []
    if not cands:
        reason = (data.get("promptFeedback") or {}).get("blockReason")
        raise AIError(f"Gemini n'a pas répondu ({reason or 'réponse vide'})")
    texts = [p.get("text", "") for p in (cands[0].get("content") or {}).get("parts", [])
             if not p.get("thought")]
    return "".join(texts)


def grounding_sources(data: dict) -> list[dict]:
    """Pages web consultées par Gemini lors de sa recherche Google."""
    out = []
    for c in (data.get("candidates") or [])[:1]:
        for ch in ((c.get("groundingMetadata") or {}).get("groundingChunks") or []):
            web = ch.get("web") or {}
            if web.get("uri"):
                out.append({"url": web["uri"], "title": web.get("title") or web.get("domain") or "source"})
    return out[:8]


def generate(key: str, model: str, prompt: str, images: list[tuple[str, bytes]] | None = None,
             json_mode: bool = True, timeout: int = 90, effort: str = "low", google_search: bool = False) -> dict:
    parts: list[dict] = [{"text": prompt}]
    for mime, data in images or []:
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}})
    gen: dict = {"temperature": 0.2}
    if json_mode:
        gen["responseMimeType"] = "application/json"
    think = _thinking(model, effort)
    if think:
        gen["thinkingConfig"] = think
    body: dict = {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen}
    if google_search:
        body["tools"] = [{"google_search": {}}]
        gen.pop("responseMimeType", None)  # certains modèles refusent JSON + outils ensemble
    for attempt in range(2):
        try:
            r = requests.post(API.format(model=model), json=body, timeout=(15, timeout),
                              headers={"x-goog-api-key": key, "Content-Type": "application/json"})
        except requests.Timeout:
            raise SlowModel(f"{model} met trop de temps à répondre")
        if r.status_code == 400 and "thinkingConfig" in gen and "think" in r.text.lower():
            gen.pop("thinkingConfig")  # modèle qui n'accepte pas ce réglage : on réessaie sans
            continue
        break
    if r.status_code == 404:
        raise ModelNotFound(f"Modèle « {model} » introuvable")
    if r.status_code == 429:
        raise RateLimited("Quota Gemini atteint, nouvelle tentative plus tard")
    if r.status_code in (500, 502, 503, 504):
        raise SlowModel(f"{model} est surchargé (HTTP {r.status_code})")
    if r.status_code in (400, 401, 403):
        msg = ""
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            pass
        if google_search and r.status_code == 400 and any(w in msg.lower() for w in ("tool", "search", "ground")):
            raise NoGrounding(msg)
        if "API key" in msg or r.status_code in (401, 403):
            raise AIError("Clé Gemini refusée : vérifie-la dans les réglages")
        if "model" in msg.lower() and ("not found" in msg.lower() or "not supported" in msg.lower()):
            raise ModelNotFound(msg)
        raise AIError(f"Gemini : {msg or 'requête refusée'}")
    if r.status_code >= 400:
        raise AIError(f"Gemini : HTTP {r.status_code}")
    return r.json()


def list_models(key: str) -> list[str]:
    """Modèles accessibles avec cette clé (réponse immédiate, sans génération)."""
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         params={"pageSize": 1000}, headers={"x-goog-api-key": key}, timeout=(10, 20))
    except requests.RequestException as e:
        raise AIError(f"Connexion à Google impossible : {e.__class__.__name__}")
    if r.status_code in (400, 401, 403):
        raise AIError("Clé Gemini refusée : vérifie-la (copie-la en entier depuis AI Studio)")
    if r.status_code >= 400:
        raise AIError(f"Gemini : HTTP {r.status_code}")
    out = []
    for m in r.json().get("models") or []:
        name = str(m.get("name", "")).replace("models/", "")
        if "generateContent" in (m.get("supportedGenerationMethods") or []) and name.startswith("gemini"):
            out.append(name)
    return out


def _version(name: str) -> tuple:
    import re as _re
    m = _re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    return (float(m.group(1)) if m else 0.0,)


def candidate_models(preferred: str | None, available: list[str] | None) -> list[str]:
    """Ordre d'essai : le modèle choisi, puis les « flash » disponibles du plus récent au plus ancien."""
    base = [preferred or DEFAULT_MODEL] + FALLBACK_MODELS
    if available:
        flash = [m for m in available if "flash" in m and not any(x in m for x in ("image", "tts", "audio", "live", "exp"))]
        flash.sort(key=lambda m: (_version(m), "lite" not in m, "preview" not in m), reverse=True)
        base = ([preferred] if preferred in available else []) + flash + [m for m in base if m in available]
    return list(dict.fromkeys(m for m in base if m))


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
        self.status = {"last_error": None, "model": None, "analysed": 0, "queued": 0, "grounding": None}
        self.available: list[str] | None = None
        self.on_identified: Callable[[int], None] | None = None
        self._no_search_until = 0.0

    def settings(self) -> dict:
        s = dict(DEFAULT_SETTINGS)
        s.update(self.db.settings())
        return s

    def ready(self) -> bool:
        s = self.settings()
        return bool(s.get("ai_enabled") and s.get("gemini_key"))

    def enqueue(self, job) -> None:
        """job : id d'une bonne affaire (int) ou ("identify", id d'annonce du marché)."""
        self.q.put(job)
        self.status["queued"] = self.q.qsize()

    def run(self) -> None:
        for item_id in self.db.items_with_ai_status("pending"):  # reprise après redémarrage
            self.q.put(item_id)
        for l in self.db.query_listings("ai_status = 'pending'", [], limit=50):
            self.q.put(("identify", l["id"]))
        while True:
            job = self.q.get()
            self.status["queued"] = self.q.qsize()
            try:
                if isinstance(job, tuple) and job[0] == "identify":
                    self._identify(job[1])
                else:
                    self._process(job)
            except Exception as e:  # ne jamais tuer le fil
                log.exception("Analyse %s", job)
                self.status["last_error"] = str(e)

    def _quota_ok(self, s: dict) -> bool:
        now = time.time()
        self.recent = [t for t in self.recent if now - t < 3600]
        if len(self.recent) >= int(s.get("ai_max_per_hour") or 30):
            return False
        self.recent.append(now)
        return True

    def _identify(self, lid: int) -> None:
        l = self.db.get_listing(lid)
        if not l:
            return
        s = self.settings()
        if not (s.get("ai_enabled") and s.get("gemini_key")):
            self.db.update_listing(lid, {"ai_status": None})
            return
        if not self._quota_ok(s):
            self.db.update_listing(lid, {"ai_status": "error",
                                         "ai_data": {"erreur": "Limite d'analyses par heure atteinte"}})
            return
        try:
            result = self.identify(l, s)
        except AIError as e:
            self.status["last_error"] = str(e)
            self.db.update_listing(lid, {"ai_status": "error", "ai_data": {"erreur": str(e)}, "ai_at": time.time()})
            return
        self.status["last_error"] = None
        self.status["analysed"] += 1
        self.db.update_listing(lid, {"ai_status": "done", "ai_data": result, "ai_at": time.time()})
        if self.on_identified:
            self.on_identified(lid)

    def identify(self, l: dict, s: dict) -> dict:
        """Identification du modèle exact + valeur de revente, avec recherche Google (si disponible)."""
        photos = (l.get("photos") or [])[:3] or ([l["photo"]] if l.get("photo") else [])
        images = [img for img in (fetch_image(u) for u in photos) if img]
        lines = [
            f"- Site : {l.get('source')}", f"- Titre : {l.get('title')}",
            f"- Marque : {l.get('brand') or 'non renseignée'}", f"- Taille : {l.get('size') or 'non renseignée'}",
            f"- État déclaré : {l.get('status') or 'non renseigné'}", f"- Prix demandé : {l.get('price') or 0:.2f} €",
        ]
        if l.get("value"):
            lines.append(f"- Valeur estimée par notre base d'annonces similaires : {l['value']:.0f} € "
                         f"({l.get('value_n') or 0} comparables)")
        if not images:
            lines.append("- (photos indisponibles : base-toi sur le texte, confiance faible)")
        prompt = IDENTIFY_PROMPT.format(context="\n".join(lines))
        if self.available is None:
            try:
                self.available = list_models(s["gemini_key"])
            except AIError:
                self.available = []
        models = candidate_models(self.status.get("model") or s.get("gemini_model"), self.available or None)
        problems = []
        use_search = self.status.get("grounding") is not False or time.time() > self._no_search_until
        for model in models[:4]:
            try:
                try:
                    data = generate(s["gemini_key"], model, prompt, images, json_mode=not use_search,
                                    timeout=120, effort="low", google_search=use_search)
                except (NoGrounding, RateLimited) as e:
                    if not use_search:
                        raise
                    log.info("Recherche Google indisponible avec Gemini (%s) : identification sans", e)
                    self.status["grounding"] = False
                    self._no_search_until = time.time() + 3600  # on réessaiera dans une heure
                    use_search = False
                    data = generate(s["gemini_key"], model, prompt, images, json_mode=True, timeout=120)
                self.status["model"] = model
                result = _extract_json(response_text(data))
                result["sources"] = grounding_sources(data)
                result["google"] = bool(use_search)
                if use_search:
                    self.status["grounding"] = True
                return result
            except (ModelNotFound, SlowModel) as e:
                problems.append(str(e))
                continue
        raise AIError("Gemini indisponible pour le moment (" + " ; ".join(problems[:2]) + ")")

    def _process(self, item_id: int) -> None:
        row = self.db.get_item(item_id)
        if not row:
            return
        s = self.settings()
        if not (s.get("ai_enabled") and s.get("gemini_key")):
            self.db.update_item(item_id, {"ai_status": "skipped"})
            self.on_done(item_id, None, "Analyse Gemini désactivée")
            return
        if not self._quota_ok(s):
            self.db.update_item(item_id, {"ai_status": "skipped", "ai_summary": "Limite d'analyses par heure atteinte"})
            self.on_done(item_id, None, "limite horaire atteinte")
            return
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

        if self.available is None:
            try:
                self.available = list_models(s["gemini_key"])
            except AIError:
                self.available = []
        models = candidate_models(self.status.get("model") or s.get("gemini_model"), self.available or None)
        problems = []
        for model in models[:4]:
            try:
                text = call_gemini(s["gemini_key"], model, prompt, images, effort="low")
                self.status["model"] = model
                return _extract_json(text)
            except (ModelNotFound, SlowModel) as e:
                problems.append(str(e))
                continue
        raise AIError("Gemini indisponible pour le moment (" + " ; ".join(problems[:2]) + ")")


def test_key(key: str, model: str) -> dict:
    """Vérifie la clé (liste des modèles, instantané) puis fait un mini essai de génération."""
    try:
        available = list_models(key)
    except AIError as e:
        return {"ok": False, "model": None, "message": str(e)}
    if not available:
        return {"ok": False, "model": None, "message": "Clé valide mais aucun modèle Gemini accessible avec ce compte"}
    slow = []
    for m in candidate_models(model, available)[:4]:
        try:
            txt = call_gemini(key, m, "Réponds uniquement par le mot OK.", json_mode=False,
                              timeout=45, effort="minimal")
            return {"ok": True, "model": m, "models": available,
                    "message": f"Clé valide ✓ — modèle utilisé : {m}"}
        except (ModelNotFound, SlowModel) as e:
            slow.append(m)
            continue
        except RateLimited:
            return {"ok": True, "model": m, "models": available,
                    "message": "Clé valide ✓ — quota momentanément atteint, l'analyse reprendra toute seule"}
        except AIError as e:
            return {"ok": False, "model": m, "message": str(e)}
    return {"ok": True, "model": None, "models": available,
            "message": "Clé valide ✓, mais Gemini répond lentement en ce moment (" + ", ".join(slow) +
                       "). L'analyse réessaiera automatiquement."}
