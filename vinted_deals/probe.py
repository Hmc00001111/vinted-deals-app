"""Diagnostic : interroge chaque site pour de vrai et écrit un rapport JSON.

Usage :  python probe.py [requête] > rapport.json
Lancé automatiquement par GitHub Actions (workflow « Diagnostic des sites »).
"""
from __future__ import annotations

import json
import re
import sys
import time
import traceback

import requests

import sources.base as base
from sources import SOURCE_CLASSES

QUERY = sys.argv[1] if len(sys.argv) > 1 else "nike"

DISCOVERY = {
    "vinted": ["https://www.vinted.fr/"],
    "vestiaire": ["https://fr.vestiairecollective.com/", "https://fr.vestiairecollective.com/search/?q=nike"],
    "depop": ["https://www.depop.com/", "https://webapi.depop.com/api/v3/search/products/?what=nike&country=fr&currency=EUR&items_per_page=4&sort=newlyListed"],
    "grailed": ["https://www.grailed.com/"],
    "ebay": ["https://www.ebay.fr/sch/i.html?_nkw=nike&_sop=10"],
    "stockx": ["https://stockx.com/fr-fr/search?s=nike"],
    "sellpy": ["https://www.sellpy.fr/", "https://www.sellpy.fr/search?query=nike"],
    "jaiio": ["https://www.jaiio.fr/", "https://jaiio.fr/"],
    "leboncoin": ["https://www.leboncoin.fr/recherche?category=72&text=nike"],
}


def discover(url: str, cffi: bool) -> dict:
    s = base.make_session(impersonate=cffi)
    out: dict = {"url": url}
    try:
        r = s.get(url, headers=base.HTML_HEADERS, timeout=25)
        text = r.text
        out.update(status=r.status_code, final_url=str(r.url), length=len(text),
                   server=r.headers.get("server"), content_type=r.headers.get("content-type"))
        out["markers"] = {
            "next_data": "__NEXT_DATA__" in text, "shopify": "cdn.shopify.com" in text or "Shopify.theme" in text,
            "woocommerce": "woocommerce" in text.lower(), "algolia": "algolia" in text.lower(),
            "jsonld": text.count("application/ld+json"), "nuxt": "__NUXT__" in text,
            "blocked": [m for m in base.BLOCK_MARKERS if m.lower() in text.lower()],
        }
        out["algolia_hits"] = sorted(set(re.findall(r'["\']([A-Z0-9]{10})["\']', text)))[:10] if "algolia" in text.lower() else []
        out["keys32"] = sorted(set(re.findall(r'["\']([a-f0-9]{32})["\']', text)))[:10]
        out["scripts"] = re.findall(r'<script[^>]+src="([^"]+)"', text)[:15]
        out["head"] = text[:1500]
        nd = base.extract_next_data(text)
        if nd:
            out["next_keys"] = list((nd.get("props") or {}).get("pageProps", {}).keys())[:30]
            out["next_sample"] = json.dumps(nd, ensure_ascii=False)[:6000]
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def run_source(cls, cffi: bool) -> dict:
    orig = base.has_cffi
    if not cffi:
        base.has_cffi = lambda: False  # simule l'appli Android (requests seul)
    res: dict = {"source": cls.key, "cffi": cffi}
    t = time.time()
    try:
        src = cls()
        if not cffi:
            src.session = base.make_session(impersonate=False)
        items = src.search(QUERY)
        res.update(ok=True, count=len(items), seconds=round(time.time() - t, 1),
                   sample=[{k: getattr(i, k) for k in ("id", "title", "price", "currency", "brand", "size",
                                                        "status", "status_id", "url", "photo", "published_at")}
                           for i in items[:3]])
        if items:
            res["raw_keys"] = list(items[0].raw.keys())[:60]
    except Exception as e:
        res.update(ok=False, error=f"{type(e).__name__}: {e}", seconds=round(time.time() - t, 1),
                   trace=traceback.format_exc()[-1500:])
    finally:
        base.has_cffi = orig
    return res


def probe_vinted_items() -> dict:
    """Vérifie la détection des ventes : routes de détail d'une annonce et lecture de son état."""
    from vinted_client import VintedClient, parse_item_state
    out: dict = {"routes": []}
    try:
        c = VintedClient("www.vinted.fr")
        items = c.search(QUERY)
        out["search_count"] = len(items)
        if not items:
            return out
        ids = [it.id for it in items[:8]]
        for tpl in c.ITEM_ROUTES + ["{base}/items/{id}"]:
            url = tpl.format(api=c.api, base=c.base, id=ids[0])
            try:
                r = c._get(url, headers=c._api_headers() if "/items/" not in tpl[-12:] or "api" in tpl else {})
                out["routes"].append({"route": tpl, "status": r.status_code, "length": len(r.text),
                                      "has_id": str(ids[0]) in r.text, "state": parse_item_state(r.text),
                                      "head": r.text[:400]})
            except Exception as e:
                out["routes"].append({"route": tpl, "error": f"{type(e).__name__}: {e}"})
        if base.has_cffi():  # la page avec une empreinte de vrai navigateur
            try:
                sc = base.make_session(impersonate=True)
                r = sc.get(f"{c.base}/items/{ids[0]}", headers=base.HTML_HEADERS, timeout=25)
                out["html_cffi"] = {"status": r.status_code, "length": len(r.text), "state": parse_item_state(r.text)}
            except Exception as e:
                out["html_cffi"] = {"error": f"{type(e).__name__}: {e}"}
        out["chosen_route"] = c.discover_item_route(ids[0])
        by_id = {it.id: it for it in items}
        out["states"] = {str(i): c.item_state(i, by_id[i].title, by_id[i].price) for i in ids}
        out["search_only"] = {str(i): c._search_state(i, by_id[i].title, by_id[i].price) for i in ids}
        out["search_missing_test"] = c.item_state(1, by_id[ids[0]].title, by_id[ids[0]].price)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        out["trace"] = traceback.format_exc()[-1500:]
    return out


def main() -> None:
    report = {"query": QUERY, "time": time.time(), "has_cffi": base.has_cffi(),
              "ip": None, "results": [], "discovery": {}}
    try:
        report["ip"] = requests.get("https://api.ipify.org?format=json", timeout=10).json()
    except Exception:
        pass
    for cls in SOURCE_CLASSES:
        for cffi in ([True, False] if base.has_cffi() else [False]):
            report["results"].append(run_source(cls, cffi))
            print(f"{cls.key:10s} cffi={cffi!s:5s} -> {report['results'][-1].get('count', report['results'][-1].get('error'))}",
                  file=sys.stderr)
        report["discovery"][cls.key] = [discover(u, c) for u in DISCOVERY.get(cls.key, [])
                                        for c in ([True, False] if base.has_cffi() else [False])]
    report["vinted_item_check"] = probe_vinted_items()
    print("vinted item check ->", report["vinted_item_check"].get("chosen_route"),
          report["vinted_item_check"].get("states"), file=sys.stderr)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
