"""Leboncoin — API de l'application mobile (protégée par Datadome : souvent bloquée)."""
from __future__ import annotations

import random
import uuid

from .base import Blocked, Item, Source, check_response, guess_status, has_cffi, parse_ts

MODE = "72"  # catégorie « Mode »
CATEGORY_IDS = {"vetements": "22", "chaussures": "53", "sacs": "47",
                "accessoires": "47", "montres_bijoux": "42"}

CONDITION_MAP = {"etatneuf": 1, "neufetiquette": 6, "tresbonetat": 2, "bonetat": 3,
                 "etatsatisfaisant": 4, "pourpieces": 4}


def _app_ua() -> str:
    model = random.choice(["SM-S911B", "SM-G991B", "Pixel 7", "Pixel 8", "Redmi Note 12"])
    return (f"LBC;Android;{random.choice(['13', '14', '15'])};{model};phone;"
            f"{uuid.uuid4().hex[:16]};wifi;{random.choice(['100.85.2', '100.84.1', '100.83.1'])}")


class LeboncoinSource(Source):
    key = "leboncoin"
    label = "Leboncoin"
    description = "Vêtements d'occasion et vente locale"
    home = "https://www.leboncoin.fr"
    min_delay = 4.0
    first_scan_pages = 1

    def get_session(self):
        if self.session is None:
            if has_cffi():
                from curl_cffi import requests as creq
                s = creq.Session(impersonate=random.choice(["chrome131_android", "chrome_android",
                                                            "safari172_ios", "safari17_2_ios"]))
            else:
                import requests
                s = requests.Session()
            s.headers.update({"User-Agent": _app_ua(), "Accept": "application/json",
                              "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
                              "Sec-Fetch-Site": "same-site"})
            try:
                s.get(self.home + "/", timeout=20)  # cookies
            except Exception:
                pass
            self.session = s
        return self.session

    def search(self, query: str, page: int = 1, category: str | None = None) -> list[Item]:
        self.throttle()
        s = self.get_session()
        payload = {
            "filters": {
                "category": {"id": CATEGORY_IDS.get(category or "", MODE)},
                "enums": {"ad_type": ["offer"]},
                "keywords": {"text": query},
                "location": {},
            },
            "limit": 35, "limit_alu": 0, "offset": 35 * (page - 1),
            "disable_total": True, "extend": True,
            "listing_source": "direct-search" if page == 1 else "pagination",
            "sort_by": "time", "sort_order": "desc",
        }
        r = s.post("https://api.leboncoin.fr/finder/search", json=payload, timeout=25)
        if r.status_code == 403:
            self.session = None
            raise Blocked("Leboncoin bloque les requêtes automatiques (protection Datadome)"
                          + ("" if has_cffi() else " — installer curl_cffi peut aider sur PC"))
        check_response(r, "Leboncoin")
        ads = r.json().get("ads") or []
        return [it for it in (self._parse(a) for a in ads) if it]

    def _parse(self, a: dict) -> Item | None:
        lid = a.get("list_id")
        price = (a.get("price_cents") / 100) if a.get("price_cents") else \
            (float(a["price"][0]) if isinstance(a.get("price"), list) and a["price"] else None)
        if not lid or price is None:
            return None
        attrs = {x.get("key"): x for x in (a.get("attributes") or []) if isinstance(x, dict)}
        cond_attr = attrs.get("condition") or attrs.get("item_condition") or {}
        cond_key = str(cond_attr.get("value") or "")
        images = a.get("images") or {}
        photos = (images.get("urls_large") or images.get("urls") or [])[:4]
        brand = a.get("brand") or (attrs.get("clothing_brand_a") or attrs.get("brand") or {}).get("value_label", "")
        size = (attrs.get("clothing_st") or attrs.get("shoe_size") or attrs.get("size") or {}).get("value_label", "")
        return Item(
            source=self.key, id=str(lid), title=a.get("subject") or "", price=price, orig_price=price,
            url=a.get("url") or f"{self.home}/ad/{lid}", brand=str(brand or ""), size=str(size or ""),
            status=str(cond_attr.get("value_label") or ""),
            status_id=CONDITION_MAP.get(cond_key) or guess_status(str(cond_attr.get("value_label") or "")),
            photo=photos[0] if photos else images.get("thumb_url"), photos=photos,
            favourites=int((a.get("counters") or {}).get("favorites") or 0),
            seller=str((a.get("owner") or {}).get("name") or ""),
            published_at=parse_ts(a.get("first_publication_date")),
            category=str(a.get("category_name") or ""), description=str(a.get("body") or "")[:600], raw=a,
        )
