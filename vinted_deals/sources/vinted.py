"""Vinted — réutilise le client éprouvé de vinted_client.py."""
from __future__ import annotations

from vinted_client import VintedClient, VintedError

from .base import Blocked, Item, Source, SourceError


class VintedSource(Source):
    key = "vinted"
    label = "Vinted"
    description = "Vêtements d'occasion, toutes marques"
    home = "https://www.vinted.fr"
    first_scan_pages = 3

    def __init__(self, domain_hint: str | None = None):
        super().__init__()
        self.client = VintedClient(domain_hint or "www.vinted.fr")

    def throttle(self) -> None:  # le client gère déjà son propre rythme
        pass

    def reset(self) -> None:
        self.client.session = None

    def search(self, query: str, page: int = 1, category: str | None = None) -> list[Item]:
        try:
            raw = self.client.search(query, page=page)
        except VintedError as e:
            msg = str(e)
            if "403" in msg or "401" in msg or "429" in msg:
                raise Blocked(f"Vinted : {msg}") from e
            raise SourceError(f"Vinted : {msg}") from e
        out = []
        for it in raw:
            photos = []
            for p in (it.raw.get("photos") or [it.raw.get("photo") or {}]):
                if isinstance(p, dict) and (p.get("url") or p.get("full_size_url")):
                    photos.append(p.get("url") or p.get("full_size_url"))
            out.append(Item(
                source=self.key, id=str(it.id), title=it.title, price=it.price,
                orig_price=it.price, currency=it.currency, total_price=it.total_price,
                url=it.url, brand=it.brand, size=it.size, status=it.status,
                status_id=it.status_id, photo=it.photo, photos=photos or ([it.photo] if it.photo else []),
                favourites=it.favourites, views=it.views, seller=it.seller,
                published_at=it.published_at, raw=it.raw,
            ))
        return out
