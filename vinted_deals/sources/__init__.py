"""Registre des sites surveillables."""
from __future__ import annotations

from .base import Blocked, Item, Source, SourceError, STATUS_LABELS
from .depop import DepopSource
from .ebay import EbaySource
from .generic import JaiioSource, StockXSource
from .sellpy import SellpySource
from .grailed import GrailedSource
from .leboncoin import LeboncoinSource
from .vestiaire import VestiaireSource
from .vinted import VintedSource

# Ordre d'affichage dans l'interface
SOURCE_CLASSES: list[type[Source]] = [
    VintedSource, VestiaireSource, DepopSource, GrailedSource, EbaySource,
    StockXSource, SellpySource, JaiioSource, LeboncoinSource,
]
SOURCES: dict[str, type[Source]] = {c.key: c for c in SOURCE_CLASSES}


def source_meta() -> list[dict]:
    return [{"key": c.key, "label": c.label, "description": c.description, "home": c.home}
            for c in SOURCE_CLASSES]


__all__ = ["Blocked", "Item", "Source", "SourceError", "STATUS_LABELS", "SOURCES",
           "SOURCE_CLASSES", "source_meta"]
