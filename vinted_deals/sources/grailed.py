"""Grailed — le site utilise Algolia ; la clé publique de recherche est lue sur la page d'accueil."""
from __future__ import annotations

import re
import time
from urllib.parse import urlencode

from .base import (HTML_HEADERS, Item, Source, SourceError, check_response, extract_next_data,
                   first, guess_status, money, parse_ts, to_eur, walk)

# Valeurs connues en secours si l'extraction automatique échoue
FALLBACK_APP_ID = "MNRWEFSS2Q"
FALLBACK_KEYS = ["bc9ee1c014521ccf312525a4ef324a16", "c89dbaddf15fe70e1941a109bf7c2a3d"]
INDEX_NEWEST = "Listing_by_date_added_production"
INDEX_DEFAULT = "Listing_production"

CONDITIONS = {"is_new": 6, "is_gently_used": 2, "is_used": 3, "is_worn": 4}


class GrailedSource(Source):
    key = "grailed"
    label = "Grailed"
    description = "Streetwear, sneakers, mode de créateurs"
    home = "https://www.grailed.com"

    def __init__(self, domain_hint: str | None = None):
        super().__init__()
        self.app_id: str | None = None
        self.api_key: str | None = None
        self._keys_at = 0.0

    def _discover(self) -> None:
        if self.app_id and time.time() - self._keys_at < 6 * 3600:
            return
        s = self.get_session()
        app_id, key = None, None
        try:
            r = s.get(self.home + "/", headers=HTML_HEADERS, timeout=25)
            html = r.text
            m = re.search(r'"(?:appId|applicationId|algoliaAppId|ALGOLIA_APP_ID|algolia_app_id)"\s*:\s*"([A-Z0-9]{8,12})"', html)
            app_id = m.group(1) if m else None
            k = re.search(r'"(?:apiKey|searchApiKey|searchOnlyApiKey|algoliaApiKey|ALGOLIA_API_KEY|'
                          r'algolia_search_key|publicApiKey)"\s*:\s*"([a-f0-9]{32})"', html)
            key = k.group(1) if k else None
            if not (app_id and key):
                # Cherche dans les fichiers JavaScript de la page
                for src in re.findall(r'<script[^>]+src="([^"]+\.js)"', html)[:25]:
                    url = src if src.startswith("http") else self.home + src
                    try:
                        js = s.get(url, timeout=20).text
                    except Exception:
                        continue
                    app_id = app_id or (re.search(r'["\']([A-Z0-9]{10})["\']\s*,\s*["\'][a-f0-9]{32}["\']', js)
                                        or re.search(r'appId\s*:\s*["\']([A-Z0-9]{10})["\']', js) or [None, None])[1]
                    km = re.search(r'["\'][A-Z0-9]{10}["\']\s*,\s*["\']([a-f0-9]{32})["\']', js) \
                        or re.search(r'(?:apiKey|searchKey)\s*:\s*["\']([a-f0-9]{32})["\']', js)
                    key = key or (km.group(1) if km else None)
                    if app_id and key:
                        break
        except Exception:
            pass
        self.app_id = app_id or FALLBACK_APP_ID
        self.api_key = key
        self._keys_at = time.time()

    def _algolia(self, query: str, page: int) -> list[dict]:
        self._discover()
        s = self.get_session()
        keys = [self.api_key] if self.api_key else []
        keys += [k for k in FALLBACK_KEYS if k not in keys]
        last = ""
        for key in keys:
            for index in (INDEX_NEWEST, INDEX_DEFAULT):
                body = {"requests": [{"indexName": index, "params": urlencode({
                    "query": query, "hitsPerPage": 40, "page": max(0, page - 1),
                    "filters": "", "facetFilters": "[]"})}]}
                r = s.post(f"https://{self.app_id.lower()}-dsn.algolia.net/1/indexes/*/queries",
                           json=body, timeout=20,
                           headers={"X-Algolia-Application-Id": self.app_id,
                                    "X-Algolia-API-Key": key,
                                    "Origin": self.home, "Referer": self.home + "/",
                                    "Content-Type": "application/json"})
                if r.status_code == 200:
                    hits = (r.json().get("results") or [{}])[0].get("hits") or []
                    if key != self.api_key:
                        self.api_key = key
                    return hits
                last = f"HTTP {r.status_code}"
                if r.status_code in (400, 404):
                    continue  # index inconnu : essai suivant
                break  # clé refusée : clé suivante
        raise SourceError(f"Grailed : recherche refusée ({last})")

    def _html(self, query: str) -> list[dict]:
        s = self.get_session()
        r = s.get(self.home + "/shop", params={"query": query, "sort": "date_added"},
                  headers=HTML_HEADERS, timeout=25)
        check_response(r, "Grailed")
        data = extract_next_data(r.text) or {}
        return walk(data, lambda d: "id" in d and "price" in d and ("title" in d or "designers" in d))

    def search(self, query: str, page: int = 1, category: str | None = None) -> list[Item]:
        self.throttle()
        try:
            hits = self._algolia(query, page)
        except SourceError:
            if page > 1:
                return []
            hits = self._html(query)
        return [it for it in (self._parse(h) for h in hits) if it]

    def _parse(self, h: dict) -> Item | None:
        lid = h.get("id")
        title = h.get("title") or ""
        price_usd = money(h.get("price"))
        if not lid or price_usd is None:
            return None
        designers = h.get("designer_names") or ", ".join(
            d.get("name", "") for d in (h.get("designers") or []) if isinstance(d, dict))
        photo = first(h, "cover_photo.url", "photos.0.url", "cover_photo.image_url")
        cond = h.get("condition") or ""
        return Item(
            source=self.key, id=str(lid), title=title or designers,
            price=to_eur(price_usd, "USD"), orig_price=price_usd, currency="USD",
            url=f"{self.home}/listings/{lid}", brand=designers or "",
            size=str(h.get("size") or ""), status=str(cond).replace("is_", "").replace("_", " "),
            status_id=CONDITIONS.get(cond) or guess_status(str(cond)),
            photo=photo, photos=[p.get("url") for p in (h.get("photos") or []) if isinstance(p, dict) and p.get("url")][:4] or ([photo] if photo else []),
            favourites=int(h.get("followerno") or h.get("followers_count") or 0),
            seller=first(h, "user.username", default="") or "",
            published_at=parse_ts(h.get("created_at_i") or h.get("created_at")),
            category=str(first(h, "category_path", "category", default="") or ""),
            description=str(h.get("description") or "")[:600], raw=h,
        )
