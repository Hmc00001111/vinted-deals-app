"""Depop — API web publique utilisée par le site (webapi.depop.com)."""
from __future__ import annotations

from .base import (HTML_HEADERS, JSON_HEADERS, Item, Source, SourceError, check_response,
                   extract_next_data, first, guess_status, money, parse_ts, to_eur, walk)


def _pic(p) -> str | None:
    if isinstance(p, str):
        return p
    if isinstance(p, dict):
        for k in ("640", "960", "480", "1280", "url", "320", "210", "150"):
            if p.get(k):
                return p[k] if isinstance(p[k], str) else first(p[k], "url")
        formats = p.get("formats") or {}
        for k in ("P0", "P1", "P2", "P4"):
            if isinstance(formats.get(k), dict) and formats[k].get("url"):
                return formats[k]["url"]
    return None


class DepopSource(Source):
    key = "depop"
    label = "Depop"
    description = "Vintage, streetwear, mode tendance"
    home = "https://www.depop.com"

    def __init__(self, domain_hint: str | None = None):
        super().__init__()
        self.cursors: dict[str, str] = {}

    def search(self, query: str, page: int = 1, category: str | None = None) -> list[Item]:
        self.throttle()
        s = self.get_session()
        params = {"what": query, "items_per_page": "48", "country": "fr", "currency": "EUR",
                  "sort": "newlyListed", "force_fee_calculation": "false"}
        if page > 1:
            cur = self.cursors.get(query)
            if not cur:
                return []
            params["cursor"] = cur
        headers = dict(JSON_HEADERS, Origin=self.home, Referer=self.home + "/search/")
        if not getattr(self, "_warmed", False):
            # L'API exige les cookies d'une vraie visite du site (v3 est fermée depuis 2026)
            try:
                s.get(self.home + "/", headers=HTML_HEADERS, timeout=20)
            except Exception:
                pass
            self._warmed = True
        last = None
        for path in ("/api/v2/search/products/",):
            try:
                r = s.get("https://webapi.depop.com" + path, params=params, headers=headers, timeout=20)
            except Exception as e:
                last = SourceError(f"Depop : {e}")
                continue
            if r.status_code == 404:
                continue
            try:
                check_response(r, "Depop")
                data = r.json()
            except Exception as e:
                last = e
                continue
            self.cursors[query] = first(data, "meta.cursor", "cursor", default="") or ""
            products = data.get("products") or data.get("objects") or []
            return [it for it in (self._parse(p) for p in products) if it]
        if page > 1:
            return []
        # Secours : page de recherche du site
        r = s.get(self.home + "/search/", params={"q": query, "sort": "newlyListed"},
                  headers=HTML_HEADERS, timeout=25)
        try:
            check_response(r, "Depop")
        except SourceError:
            if last:
                raise last
            raise
        data = extract_next_data(r.text) or {}
        prods = walk(data, lambda d: "slug" in d and ("price" in d or "pricing" in d))
        if not prods and last:
            raise last if isinstance(last, SourceError) else SourceError(f"Depop : {last}")
        return [it for it in (self._parse(p) for p in prods) if it]

    def _parse(self, p: dict) -> Item | None:
        slug = p.get("slug")
        pid = p.get("id") or slug
        price_obj = p.get("price") or p.get("pricing") or {}
        amount = money(first(price_obj, "discountedPriceAmount", "priceAmount", "original_price.price_breakdown.price.amount", "amount")) \
            if isinstance(price_obj, dict) else money(price_obj)
        currency = (first(price_obj, "currencyName", "currency_name", "currency", default="EUR")
                    if isinstance(price_obj, dict) else "EUR")
        if amount is None:
            amount = money(first(p, "pricing.original_price.price_breakdown.price.amount",
                                 "pricing.discounted_price.price_breakdown.price.amount"))
            currency = first(p, "pricing.currency_name", default=currency)
        if not pid or amount is None:
            return None
        pics = p.get("pictures") or p.get("preview") or p.get("pictures_data") or []
        if isinstance(pics, dict):
            pics = [pics]
        photos = [u for u in (_pic(x) for x in pics) if u][:4]
        title = p.get("description") or p.get("title") or slug or ""
        title = str(title).split("\n")[0][:140]
        brand = p.get("brandName") or p.get("brand_name") or first(p, "brand.name", default="") or ""
        size = ", ".join(str(x.get("name", x)) if isinstance(x, dict) else str(x)
                         for x in (p.get("sizes") or [])[:3])
        cond = str(first(p, "condition", "condition.name", default="") or "")
        return Item(
            source=self.key, id=str(pid), title=title, price=to_eur(amount, currency) or amount,
            orig_price=amount, currency=str(currency or "EUR").upper(),
            url=f"{self.home}/products/{slug}/" if slug else self.home,
            brand=str(brand), size=size, status=cond.replace("_", " ").lower(),
            status_id=guess_status(cond), photo=photos[0] if photos else None, photos=photos,
            favourites=int(p.get("like_count") or 0),
            seller=str(first(p, "seller.username", "sellerUsername", default="") or ""),
            published_at=parse_ts(p.get("dateCreated") or p.get("date_created") or p.get("created")),
            description=str(p.get("description") or "")[:600], raw=p,
        )
