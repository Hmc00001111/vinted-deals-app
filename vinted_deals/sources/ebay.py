"""eBay France — page de résultats triée par « annonces les plus récentes »."""
from __future__ import annotations

import re

from .base import (HTML_HEADERS, Item, Source, SourceError, check_response, guess_status,
                   money, strip_tags)

_BLOCK = re.compile(r"<li\b", re.I)
_ID = re.compile(r'href="https?://(?:www\.)?ebay\.[a-z.]+/itm/(?:[^"/?]+/)?(\d{9,})')
_TITLE = re.compile(r'class="[^"]*(?:s-item__title|s-card__title)[^"]*"[^>]*>(.*?)</(?:div|span|h3|a)>',
                    re.S)
_PRICE = re.compile(r'class="[^"]*(?:s-item__price|s-card__price)[^"]*"[^>]*>(.*?)</(?:span|div)>', re.S)
_IMG = re.compile(r'<img[^>]+?(?:data-defer-load|data-src|src)="(https://i\.ebayimg\.com/[^"]+)"', re.S)
_ALT = re.compile(r'<img[^>]+alt="([^"]{6,})"')
_COND = re.compile(r'class="[^"]*(?:SECONDARY_INFO|s-card__subtitle)[^"]*"[^>]*>(.*?)</(?:span|div)>',
                   re.S)


class EbaySource(Source):
    key = "ebay"
    label = "eBay"
    description = "Mode neuve et occasion, vintage"
    home = "https://www.ebay.fr"

    FASHION_CATEGORY = "11450"  # « Vêtements, accessoires » (le détail est filtré ensuite sur le titre)

    def search(self, query: str, page: int = 1, category: str | None = None) -> list[Item]:
        self.throttle()
        s = self.get_session()
        params = {"_nkw": query, "_sop": "10", "_ipg": "60", "_pgn": str(page)}
        if category:
            params["_sacat"] = self.FASHION_CATEGORY
        r = s.get(self.home + "/sch/i.html", params=params, headers=HTML_HEADERS, timeout=25)
        check_response(r, "eBay")
        return self.parse(r.text)

    def parse(self, html: str) -> list[Item]:
        out: list[Item] = []
        seen = set()
        for block in _BLOCK.split(html)[1:]:
            m = _ID.search(block)
            if not m:
                continue
            item_id = m.group(1)
            if item_id in seen or item_id.startswith("123456"):
                continue
            t = _TITLE.search(block)
            title = strip_tags(t.group(1)) if t else ""
            if not title:
                a = _ALT.search(block)
                title = strip_tags(a.group(1)) if a else ""
            title = re.sub(r"^(Nouvelle annonce|New listing)\s*", "", title, flags=re.I)
            title = re.sub(r"\s*(La page s'ouvre dans une nouvelle fenêtre ou un nouvel onglet|"
                           r"Opens in a new window or tab)\s*$", "", title, flags=re.I)
            p = _PRICE.search(block)
            price = money(strip_tags(p.group(1))) if p else None
            if not title or price is None or title.lower().startswith("shop on ebay"):
                continue
            img = _IMG.search(block)
            photo = img.group(1) if img else None
            if photo:
                photo = re.sub(r"s-l\d+", "s-l500", photo)
            c = _COND.search(block)
            cond = strip_tags(c.group(1)) if c else ""
            seen.add(item_id)
            out.append(Item(
                source=self.key, id=item_id, title=title, price=price, orig_price=price,
                url=f"{self.home}/itm/{item_id}", status=cond, status_id=guess_status(cond),
                photo=photo, photos=[photo] if photo else [],
            ))
        if not out and ("s-item" not in html and "s-card" not in html):
            raise SourceError("eBay : format de page inattendu")
        return out
