"""Connecteur générique pour les sites sans API connue.

Stratégies essayées dans l'ordre, la première qui marche est mémorisée :
  1. API Shopify (/search/suggest.json)            si le site tourne sous Shopify
  2. données Next.js (__NEXT_DATA__) de la page de recherche
  3. données structurées JSON-LD (Product / ItemList) de la page de recherche
"""
from __future__ import annotations

import re

from .base import (HTML_HEADERS, JSON_HEADERS, Item, Source, SourceError, check_response,
                   extract_json_ld, extract_next_data, first, guess_status, money, parse_ts,
                   to_eur, walk)


def _looks_like_product(d: dict) -> bool:
    keys = set(d)
    has_id = bool(keys & {"id", "objectID", "productId", "itemId", "uuid", "sku", "urlKey", "handle"})
    has_price = bool(keys & {"price", "prices", "salePrice", "currentPrice", "priceAmount", "market", "amount"})
    has_name = bool(keys & {"title", "name", "productName", "headline"})
    return has_id and has_price and has_name


class GenericSource(Source):
    search_paths: list[tuple[str, str]] = []   # (chemin, nom du paramètre de recherche)
    url_template = "{home}/{path}"            # pour reconstruire un lien depuis un slug

    def __init__(self, domain_hint: str | None = None):
        super().__init__()
        self.strategy: str | None = None

    # --- stratégies -----------------------------------------------------
    def _shopify(self, query: str) -> list[Item]:
        s = self.get_session()
        r = s.get(self.home + "/search/suggest.json",
                  params={"q": query, "resources[type]": "product", "resources[limit]": "10",
                          "resources[options][unavailable_products]": "hide"},
                  headers=JSON_HEADERS, timeout=20)
        check_response(r, self.label)
        prods = first(r.json(), "resources.results.products", default=[]) or []
        out = []
        for p in prods:
            price = money(p.get("price"))
            if price is None:
                continue
            url = p.get("url") or ""
            out.append(Item(source=self.key, id=str(p.get("id") or p.get("handle")),
                            title=p.get("title") or "", price=price, orig_price=price,
                            url=url if url.startswith("http") else self.home + url.split("?")[0],
                            brand=p.get("vendor") or "", photo=p.get("image"),
                            photos=[p["image"]] if p.get("image") else [],
                            description=re.sub("<[^>]+>", " ", p.get("body") or "")[:600], raw=p))
        return out

    def _page(self, query: str) -> str:
        s = self.get_session()
        last: Exception | None = None
        for path, param in self.search_paths:
            try:
                r = s.get(self.home + path, params={param: query}, headers=HTML_HEADERS, timeout=25)
                check_response(r, self.label)
                return r.text
            except Exception as e:
                last = e
        raise last if isinstance(last, SourceError) else SourceError(f"{self.label} : {last}")

    def _from_next(self, html: str) -> list[Item]:
        data = extract_next_data(html)
        if not data:
            # certains sites mettent l'état initial dans un autre script JSON
            m = re.search(r'window\.__(?:INITIAL_STATE|APOLLO_STATE|PRELOADED_STATE)__\s*=\s*(\{.*?\});\s*</script>',
                          html, re.S)
            if m:
                import json
                try:
                    data = json.loads(m.group(1))
                except ValueError:
                    data = None
        if not data:
            return []
        return [it for it in (self.parse_product(p) for p in walk(data, _looks_like_product)) if it]

    def _from_jsonld(self, html: str) -> list[Item]:
        out = []
        for block in extract_json_ld(html):
            nodes = walk(block, lambda d: d.get("@type") in ("Product", "IndividualProduct"))
            for n in nodes:
                offer = n.get("offers") or {}
                if isinstance(offer, list):
                    offer = offer[0] if offer else {}
                price = money(offer.get("price") or offer.get("lowPrice"))
                url = n.get("url") or offer.get("url") or ""
                if price is None or not url:
                    continue
                img = n.get("image")
                img = img[0] if isinstance(img, list) and img else img
                if isinstance(img, dict):
                    img = img.get("url")
                cur = offer.get("priceCurrency") or "EUR"
                out.append(Item(source=self.key, id=str(n.get("sku") or n.get("productID") or url),
                                title=n.get("name") or "", price=to_eur(price, cur) or price,
                                orig_price=price, currency=cur, url=url,
                                brand=str(first(n, "brand.name", "brand", default="") or ""),
                                status=str(first(offer, "itemCondition", default="")).split("/")[-1],
                                status_id=guess_status(str(first(offer, "itemCondition", default=""))),
                                photo=img, photos=[img] if img else [], raw=n))
        return out

    # --- produit trouvé dans un JSON quelconque ----------------------------
    def parse_product(self, p: dict) -> Item | None:
        pid = first(p, "id", "objectID", "productId", "itemId", "uuid", "sku", "urlKey", "handle")
        title = first(p, "title", "name", "productName", "headline", default="")
        price_raw = first(p, "price", "salePrice", "currentPrice", "priceAmount", "prices.0",
                          "market.state.lowestAsk.amount", "market.bidAskData.lowestAsk", "amount")
        currency = first(p, "currency", "currencyCode", "price.currency", "price.currencyCode",
                         "market.currencyCode", default="EUR")
        price = money(price_raw)
        if isinstance(price_raw, dict) and price_raw.get("cents") is not None:
            price = float(price_raw["cents"]) / 100
        if not pid or not title or price is None or price <= 0:
            return None
        url = first(p, "url", "link", "href", "path", "permalink", default="")
        if not url:
            slug = first(p, "slug", "urlKey", "handle", default="")
            url = self.url_template.format(home=self.home, path=slug) if slug else self.home
        elif not str(url).startswith("http"):
            url = self.home + "/" + str(url).lstrip("/")
        img = first(p, "image", "imageUrl", "thumbnail", "thumbUrl", "media.thumbUrl", "media.imageUrl",
                    "images.0", "pictures.0", "photos.0", "mainImage", "primaryImage")
        if isinstance(img, dict):
            img = first(img, "url", "src", "path", "large", "medium")
        brand = first(p, "brand", "brand.name", "brandName", "vendor", "designer", default="")
        if isinstance(brand, dict):
            brand = brand.get("name", "")
        cond = str(first(p, "condition", "condition.name", "quality", "grade", default="") or "")
        return Item(source=self.key, id=str(pid), title=str(title)[:140],
                    price=to_eur(price, str(currency)) or price, orig_price=price,
                    currency=str(currency).upper() if isinstance(currency, str) else "EUR",
                    url=str(url), brand=str(brand or ""), size=str(first(p, "size", "size.name", default="") or ""),
                    status=cond, status_id=guess_status(cond),
                    photo=str(img) if img else None, photos=[str(img)] if img else [],
                    published_at=parse_ts(first(p, "createdAt", "created_at", "publishedAt", "listedAt")),
                    raw=p)

    # --- point d'entrée -------------------------------------------------
    def search(self, query: str, page: int = 1, category: str | None = None) -> list[Item]:
        if page > 1:
            return []  # pagination non gérée par le connecteur générique
        self.throttle()
        order = ["shopify", "next", "jsonld"]
        if self.strategy:
            order.remove(self.strategy)
            order.insert(0, self.strategy)
        html: str | None = None
        errors = []
        for strat in order:
            try:
                if strat == "shopify":
                    items = self._shopify(query)
                else:
                    if html is None:
                        html = self._page(query)
                    items = self._from_next(html) if strat == "next" else self._from_jsonld(html)
            except SourceError as e:
                errors.append(e)
                continue
            except Exception as e:  # format inattendu
                errors.append(SourceError(f"{self.label} : {e}"))
                continue
            if items:
                self.strategy = strat
                return items
        blocking = [e for e in errors if "bloque" in str(e) or "anti-robot" in str(e)]
        if blocking:
            raise blocking[0]
        if html is not None:
            return []  # page lue mais aucune annonce reconnue
        raise errors[-1] if errors else SourceError(f"{self.label} : aucune annonce lisible")


class JaiioSource(GenericSource):
    """Boutique Shopify : on lit les derniers produits mis en ligne et on filtre sur la recherche."""
    key = "jaiio"
    label = "Jaiio"
    description = "Mode de seconde main, marques premium"
    home = "https://www.jaiio.fr"
    search_paths = [("/search", "q"), ("/recherche", "q"), ("/", "s")]
    url_template = "{home}/products/{path}"

    def search(self, query: str, page: int = 1, category: str | None = None) -> list[Item]:
        if page > 2:
            return []
        self.throttle()
        s = self.get_session()
        words = [w for w in query.lower().split() if w]
        try:
            r = s.get(self.home + "/collections/all/products.json",
                      params={"sort_by": "created-descending", "limit": "250", "page": str(page)},
                      headers=JSON_HEADERS, timeout=25)
            check_response(r, self.label)
            products = r.json().get("products") or []
        except Exception:
            return super().search(query, page, category)
        out = []
        for p in products:
            text = f"{p.get('title', '')} {p.get('vendor', '')} {p.get('product_type', '')} {' '.join(p.get('tags') or [])}".lower()
            if words and not all(w in text for w in words):
                continue
            variants = p.get("variants") or [{}]
            avail = [v for v in variants if v.get("available", True)]
            if not avail:
                continue  # déjà vendu
            price = money(avail[0].get("price"))
            if price is None:
                continue
            imgs = [i.get("src") for i in (p.get("images") or []) if i.get("src")][:4]
            body = re.sub("<[^>]+>", " ", p.get("body_html") or "")
            size = " / ".join(str(v.get("title")) for v in avail[:3] if v.get("title") not in (None, "Default Title"))
            out.append(Item(source=self.key, id=str(p.get("id")), title=p.get("title") or "",
                            price=price, orig_price=price, url=f"{self.home}/products/{p.get('handle')}",
                            brand=p.get("vendor") or "", size=size, status=guess_status_label(body),
                            status_id=guess_status(body), photo=imgs[0] if imgs else None, photos=imgs,
                            published_at=parse_ts(p.get("published_at") or p.get("created_at")),
                            category=str(p.get("product_type") or ""), description=" ".join(body.split())[:600],
                            raw={k: p.get(k) for k in ("id", "title", "vendor", "product_type", "tags", "handle")}))
        return out


def guess_status_label(text: str) -> str:
    sid = guess_status(text)
    from .base import STATUS_LABELS
    return STATUS_LABELS.get(sid, "") if sid else ""


class StockXSource(GenericSource):
    key = "stockx"
    label = "StockX"
    description = "Sneakers et vêtements de marques recherchées"
    home = "https://stockx.com"
    search_paths = [("/fr-fr/search", "s"), ("/search", "s")]
    url_template = "{home}/fr-fr/{path}"
    min_delay = 5.0

    def search(self, query: str, page: int = 1, category: str | None = None) -> list[Item]:
        self.strategy = self.strategy or "next"
        return super().search(query, page, category)
