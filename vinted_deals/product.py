"""Identification d'un article à partir de son annonce : type précis (pull, jean, veste…),
marque normalisée, couleur et « signature de modèle » (les mots qui distinguent le modèle :
detroit, 501, 990v5, air max 90, cachemire…).

C'est la base de la valorisation par articles similaires (valuation.py) et de l'analyste.
"""
from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------- types d'articles
# Ordre sans importance : on retient le type dont le mot apparaît le plus tôt dans le titre
# (« Veste en jean Levi's » → veste ; « Jean Levi's 501 » → jean), à égalité le plus long.
ITEM_TYPES: dict[str, dict] = {
    "tshirt": {"label": "T-shirt", "words": ["t shirt", "tshirt", "tee shirt", "tee", "teeshirt"]},
    "polo": {"label": "Polo", "words": ["polo", "polos"]},
    "chemise": {"label": "Chemise", "words": ["chemise", "chemisette", "shirt", "overshirt", "surchemise", "flanelle"]},
    "pull": {"label": "Pull", "words": ["pull", "pullover", "pull over", "sweater", "jumper", "knit", "tricot",
                                         "col roule", "maille", "turtleneck"]},
    "gilet": {"label": "Gilet / cardigan", "words": ["gilet", "cardigan"]},
    "sweat": {"label": "Sweat", "words": ["sweat", "sweatshirt", "sweat shirt", "crewneck", "crew neck"]},
    "hoodie": {"label": "Hoodie", "words": ["hoodie", "hoody", "sweat a capuche", "hooded", "zip hoodie"]},
    "veste": {"label": "Veste", "words": ["veste", "jacket", "blouson", "bomber", "harrington", "trucker",
                                           "chore", "workwear jacket", "coach jacket", "veste en jean"]},
    "blazer": {"label": "Blazer / costume", "words": ["blazer", "costume", "veston", "suit"]},
    "manteau": {"label": "Manteau", "words": ["manteau", "coat", "trench", "parka", "caban", "duffle",
                                               "pardessus", "overcoat"]},
    "doudoune": {"label": "Doudoune", "words": ["doudoune", "puffer", "down jacket", "nuptse", "doudounes"]},
    "polaire": {"label": "Polaire", "words": ["polaire", "fleece", "sherpa"]},
    "coupe_vent": {"label": "Coupe-vent", "words": ["coupe vent", "windbreaker", "k way", "kway", "anorak",
                                                     "shell", "softshell", "imperméable", "impermeable"]},
    "jean": {"label": "Jean", "words": ["jean", "jeans"]},
    "pantalon": {"label": "Pantalon", "words": ["pantalon", "pants", "trousers", "chino", "chinos", "cargo",
                                                 "jogging", "jogger", "joggers", "track pants", "baggy"]},
    "short": {"label": "Short", "words": ["short", "shorts", "bermuda"]},
    "survet": {"label": "Survêtement", "words": ["survetement", "tracksuit", "ensemble survetement"]},
    "maillot": {"label": "Maillot (foot…)", "words": ["maillot", "jersey"]},
    "robe": {"label": "Robe", "words": ["robe", "dress"]},
    "jupe": {"label": "Jupe", "words": ["jupe", "skirt"]},
    "top": {"label": "Top / débardeur", "words": ["top", "debardeur", "crop top", "tank top", "body", "brassiere"]},
    "combinaison": {"label": "Combinaison", "words": ["combinaison", "salopette", "overall", "overalls"]},
    "baskets": {"label": "Baskets", "words": ["basket", "baskets", "sneaker", "sneakers", "trainers", "dunk",
                                               "air max", "air force", "jordan", "yeezy", "samba", "gazelle",
                                               "campus", "spezial", "chaussures de sport", "running"]},
    "boots": {"label": "Boots / bottes", "words": ["boots", "bottes", "bottines", "chelsea", "rangers", "1460",
                                                    "santiags"]},
    "chaussures": {"label": "Chaussures de ville", "words": ["mocassin", "mocassins", "derby", "derbies",
                                                              "richelieu", "loafers", "escarpins", "chaussures",
                                                              "chaussure", "shoes", "ballerines"]},
    "sandales": {"label": "Sandales / claquettes", "words": ["sandale", "sandales", "claquettes", "claquette",
                                                             "mules", "tongs", "slides", "birkenstock arizona"]},
    "sac": {"label": "Sac", "words": ["sac", "sacs", "bag", "handbag", "tote", "cabas", "sacoche", "banane",
                                       "backpack", "sac a dos", "besace", "pochette", "clutch", "crossbody"]},
    "maroquinerie": {"label": "Portefeuille / porte-cartes", "words": ["portefeuille", "porte cartes",
                                                                        "porte carte", "porte monnaie",
                                                                        "wallet", "card holder", "cardholder"]},
    "casquette": {"label": "Casquette / bonnet", "words": ["casquette", "cap", "bonnet", "beanie", "bob",
                                                            "chapeau", "hat", "snapback", "trucker cap"]},
    "ceinture": {"label": "Ceinture", "words": ["ceinture", "belt"]},
    "echarpe": {"label": "Écharpe / foulard", "words": ["echarpe", "foulard", "scarf", "carre", "etole"]},
    "lunettes": {"label": "Lunettes", "words": ["lunettes", "sunglasses", "solaires"]},
    "montre": {"label": "Montre", "words": ["montre", "watch", "chronographe", "chronograph"]},
    "bijou": {"label": "Bijou", "words": ["bracelet", "collier", "bague", "boucles d oreilles", "pendentif",
                                           "chaine", "chevaliere", "necklace", "ring", "earrings", "bijou"]},
}

COLORS = {
    "noir": ["noir", "noire", "black"], "blanc": ["blanc", "blanche", "white", "ecru", "creme", "cream", "off white"],
    "gris": ["gris", "grise", "grey", "gray", "anthracite", "chine"], "bleu": ["bleu", "bleue", "blue", "ciel"],
    "marine": ["marine", "navy"], "rouge": ["rouge", "red"], "vert": ["vert", "verte", "green", "olive"],
    "kaki": ["kaki", "khaki"], "beige": ["beige", "sable", "sand", "tan"], "marron": ["marron", "brown", "chocolat"],
    "camel": ["camel", "cognac", "caramel"], "jaune": ["jaune", "yellow", "moutarde"], "orange": ["orange"],
    "rose": ["rose", "pink"], "violet": ["violet", "violette", "purple", "lilas"],
    "bordeaux": ["bordeaux", "burgundy", "prune"], "multi": ["multicolore", "multicolor"],
}

# Mots sans valeur pour distinguer un modèle
STOPWORDS = set("""
de du des d la le les l un une et en a au aux avec pour par sur sans ou the and of for with in by
homme femme hommes femmes men man mens women woman womens unisexe unisex mixte enfant enfants kids kid
garcon fille boy girl junior adulte taille size tailles sz fit coupe regular slim oversize oversized
neuf neuve new nouveau etat tbe be tres bon bonne comme porte portee jamais etiquette etiquettes
tags tag nwt nwot excellent parfait impeccable occasion vintage authentique authentic original originale
veritable vrai rare top super beau belle magnifique joli jolie lot pas cher prix ref reference
modele model style look marque brand collection taille m l s xl xxl xs xxs xxxl
""".split())

SIZE_RE = re.compile(r"^(?:x{0,3}[sl]|m|xxs|\d{1,2}(?:[.,]5)?|[wl]\d{2}|(?:eu|us|uk|fr|it|t)\d{1,2}(?:[.,]5)?|"
                     r"\d{1,2}ans|\d{1,2}m|one|onesize|tu|os)$")

# Valeurs relatives par état (pour ramener le prix d'un comparable à l'état de l'article évalué)
CONDITION_MULT = {6: 1.0, 1: 0.92, 2: 0.80, 3: 0.66, 4: 0.50}
CONDITION_DEFAULT = 0.80


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    text = re.sub(r"([a-z])['’`´]s\b", r"\1s", text)  # Levi's → levis
    text = re.sub(r"['’`´]", " ", text)
    text = re.sub(r"[^a-z0-9.+]+", " ", text)
    text = re.sub(r"(?<![0-9])\.|\.(?![0-9])", " ", text)  # garde « 2.0 », coupe « logo. »
    return " ".join(text.split())


def _tokens(text: str) -> list[str]:
    return norm(text).split()


_TYPE_PATTERNS: list[tuple[str, str, re.Pattern]] = []
for _key, _meta in ITEM_TYPES.items():
    for _w in _meta["words"]:
        _n = norm(_w)
        _TYPE_PATTERNS.append((_key, _n, re.compile(r"(?:^| )" + re.escape(_n) + r"(?: |$)")))
# Mots de type qui désignent aussi un modèle précis : on les garde dans la signature du modèle
MODEL_TYPE_WORDS = {"dunk", "air", "max", "force", "jordan", "yeezy", "samba", "gazelle", "campus", "spezial",
                    "chelsea", "1460", "trucker", "nuptse", "harrington", "chore", "cargo", "chino", "chinos",
                    "bomber", "trench", "parka", "caban", "duffle", "overshirt", "crewneck", "fleece", "sherpa",
                    "anorak", "softshell", "shell", "flanelle", "cardigan", "turtleneck", "baggy", "running",
                    "polo", "tote", "backpack", "banane", "bob", "snapback", "carre", "derby", "loafers",
                    "mocassins", "rangers", "santiags", "arizona"}
_TYPE_WORDS = {tok for _, n, _ in _TYPE_PATTERNS for tok in n.split()} - MODEL_TYPE_WORDS
# Un petit nombre juste après ces mots fait partie du modèle (Air Max 90, Jordan 4, Force 1…)
_NUM_AFTER = {"max", "force", "jordan", "yeezy", "boost", "dunk", "air", "v", "mk", "series", "type", "no", "n"}
_COLOR_WORDS = {norm(w): k for k, ws in COLORS.items() for w in ws}


def item_type(title: str, category: str = "") -> str:
    """Type précis de l'article ('' si inconnu)."""
    for text in (title, category):
        t = norm(text)
        if not t:
            continue
        best = None  # (position, -longueur, clé)
        for key, phrase, pat in _TYPE_PATTERNS:
            m = pat.search(t)
            if m:
                cand = (m.start(), -len(phrase), key)
                if best is None or cand < best:
                    best = cand
        if best:
            return best[2]
    return ""


def type_label(key: str) -> str:
    return ITEM_TYPES.get(key, {}).get("label", "Autre") if key else "Autre"


def color_of(title: str, brand: str = "") -> str:
    t = f" {norm(title)} "
    b = norm(brand)
    if b:
        t = t.replace(f" {b} ", " ")  # « Off-White » est une marque, pas une couleur
    for w, key in _COLOR_WORDS.items():
        if f" {w} " in t:
            return key
    return ""


def brand_norm(brand: str) -> str:
    b = norm(brand).replace(" ", "") if len(norm(brand).replace(" ", "")) <= 3 else norm(brand)
    b = re.sub(r"^(?:the )", "", b)
    return b.strip()


def guess_brand(title: str, known_brands: set[str]) -> str:
    """Marque absente (eBay, Leboncoin…) : la plus longue marque connue présente dans le titre."""
    t = f" {norm(title)} "
    best = ""
    for b in known_brands:
        if len(b) > len(best) and len(b) >= 3 and f" {b} " in t:
            best = b
    return best


def model_tokens(title: str, brand: str = "") -> list[str]:
    """Mots qui caractérisent le modèle, sans marque, type, couleur, taille ni mots creux."""
    brand_toks = set(_tokens(brand)) | set(_tokens(brand.replace(" ", "")))
    out = []
    prev = ""
    for tok in _tokens(title):
        keep_num = tok.isdigit() and prev in _NUM_AFTER
        p, prev = prev, tok
        if keep_num:
            out.append(tok)
            continue
        if tok in brand_toks or tok in STOPWORDS or tok in _TYPE_WORDS or tok in _COLOR_WORDS:
            continue
        if SIZE_RE.match(tok) or (len(tok) < 2 and not tok.isdigit()):
            continue
        out.append(tok)
    return list(dict.fromkeys(out))[:10]


def is_vintage(title: str) -> bool:
    t = f" {norm(title)} "
    return any(w in t for w in (" vintage ", " 90s ", " 80s ", " 00s ", " y2k ", " archive "))


def condition_mult(status_id: int | None) -> float:
    return CONDITION_MULT.get(status_id or -1, CONDITION_DEFAULT)


def fingerprint(title: str, brand: str, category: str = "", status_id: int | None = None,
                known_brands: set[str] | None = None) -> dict:
    b = brand_norm(brand) or (guess_brand(title, known_brands) if known_brands else "")
    return {
        "brand_norm": b,
        "itype": item_type(title, category),
        "model": model_tokens(title, b),
        "color": color_of(title, b),
        "status_id": status_id,
        "vintage": is_vintage(title),
    }
