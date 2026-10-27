"""Détection des bonnes affaires.

Principe :
  1. Prix de référence = prix saisi à la main pour la recherche, sinon médiane
     (sans les 10 % extrêmes) des annonces observées ces 30 derniers jours.
  2. Décote = 1 - prix / référence.
  3. Score sur 100 = base + décote + mots « rares » + état + favoris.
"""
from __future__ import annotations

import statistics
import unicodedata
from dataclasses import dataclass, field

from vinted_client import Item

DEFAULT_RARE_KEYWORDS = [
    "vintage", "rare", "edition limitee", "limited edition", "deadstock", "collector",
    "made in italy", "made in japan", "made in usa", "made in england", "archive",
    "sample", "prototype", "signe", "numerote", "90s", "y2k", "og", "1 of",
]

DEFAULT_EXCLUDE_KEYWORDS = [
    "cherche", "recherche", "echange uniquement", "boite vide", "boite seule",
    "box only", "replica", "reproduction", "copie", "inspired", "style",
]

CONDITION_POINTS = {6: 10, 1: 7, 2: 4, 3: 0, 4: -8}

MIN_SAMPLES = 8


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return " ".join(text.lower().split())


def parse_list(value: str | None) -> list[str]:
    return [norm(x) for x in (value or "").split(",") if x.strip()]


def contains_word(haystack: str, needle: str) -> bool:
    padded = f" {haystack} "
    for sep in (" ", "-", "/", "(", ")", ",", "."):
        padded = padded.replace(sep, " ")
    return f" {needle} " in f" {padded} " or (" " in needle and needle in haystack)


def reference_price(samples: list[float]) -> float | None:
    samples = [p for p in samples if p and p > 0]
    if len(samples) < MIN_SAMPLES:
        return None
    s = sorted(samples)
    k = int(len(s) * 0.1)
    core = s[k:len(s) - k] or s
    return float(statistics.median(core))


def brand_ok(item: Item, watch: dict) -> bool:
    brands = parse_list(watch.get("brands"))
    if not brands:
        return True
    b = norm(item.brand)
    return any(b == x or x in b for x in brands)


def excluded_by(item: Item, watch: dict) -> str | None:
    words = parse_list(watch.get("exclude_keywords")) or DEFAULT_EXCLUDE_KEYWORDS
    title = norm(item.title)
    for w in words:
        if contains_word(title, w):
            return w
    return None


def matches_filters(item: Item, watch: dict) -> bool:
    """Filtres utilisateur (prix, état, taille). La marque est vérifiée à part."""
    if watch.get("price_min") is not None and item.price < float(watch["price_min"]):
        return False
    if watch.get("price_max") is not None and item.price > float(watch["price_max"]):
        return False
    status_ids = [int(x) for x in str(watch.get("status_ids") or "").split(",") if x.strip()]
    if status_ids and item.status_id not in status_ids:
        return False
    sizes = parse_list(watch.get("sizes"))
    if sizes and norm(item.size) not in sizes:
        return False
    return True


@dataclass
class Verdict:
    score: float
    is_deal: bool
    discount: float | None
    ref_price: float | None
    reasons: list[str] = field(default_factory=list)


def evaluate(item: Item, watch: dict, samples: list[float]) -> Verdict:
    reasons: list[str] = []
    score = 20.0

    manual_ref = watch.get("ref_price")
    ref = float(manual_ref) if manual_ref else reference_price(samples)
    discount = None
    if ref:
        discount = 1 - item.price / ref
        score += max(-40.0, min(60.0, discount * 100))
        origin = "votre prix de référence" if manual_ref else f"médiane de {len(samples)} annonces"
        if discount > 0:
            reasons.append(f"{discount:.0%} sous le marché ({ref:.0f} €, {origin})")
        else:
            reasons.append(f"Au-dessus du marché ({ref:.0f} €, {origin})")
        if discount > 0.8:
            score -= 10
            reasons.append("Prix anormalement bas : vérifier (arnaque, accessoire, lot ?)")
    else:
        score += 15
        reasons.append(f"Référence en construction ({len(samples)}/{MIN_SAMPLES} annonces)")

    rare_words = parse_list(watch.get("rare_keywords")) or DEFAULT_RARE_KEYWORDS
    title = norm(item.title)
    hits = [w for w in rare_words if contains_word(title, w)]
    if hits:
        score += min(24, 8 * len(hits))
        reasons.append("Mots rares : " + ", ".join(hits[:4]))

    cond = CONDITION_POINTS.get(item.status_id or -1, 0)
    score += cond
    if cond > 0:
        reasons.append(f"État : {item.status}")

    if item.favourites:
        score += min(10, item.favourites * 2)
        reasons.append(f"{item.favourites} favori(s) déjà")

    score = max(0.0, min(100.0, score))
    min_score = float(watch.get("min_score") or 55)
    min_discount = float(watch.get("min_discount") or 0)
    is_deal = score >= min_score and (discount is None or discount >= min_discount)
    return Verdict(round(score, 1), is_deal, discount, ref, reasons)
