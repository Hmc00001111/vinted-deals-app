"""Sellpy — recherche Algolia ; la configuration est lue dans les scripts du site."""
from __future__ import annotations

import re
import time
from urllib.parse import urlencode

from .base import (HTML_HEADERS, Item, Source, SourceError, check_response, first, guess_status,
                   money, parse_ts, to_eur)

# Valeurs relevées sur sellpy.fr (octobre 2026), utilisées si la lecture automatique échoue
FALLBACK = {"app": "G04G8KCJV4", "key": "c666d7511b996314df31ad5bc97e4fc4",
            "index": "prod_marketItem_fr_saleStartedAt_desc"}


class SellpySource(Source):
    key = "sellpy"
    label = "Sellpy"
    description = "Seconde main généraliste"
    home = "https://www.sellpy.fr"

    def __init__(self, domain_hint: str | None = None):
        super().__init__()
        self.cfg: dict | None = None
        self._cfg_at = 0.0

    def _config(self) -> dict:
        if self.cfg and time.time() - self._cfg_at < 12 * 3600:
            return self.cfg
        cfg = dict(FALLBACK)
        try:
            s = self.get_session()
            html = s.get(self.home + "/", headers=HTML_HEADERS, timeout=20).text
            main = re.findall(r'src="(/market/[^"]+\.js)"', html)
            if main:
                js = s.get(self.home + main[0], timeout=30).text
                chunk = re.search(r'(config\.prod\.[\w-]+\.bundle\.js)', js)
                if chunk:
                    conf = s.get(f"{self.home}/market/{chunk.group(1)}", timeout=30).text
                    m = re.search(r'fr:\{applicationId:`(\w+)`,searchKey:`(\w+)`[^}]*?'
                                  r'marketItemIndexSaleStartDescending:`([\w-]+)`', conf)
                    if m:
                        cfg = {"app": m.group(1), "key": m.group(2), "index": m.group(3)}
        except Exception:
            pass
        self.cfg, self._cfg_at = cfg, time.time()
        return cfg

    def search(self, query: str, page: int = 1, category: str | None = None) -> list[Item]:
        self.throttle()
        cfg = self._config()
        s = self.get_session()
        body = {"requests": [{"indexName": cfg["index"], "params": urlencode({
            "query": query, "hitsPerPage": 48, "page": max(0, page - 1)})}]}
        r = s.post(f"https://{cfg['app'].lower()}-dsn.algolia.net/1/indexes/*/queries", json=body, timeout=20,
                   headers={"X-Algolia-Application-Id": cfg["app"], "X-Algolia-API-Key": cfg["key"],
                            "Origin": self.home, "Referer": self.home + "/", "Content-Type": "application/json"})
        if r.status_code in (401, 403):
            self.cfg = None  # clé changée : relire la configuration au prochain passage
        check_response(r, "Sellpy")
        hits = (r.json().get("results") or [{}])[0].get("hits") or []
        return [it for it in (self._parse(h) for h in hits) if it]

    def _parse(self, h: dict) -> Item | None:
        oid = h.get("objectID") or h.get("id")
        price_raw = first(h, "price_FR.amount", "price_FR", "price_EUR.amount", "price_EUR", "price.amount", "price")
        currency = first(h, "price_FR.currency", "price_EUR.currency", "price.currency", "currency", default="EUR")
        price = money(price_raw)
        if not oid or price is None:
            return None
        meta = h.get("metadata") or {}
        brand = first(h, "metadata.brand", "brand", "brandName", default="") or ""
        if isinstance(brand, dict):
            brand = brand.get("name", "")
        title_parts = [first(meta, "type", default="") or first(h, "type", default=""), brand]
        title = first(h, "title", "name", "headline", default="") or " ".join(p for p in title_parts if p)
        imgs = h.get("images") or h.get("imageUrls") or []
        photos = [i if isinstance(i, str) else first(i, "url", "src", default="") for i in imgs][:4]
        photos = [p for p in photos if p]
        cond = str(first(h, "metadata.condition", "condition", "quality", default="") or "")
        size = first(h, "metadata.size", "size", default="") or ""
        return Item(
            source=self.key, id=str(oid), title=str(title)[:140],
            price=to_eur(price, str(currency)) or price, orig_price=price, currency=str(currency).upper(),
            url=f"{self.home}/item/{oid}", brand=str(brand), size=str(size),
            status=cond, status_id=guess_status(cond), photo=photos[0] if photos else None, photos=photos,
            published_at=parse_ts(first(h, "saleStartedAt", "createdAt", "publishedAt")),
            category=str(first(h, "metadata.type", "type", "category", default="") or ""),
            description=str(first(h, "metadata.description", "description", default="") or "")[:600], raw=h,
        )
