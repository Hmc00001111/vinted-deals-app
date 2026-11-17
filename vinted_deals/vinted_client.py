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
               price_from: float | None = None, order: str = "newest_first",
               price_to: float | None = None) -> list[Item]:
        params: dict[str, Any] = {"page": page, "per_page": per_page, "order": order}
        if text:
            params["search_text"] = text
        if price_from:
            params["price_from"] = price_from
        if price_to:
            params["price_to"] = price_to

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

    # ---------- état d'une annonce (vendue ? toujours en ligne ?) ----------
    # Routes JSON candidates pour le détail d'une annonce. La bonne est découverte une fois
    # (avec une annonce qu'on sait en ligne) puis mémorisée ; à défaut on lit la page HTML.
    ITEM_ROUTES = [
        "{api}/svc-catalogue/items/{id}",
        "{base}/api/v2/items/{id}/details",
        "{base}/api/v2/items/{id}",
        "{api}/svc-items/items/{id}",
    ]
    HTML_LIMIT = 700_000  # on n'a pas besoin de toute la page (économie de données sur téléphone)

    item_route: str | None = None   # route JSON validée, ou "html"

    def _get(self, url: str, **kw):
        if self.session is None:
            self._new_session()
        self._throttle()
        return self.session.get(url, timeout=20, **kw)

    def discover_item_route(self, known_active_id: int | str) -> str:
        """Trouve une route de détail qui marche, en testant sur une annonce en ligne."""
        for tpl in self.ITEM_ROUTES:
            url = tpl.format(api=self.api, base=self.base, id=known_active_id)
            try:
                r = self._get(url, headers=self._api_headers())
            except requests.RequestException:
                continue
            if r.status_code == 200 and str(known_active_id) in r.text[:200_000]:
                try:
                    r.json()
                except ValueError:
                    continue
                self.item_route = tpl
                log.info("Détail d'annonce Vinted : %s", tpl)
                return tpl
            if r.status_code in (401, 403, 429):
                continue
        # pas de route JSON : on suit l'annonce par sa présence dans la recherche (léger, marche partout),
        # avec confirmation par la page de l'annonce quand curl_cffi est installé (PC)
        self.item_route = "search"
        log.info("Suivi des annonces Vinted : présence dans la recherche%s",
                 " + page de l'annonce" if _has_cffi() else "")
        return "search"

    def item_state(self, item_id: int | str, title: str = "", price: float | None = None) -> dict | None:
        """{'state': 'active'|'reserved'|'sold'|'removed'|'missing', 'price': float|None, 'favourites': int|None}
        ou None si on ne sait pas dire (page illisible). Lève VintedError en cas de blocage.
        'missing' : l'annonce n'apparaît plus dans la recherche (vendue ou retirée, à confirmer)."""
        route = self.item_route or "search"
        if route == "search":
            st = self._search_state(item_id, title, price)
            if st and st["state"] == "missing" and _has_cffi():
                try:  # confirmation définitive (vendue / supprimée / toujours là) par la page
                    page = self._html_state(item_id)
                    if page and page["state"] != "unknown":
                        return page
                except (VintedError, requests.RequestException, Exception):
                    pass
            return st
        if route != "html":
            url = route.format(api=self.api, base=self.base, id=item_id)
            r = self._get(url, headers=self._api_headers())
            if r.status_code in (401, 403, 429):
                self.session = None
                raise VintedError(f"HTTP {r.status_code} (détail annonce)")
            if r.status_code in (404, 410):
                return {"state": "removed", "price": None, "favourites": None}
            if r.status_code == 200:
                return parse_item_state(r.text)
            return None
        return self._html_state(item_id)

    def _search_state(self, item_id: int | str, title: str, price: float | None) -> dict | None:
        """Cherche l'annonce par son titre (et son prix) : présente = en vente, absente = disparue."""
        if not title:
            return None
        words = title.split()
        pf = {"price_from": max(0.5, round(price - 0.5, 2)), "price_to": round(price + 0.5, 2)} if price else {}
        attempts = [(" ".join(words[:8]), pf, "relevance"), (" ".join(words[:8]), {}, "relevance"),
                    (" ".join(words[:3]), pf, "newest_first")]
        answered = 0
        for text, kw, order in attempts:
            items = self.search(text, page=1, per_page=96, order=order, **kw)
            for it in items:
                if str(it.id) == str(item_id):
                    return {"state": "active", "price": it.price, "favourites": it.favourites}
            if items:
                answered += 1
        # introuvable alors que la recherche répond : vendue ou retirée (à confirmer par un 2e passage)
        return {"state": "missing", "price": None, "favourites": None} if answered >= 2 else None

    def _html_state(self, item_id: int | str) -> dict | None:
        if _has_cffi():  # empreinte de vrai navigateur : la page passe la protection anti-robot
            from sources.base import HTML_HEADERS, make_session
            if getattr(self, "_page_session", None) is None:
                self._page_session = make_session(impersonate=True)
            self._throttle()
            r = self._page_session.get(f"{self.base}/items/{item_id}", headers=HTML_HEADERS, timeout=30)
            if r.status_code in (404, 410) or (r.status_code == 200 and f"/items/{item_id}" not in str(r.url)):
                return {"state": "removed", "price": None, "favourites": None}
            if r.status_code != 200:
                self._page_session = None
                raise VintedError(f"HTTP {r.status_code} (page annonce)")
            return parse_item_state(r.text)
        # page HTML de l'annonce (lecture partielle)
        page_headers = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": f"{self.locale},en;q=0.5"}
        r = self._get(f"{self.base}/items/{item_id}", headers=page_headers, stream=True, allow_redirects=True)
        try:
            if r.status_code in (403, 429):
                self.session = None
                if self.item_route == "html":
                    self.item_route = None  # la page est bloquée : on redécouvre une méthode
                raise VintedError(f"HTTP {r.status_code} (page annonce)")
            if r.status_code in (404, 410):
                return {"state": "removed", "price": None, "favourites": None}
            if f"/items/{item_id}" not in r.url:  # redirigé vers le catalogue : annonce supprimée
                return {"state": "removed", "price": None, "favourites": None}
            chunks, size = [], 0
            for chunk in r.iter_content(65536, decode_unicode=False):
                chunks.append(chunk)
                size += len(chunk)
                text = b"".join(chunks).decode("utf-8", "ignore")
                st = parse_item_state(text, partial=True)
                if st and st["state"] != "unknown" and st.get("price") is not None:
                    return st
                if size > self.HTML_LIMIT:
                    break
            st = parse_item_state(b"".join(chunks).decode("utf-8", "ignore"))
            return st if st and st["state"] != "unknown" else None
        finally:
            r.close()


import re as _re


def _has_cffi() -> bool:
    try:
        import curl_cffi  # noqa: F401
        return True
    except Exception:
        return False

_B = r'\\?"'  # guillemet éventuellement échappé (données Next.js / RSC)


def _flag(text: str, key: str) -> bool | None:
    m = _re.search(_B + key + _B + r"\s*:\s*(true|false)", text)
    return None if not m else m.group(1) == "true"


def parse_item_state(text: str, partial: bool = False) -> dict | None:
    """Repère l'état d'une annonce dans un JSON d'API ou une page HTML Vinted."""
    if not text:
        return None
    t = text
    state = "unknown"
    closing = _re.search(_B + r"item_closing_action" + _B + r"\s*:\s*" + _B + r"(\w+)", t)
    is_closed, is_reserved = _flag(t, "is_closed"), _flag(t, "is_reserved")
    is_hidden = _flag(t, "is_hidden")
    avail = _re.search(r"schema\.org/(InStock|SoldOut|OutOfStock|Discontinued|LimitedAvailability)", t)
    if (closing and closing.group(1).lower() == "sold") or (avail and avail.group(1) == "SoldOut"):
        state = "sold"
    elif is_closed:
        state = "sold" if not is_hidden else "removed"
    elif avail and avail.group(1) in ("OutOfStock", "Discontinued"):
        state = "sold"
    elif is_reserved:
        state = "reserved"
    elif is_closed is False or (avail and avail.group(1) in ("InStock", "LimitedAvailability")):
        state = "active"
    price = None
    m = _re.search(_B + r"price" + _B + r"\s*:\s*\{[^{}]*?" + _B + r"amount" + _B + r"\s*:\s*" + _B +
                   r"?([0-9]+(?:\.[0-9]+)?)", t) or \
        _re.search(r'"price"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)"?\s*,\s*"priceCurrency"', t)
    if m:
        price = float(m.group(1))
    fav = _re.search(_B + r"favourite_count" + _B + r"\s*:\s*(\d+)", t)
    if state == "unknown" and partial:
        return {"state": "unknown", "price": price, "favourites": int(fav.group(1)) if fav else None}
    return {"state": state, "price": price, "favourites": int(fav.group(1)) if fav else None}
