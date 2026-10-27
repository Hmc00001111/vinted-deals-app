"""Vestiaire Collective — API de recherche utilisée par le site, avec la page de recherche en secours."""
from __future__ import annotations

from .base import (HTML_HEADERS, JSON_HEADERS, Item, Source, SourceError, check_response,
                   extract_next_data, first, guess_status, money, parse_ts, to_eur, walk)

IMG = "https://images.vestiairecollective.com/images/resized/w=480,q=75,f=auto,"


def _pic(p) -> str | None:
    if isinstance(p, dict):
        p = p.get("path") or p.get("url")
    if not p:
        return None
    p = str(p)
    if p.startswith("http"):
        return p
    return IMG + p.lstrip("/")


class VestiaireSource(Source):
    key = "vestiaire"
    label = "Vestiaire Collective"
    description = "Luxe et vêtements de créateurs d'occasion"
    home = "https://fr.vestiairecollective.com"

    def search(self, query: str, page: int = 1, category: str | None = None) -> list[Item]:
        self.throttle()
        s = self.get_session()
        body = {
            "pagination": {"offset": (page - 1) * 48, "limit": 48},
            "fields": ["name", "description", "brand", "model", "country", "price", "discount",
                       "link", "sold", "likes", "editorPicks", "shouldBeGone", "seller",
                       "directShipping", "local", "pictures", "colors", "size", "stock",
                       "universeId", "condition", "createdAt"],
            "facets": {"fields": [], "stats": []},
            "sortBy": "recency", "q": query, "countryCode": "FR",
            "locale": {"country": "FR", "currency": "EUR", "language": "fr", "sizeType": "FR"},
            "mySizes": None,
        }
        headers = dict(JSON_HEADERS, Origin=self.home, Referer=self.home + "/",
                       **{"Content-Type": "application/json"})
        err: Exception | None = None
        try:
            r = s.post("https://search.vestiairecollective.com/v1/product/search",
                       json=body, headers=headers, timeout=20)
            check_response(r, "Vestiaire Collective")
            data = r.json()
            items = data.get("items") or data.get("products") or []
            parsed = [it for it in (self._parse(p) for p in items) if it]
            if parsed or items == []:
                return parsed
        except Exception as e:
            err = e
        if page > 1:
            return []
        r = s.get(self.home + "/search/", params={"q": query, "sortBy": "recency"},
                  headers=HTML_HEADERS, timeout=25)
        try:
            check_response(r, "Vestiaire Collective")
        except SourceError:
            if err:
                raise err if isinstance(err, SourceError) else SourceError(f"Vestiaire : {err}")
            raise
        data = extract_next_data(r.text) or {}
        prods = walk(data, lambda d: "id" in d and "price" in d and ("name" in d or "link" in d))
        return [it for it in (self._parse(p) for p in prods) if it]

    def _parse(self, p: dict) -> Item | None:
        pid = p.get("id")
        price_obj = p.get("price") or {}
        if isinstance(price_obj, dict):
            amount = (float(price_obj["cents"]) / 100 if price_obj.get("cents") is not None
                      else money(price_obj.get("amount") or price_obj.get("formatted")))
            currency = price_obj.get("currency") or "EUR"
        else:
            amount, currency = money(price_obj), "EUR"
        if not pid or amount is None:
            return None
        brand = first(p, "brand.name", "brand.localizedName", default="") or ""
        if isinstance(p.get("brand"), str):
            brand = p["brand"]
        pics = p.get("pictures") or []
        photos = [u for u in (_pic(x) for x in pics) if u][:4]
        link = p.get("link") or p.get("path") or ""
        url = link if str(link).startswith("http") else (self.home + "/" + str(link).lstrip("/") if link else self.home)
        cond = str(first(p, "condition.description", "condition.name", "condition", default="") or "")
        size = first(p, "size.size", "size.localizedSize", "size.label", default="")
        if isinstance(p.get("size"), str):
            size = p["size"]
        return Item(
            source=self.key, id=str(pid), title=str(p.get("name") or brand or "")[:140],
            price=to_eur(amount, currency) or amount, orig_price=amount, currency=str(currency).upper(),
            url=url, brand=str(brand), size=str(size or ""), status=cond,
            status_id=guess_status(cond), photo=photos[0] if photos else None, photos=photos,
            favourites=int(p.get("likes") or 0) if str(p.get("likes") or "0").isdigit() else 0,
            seller=str(first(p, "seller.firstname", "seller.username", default="") or ""),
            published_at=parse_ts(p.get("createdAt") or p.get("creationDate")),
            description=str(p.get("description") or "")[:600], raw=p,
        )
