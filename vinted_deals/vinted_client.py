"""Client pour l'API interne (non officielle) de Vinted — version septembre 2026.

Depuis septembre 2026, l'ancienne route www.vinted.xx/api/v2/catalog/items renvoie 404.
La recherche passe désormais par :

    GET https://api.vinted.<tld>/svc-catalogue/items

avec une identité anonyme récupérée sur la page d'accueil :
  - cookie  access_token_web
  - en-tête X-Anon-Id (renvoyé par la page d'accueil, à renvoyer à l'API)

Les filtres prennent la forme attribute_ids[brand], attribute_ids[status], etc.
Un paramètre vide (price_from= …) provoque une erreur 400 : on ne les envoie jamais vides.

En cas de souci, la dernière réponse brute de l'API est écrite dans
debug_last_response.json pour pouvoir adapter le parsing.
"""
from __future__ import annotations

import json
import logging
import random
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import requests

log = logging.getLogger("vinted.client")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.6 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0",
]

LOCALES = {"fr": "fr-FR", "be": "fr-BE", "es": "es-ES", "it": "it-IT", "de": "de-DE",
           "nl": "nl-NL", "pl": "pl-PL", "pt": "pt-PT", "lu": "fr-LU", "co.uk": "en-GB",
           "com": "en-US", "at": "de-AT", "cz": "cs-CZ", "lt": "lt-LT", "se": "sv-SE"}

DEBUG_FILE = "debug_last_response.json"

# Identifiants d'état utilisés par Vinted
STATUS_LABELS = {
    6: "Neuf avec étiquette",
    1: "Neuf sans étiquette",
    2: "Très bon état",
    3: "Bon état",
    4: "Satisfaisant",
}


def _plain(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


_LABEL_TO_ID = {_plain(v): k for k, v in STATUS_LABELS.items()}
_LABEL_TO_ID.update({
    "new with tags": 6, "new without tags": 1, "very good": 2, "good": 3,
    "satisfactory": 4, "neuf": 1, "nuovo con cartellino": 6, "nuovo senza cartellino": 1,
    "ottime": 2, "buone": 3, "nuevo con etiquetas": 6, "nuevo sin etiquetas": 1,
    "muy bueno": 2, "bueno": 3, "neu mit etikett": 6, "neu ohne etikett": 1,
    "sehr gut": 2, "gut": 3, "zufriedenstellend": 4,
})


def status_to_id(label: str) -> int | None:
    p = _plain(label)
    if p in _LABEL_TO_ID:
        return _LABEL_TO_ID[p]
    # le plus long libellé contenu dans le texte (ex. « L · Très bon état »)
    best = None
    for k, v in _LABEL_TO_ID.items():
        if len(k) > 3 and k in p and (best is None or len(k) > len(best[0])):
            best = (k, v)
    return best[1] if best else None


class VintedError(Exception):
    pass


@dataclass
class Item:
    id: int
    title: str
    price: float
    total_price: float | None
    currency: str
    brand: str
    size: str
    status: str
    status_id: int | None
    url: str
    photo: str | None
    favourites: int
    views: int
    seller: str
    published_at: float | None
    raw: dict = field(default_factory=dict, repr=False)


def _money(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("amount")
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_item(d: dict, base: str) -> Item:
    box = d.get("item_box") or {}
    title = d.get("title") or ""

    raw_price = d.get("price")
    currency = (raw_price.get("currency_code") if isinstance(raw_price, dict) else None) \
        or d.get("currency") or "EUR"

    # Marque : brand_title, sinon première ligne de la vignette (si ≠ titre)
    brand = d.get("brand_title") or ""
    if not brand and isinstance(d.get("brand"), dict):
        brand = d["brand"].get("title") or ""
    first = (box.get("first_line") or "").strip()
    if not brand and first and first != title:
        brand = first

    # Taille / état : champs dédiés, sinon deuxième ligne « taille · état »
    size = d.get("size_title") or ""
    status = d.get("status") or ""
    if isinstance(status, dict):
        status = status.get("title", "")
    second = (box.get("second_line") or "").strip()
    if second and (not size or not status):
        parts = [p.strip() for p in second.replace("•", "·").split("·") if p.strip()]
        if len(parts) >= 2:
            size = size or parts[0]
            status = status or parts[-1]
        elif parts:
            if status_to_id(parts[0]):
                status = status or parts[0]
            else:
                size = size or parts[0]
    status_id = d.get("status_id") or status_to_id(str(status)) \
        or status_to_id(box.get("accessibility_label") or "")

    photo = d.get("photo") or (d.get("photos") or [{}])[0] or {}
    photo_url = photo.get("url") or photo.get("full_size_url")
    if not photo_url:
        thumbs = photo.get("thumbnails") or []
        photo_url = thumbs[-1].get("url") if thumbs else None
    published = (photo.get("high_resolution") or {}).get("timestamp")

    url = d.get("url") or d.get("path") or f"/items/{d.get('id')}"
    if url.startswith("/"):
        url = base + url

    user = d.get("user") or {}
    return Item(
        id=int(d.get("id") or box.get("item_id")),
        title=title,
        price=_money(raw_price) or 0.0,
        total_price=_money(d.get("total_item_price")),
        currency=currency,
        brand=brand,
        size=size,
        status=str(status),
        status_id=int(status_id) if status_id else None,
        url=url,
        photo=photo_url,
        favourites=int(d.get("favourite_count") or 0),
        views=int(d.get("view_count") or 0),
        seller=user.get("login") or "",
        published_at=float(published) if published else None,
        raw=d,
    )


class VintedClient:
    def __init__(self, domain: str = "www.vinted.fr", min_delay: float = 2.0):
        host = domain.replace("https://", "").replace("http://", "").strip("/")
        bare = host[4:] if host.startswith("www.") else host
        self.base = f"https://www.{bare}"
        self.api = f"https://api.{bare}"
        tld = bare.split("vinted.", 1)[-1]
        self.locale = LOCALES.get(tld, "fr-FR")
        self.min_delay = min_delay
        self.session: requests.Session | None = None
        self.anon_id: str | None = None
        self._last_call = 0.0
        self._debug_written = False

    # ---------- identité anonyme ----------
    def _new_session(self) -> None:
        ua = random.choice(USER_AGENTS)
        s = requests.Session()
        s.headers.update({"User-Agent": ua})
        page_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": f"{self.locale},en;q=0.5",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
        }
        anon = None
        # La page fait plusieurs Mo : on ne lit que les en-têtes (stream + close)
        for path in ("/", "/catalog"):
            r = s.get(self.base + path, headers=page_headers, timeout=20, stream=True)
            anon = anon or (r.headers.get("X-Anon-Id") or "").strip() or None
            status = r.status_code
            r.close()
            if status >= 400:
                raise VintedError(f"Impossible d'ouvrir une session Vinted (HTTP {status})")
            if s.cookies.get("access_token_web") and anon:
                break
        if not s.cookies.get("access_token_web"):
            log.warning("Cookie access_token_web absent : Vinted risque de refuser les requêtes")
        if not anon:
            log.warning("En-tête X-Anon-Id absent de la page d'accueil")
        log.info("Nouvelle session Vinted (cookie=%s, anon_id=%s)",
                 bool(s.cookies.get("access_token_web")), bool(anon))
        self.session, self.anon_id = s, anon

    def _api_headers(self) -> dict:
        h = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": f"{self.locale},en;q=0.5",
            "Cache-Control": "no-cache", "Pragma": "no-cache",
            "Origin": self.base, "Referer": self.base + "/",
            "Locale": self.locale, "Platform": "web", "X-Next-App": "marketplace-web",
            "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-site",
        }
        if self.anon_id:
            h["X-Anon-Id"] = self.anon_id
        return h

    def _throttle(self) -> None:
        wait = self.min_delay + random.uniform(0, 1.5) - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def _dump(self, payload: Any, force: bool = False) -> None:
        if self._debug_written and not force:
            return
        try:
            with open(DEBUG_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            self._debug_written = True
        except OSError:
            pass

    # ---------- recherche ----------
    def search(self, text: str = "", page: int = 1, per_page: int = 96,
               price_from: float | None = None, order: str = "newest_first") -> list[Item]:
        params: dict[str, Any] = {"page": page, "per_page": per_page, "order": order}
        if text:
            params["search_text"] = text
        if price_from:
            params["price_from"] = price_from

        last_error = ""
        for attempt in range(5):
            try:
                if self.session is None:
                    self._new_session()
                self._throttle()
                r = self.session.get(self.api + "/svc-catalogue/items", params=params,
                                     headers=self._api_headers(), timeout=20)
            except (requests.RequestException, VintedError) as e:
                last_error = str(e)
                self.session = None
                time.sleep(5 * (attempt + 1))
                continue

            if r.status_code in (401, 403):
                last_error = f"HTTP {r.status_code} (session refusée) : {r.text[:120]}"
                log.warning(last_error)
                self.session = None
                time.sleep(3 + attempt * 5)
                continue
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After") or 0) or 30 * (attempt + 1)
                last_error = "HTTP 429 (trop de requêtes)"
                log.warning("Limité par Vinted, pause de %ss", wait)
                time.sleep(wait)
                continue
            if r.status_code >= 500:
                last_error = f"HTTP {r.status_code}"
                time.sleep(10)
                continue
            if r.status_code >= 400:
                self._dump({"status": r.status_code, "url": r.url, "body": r.text[:5000]}, True)
                raise VintedError(f"HTTP {r.status_code} sur {r.url.split('?')[0]} : "
                                  f"{r.text[:150]}")

            try:
                data = r.json()
            except ValueError:
                self._dump({"url": r.url, "body": r.text[:5000]}, True)
                raise VintedError("Réponse non JSON (voir debug_last_response.json)")
            self._dump(data)

            raw_items = data.get("items")
            if raw_items is None:
                raw_items = (data.get("data") or {}).get("items", []) \
                    if isinstance(data.get("data"), dict) else []
            out = []
            for d in raw_items:
                try:
                    out.append(parse_item(d, self.base))
                except Exception as e:  # une annonce mal formée ne doit pas tout casser
                    log.debug("Annonce ignorée : %s", e)
            if raw_items and not out:
                self._dump(data, True)
                raise VintedError("Format de réponse inattendu (voir debug_last_response.json)")
            return out

        raise VintedError(f"Vinted ne répond pas ({last_error})")
