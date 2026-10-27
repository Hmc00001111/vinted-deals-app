"""Briques communes à tous les sites : modèle d'annonce, session HTTP, outils d'extraction."""
from __future__ import annotations

import html as htmllib
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import requests

log = logging.getLogger("deals.sources")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.6 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0",
]

# États harmonisés (mêmes identifiants que Vinted)
STATUS_LABELS = {
    6: "Neuf avec étiquette",
    1: "Neuf sans étiquette",
    2: "Très bon état",
    3: "Bon état",
    4: "Satisfaisant",
}

# Conversion approximative vers l'euro (rafraîchie au démarrage si possible)
FX_TO_EUR = {"EUR": 1.0, "USD": 0.86, "GBP": 1.16, "CHF": 1.07, "SEK": 0.09, "PLN": 0.235,
             "DKK": 0.134, "CZK": 0.04, "HUF": 0.0025, "RON": 0.2, "NOK": 0.085}


class SourceError(Exception):
    """Erreur lisible par l'utilisateur (affichée à côté du site)."""


class Blocked(SourceError):
    """Le site refuse nos requêtes (protection anti-robot)."""


@dataclass
class Item:
    source: str
    id: str                       # identifiant propre au site
    title: str
    price: float                  # en euros
    url: str
    currency: str = "EUR"         # devise d'origine
    orig_price: float | None = None
    total_price: float | None = None
    brand: str = ""
    size: str = ""
    status: str = ""
    status_id: int | None = None
    photo: str | None = None
    photos: list[str] = field(default_factory=list)
    favourites: int = 0
    views: int = 0
    seller: str = ""
    published_at: float | None = None
    category: str = ""
    description: str = ""
    raw: dict = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------- HTTP
def has_cffi() -> bool:
    try:
        import curl_cffi  # noqa: F401
        return True
    except Exception:
        return False


def make_session(impersonate: bool = True, user_agent: str | None = None):
    """Session HTTP. Utilise curl_cffi (empreinte de vrai navigateur) s'il est installé,
    ce qui passe beaucoup mieux les protections anti-robot ; sinon requests."""
    if impersonate and has_cffi():
        from curl_cffi import requests as creq
        s = creq.Session(impersonate="chrome")
        if user_agent:
            s.headers.update({"User-Agent": user_agent})
        s.headers.update({"Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"})
        return s
    s = requests.Session()
    s.headers.update({
        "User-Agent": user_agent or random.choice(USER_AGENTS),
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    })
    return s


HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
}
JSON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-site",
}

BLOCK_MARKERS = ("captcha-delivery.com", "datadome", "px-captcha", "perimeterx",
                 "cf-chl", "challenge-platform", "Attention Required", "Pardon Our Interruption",
                 "Access Denied", "_Incapsula_", "Just a moment...")


def check_response(r, site: str) -> None:
    """Lève une erreur claire selon la réponse."""
    code = r.status_code
    text = ""
    try:
        text = r.text[:3000]
    except Exception:
        pass
    blocked = any(m.lower() in text.lower() for m in BLOCK_MARKERS)
    if code in (401, 403, 429) or (blocked and code >= 400):
        raise Blocked(f"{site} bloque les requêtes automatiques (HTTP {code})")
    if code >= 400:
        raise SourceError(f"{site} : HTTP {code}")
    if blocked and len(text) < 3000 and "<html" in text.lower():
        raise Blocked(f"{site} affiche une vérification anti-robot")


# ---------------------------------------------------------------- extraction
def money(value: Any) -> float | None:
    """Convertit '1 234,56 €', '12.5', {'amount': '3.0'}, 1999 (centimes non gérés) en float."""
    if value is None:
        return None
    if isinstance(value, dict):
        for k in ("amount", "priceAmount", "value", "price"):
            if k in value:
                return money(value[k])
        if "cents" in value:
            try:
                return float(value["cents"]) / 100
            except (TypeError, ValueError):
                return None
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace("\xa0", " ").replace(" ", " ")
    m = re.search(r"\d[\d\s.,]*", s)
    if not m:
        return None
    num = m.group(0).strip().replace(" ", "")
    if "," in num and "." in num:  # 1.234,56 ou 1,234.56
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        head, _, tail = num.rpartition(",")
        num = f"{head.replace(',', '')}.{tail}" if len(tail) in (1, 2) else num.replace(",", "")
    try:
        return float(num)
    except ValueError:
        return None


def to_eur(amount: float | None, currency: str | None) -> float | None:
    if amount is None:
        return None
    return round(amount * FX_TO_EUR.get((currency or "EUR").upper(), 1.0), 2)


def strip_tags(s: str) -> str:
    s = re.sub(r"<!--.*?-->", "", s or "", flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    return " ".join(htmllib.unescape(s).split())


def extract_next_data(html: str) -> dict | None:
    m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def extract_json_ld(html: str) -> list:
    out = []
    for m in re.finditer(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(m.group(1).strip())
        except ValueError:
            continue
        out.extend(data if isinstance(data, list) else [data])
    return out


def walk(obj: Any, pred: Callable[[dict], bool], limit: int = 500) -> list[dict]:
    """Parcourt récursivement un JSON et renvoie les dictionnaires qui vérifient pred."""
    found: list[dict] = []
    stack = [obj]
    seen = 0
    while stack and len(found) < limit:
        cur = stack.pop()
        seen += 1
        if seen > 200_000:
            break
        if isinstance(cur, dict):
            try:
                if pred(cur):
                    found.append(cur)
                    continue
            except Exception:
                pass
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(reversed(cur))
    return found


def first(d: dict, *keys: str, default=None):
    for k in keys:
        cur: Any = d
        ok = True
        for part in k.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                ok = False
                break
        if ok and cur not in (None, "", [], {}):
            return cur
    return default


def dedupe(items: Iterable[Item]) -> list[Item]:
    seen, out = set(), []
    for it in items:
        if it.id and it.id not in seen:
            seen.add(it.id)
            out.append(it)
    return out


def guess_status(text: str) -> int | None:
    """Devine l'état harmonisé depuis un libellé libre (FR/EN)."""
    t = (text or "").lower()
    if not t:
        return None
    rules = [
        (6, ("avec étiquette", "with tags", "nwt", "neuf avec", "new with tag", "brand new", "deadstock",
             "neufetiquette")),
        (1, ("neuf sans", "without tags", "nwot", "jamais porté", "never worn", "neuf", "new", "is_new",
             "etatneuf")),
        (2, ("très bon", "tres bon", "very good", "excellent", "gently used", "is_gently_used",
             "like new", "comme neuf", "tresbonetat")),
        (3, ("bon état", "bon etat", "good", "used", "occasion", "is_used", "pre-owned", "bonetat")),
        (4, ("satisfaisant", "fair", "worn", "is_worn", "acceptable", "abîmé", "etatsatisfaisant")),
    ]
    for sid, keys in rules:
        if any(k in t for k in keys):
            return sid
    return None


def parse_ts(value: Any) -> float | None:
    """Timestamp Unix depuis un nombre (s ou ms) ou une date ISO."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000 if v > 1e12 else v
    s = str(value).strip()
    if s.isdigit():
        return parse_ts(int(s))
    from datetime import datetime, timezone
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            d = datetime.strptime(s.replace("Z", "+0000"), fmt)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d.timestamp()
        except ValueError:
            continue
    return None


class Source:
    """Classe de base d'un connecteur."""
    key = ""
    label = ""
    description = ""
    home = ""
    min_delay = 2.5
    first_scan_pages = 2

    def __init__(self, domain_hint: str | None = None):
        self._last_call = 0.0
        self.session = None

    def throttle(self) -> None:
        wait = self.min_delay + random.uniform(0, 1.2) - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def get_session(self):
        if self.session is None:
            self.session = make_session()
        return self.session

    def reset(self) -> None:
        self.session = None

    def search(self, query: str, page: int = 1, category: str | None = None) -> list[Item]:
        raise NotImplementedError
