"""Catégories d'articles (optionnelles).

Quand le site a une catégorie native connue, elle est envoyée dans la recherche ;
sinon l'annonce est filtrée sur les mots de son titre (et de sa catégorie si le site la donne).
"""
from __future__ import annotations

import unicodedata

CATEGORIES: dict[str, dict] = {
    "vetements": {
        "label": "Vêtements",
        "words": ["veste", "manteau", "jean", "jeans", "pantalon", "t-shirt", "tshirt", "tee", "sweat",
                  "sweatshirt", "hoodie", "pull", "chemise", "robe", "jupe", "short", "blouson",
                  "doudoune", "polo", "gilet", "cardigan", "parka", "trench", "costume", "blazer",
                  "jogging", "survetement", "maillot", "debardeur", "top", "combinaison", "salopette",
                  "jacket", "coat", "pants", "trousers", "shirt", "dress", "skirt", "sweater", "jumper",
                  "knit", "tracksuit", "vest", "puffer", "denim", "overshirt", "crewneck", "fleece",
                  "polaire", "anorak", "kimono", "chino", "cargo", "legging", "body", "bomber"],
    },
    "chaussures": {
        "label": "Chaussures & sneakers",
        "words": ["chaussure", "chaussures", "basket", "baskets", "sneaker", "sneakers", "boots", "bottes",
                  "bottines", "mocassin", "mocassins", "derby", "derbies", "richelieu", "escarpin",
                  "escarpins", "sandale", "sandales", "tong", "claquette", "mule", "mules", "espadrille",
                  "ballerine", "jordan", "dunk", "air max", "air force", "yeezy", "new balance", "asics",
                  "shoes", "trainers", "loafers", "heels", "slides", "clogs", "sabots", "rangers", "chelsea"],
    },
    "sacs": {
        "label": "Sacs & maroquinerie",
        "words": ["sac", "sacs", "bag", "bags", "pochette", "portefeuille", "porte-monnaie", "porte-cartes",
                  "wallet", "cabas", "tote", "sacoche", "banane", "backpack", "sac a dos", "valise", "bagage",
                  "clutch", "handbag", "crossbody", "besace", "cartable", "keepall", "speedy", "neverfull",
                  "card holder", "cardholder"],
    },
    "accessoires": {
        "label": "Accessoires",
        "words": ["ceinture", "casquette", "bonnet", "echarpe", "foulard", "chapeau", "bob", "gants",
                  "lunettes", "cravate", "noeud papillon", "belt", "cap", "hat", "beanie", "scarf",
                  "sunglasses", "gloves", "tie", "carre", "bandana", "porte-cles", "keychain", "parapluie",
                  "chaussettes", "socks", "bretelles"],
    },
    "montres_bijoux": {
        "label": "Montres & bijoux",
        "words": ["montre", "montres", "watch", "watches", "bracelet", "collier", "bague", "bagues",
                  "boucles d'oreilles", "boucle d'oreille", "pendentif", "chaine", "chevaliere", "broche",
                  "necklace", "ring", "earrings", "pendant", "chain", "jewelry", "bijou", "bijoux", "rolex",
                  "omega", "seiko", "cartier", "chronographe", "chronograph"],
    },
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    for ch in "-/()[],.!?:;'\"":
        s = s.replace(ch, " ")
    return " ".join(s.split())


_NORMALIZED = {k: [_norm(w) for w in v["words"]] for k, v in CATEGORIES.items()}


def parse_categories(value: str | None) -> list[str]:
    return [c.strip() for c in (value or "").split(",") if c.strip() in CATEGORIES]


def matches_category(title: str, raw_category: str, cats: list[str]) -> bool:
    if not cats:
        return True
    text = f" {_norm(title)} {_norm(raw_category)} "
    return any(f" {w} " in text for c in cats for w in _NORMALIZED.get(c, []))


def category_meta() -> list[dict]:
    return [{"key": k, "label": v["label"]} for k, v in CATEGORIES.items()]
