"""Sonde ciblée : bundle Sellpy (config Algolia) + variantes d'API Depop."""
import json, re, sys, requests
out = {}
s = requests.Session(); s.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
try:
    html = s.get("https://www.sellpy.fr/search?query=nike", timeout=20).text
    bundles = re.findall(r'src="(/market/[^"]+\.js)"', html)
    out["sellpy_bundles"] = bundles
    ctx = []
    for b in bundles[:3]:
        js = s.get("https://www.sellpy.fr" + b, timeout=30).text
        out.setdefault("sellpy_bundle_len", []).append(len(js))
        for m in re.finditer(r'algolia', js, re.I):
            ctx.append(js[max(0, m.start()-200): m.start()+300])
            if len(ctx) > 25: break
        out["sellpy_appids"] = sorted(set(re.findall(r'["\']([A-Z0-9]{10})["\']', js)))[:30]
        out["sellpy_keys"] = sorted(set(re.findall(r'["\']([a-f0-9]{32})["\']', js)))[:30]
        out["sellpy_index"] = sorted(set(re.findall(r'["\']([A-Za-z0-9_\-]*(?:[Ii]tem|[Pp]roduct|[Ss]earch)[A-Za-z0-9_\-]*)["\']', js)))[:120]
        # imports dynamiques : autres bundles
        out["sellpy_chunks"] = sorted(set(re.findall(r'["\'](\./[^"\']+\.js|/market/[^"\']+\.js)["\']', js)))[:80]
    out["sellpy_ctx"] = ctx
except Exception as e:
    out["sellpy_error"] = repr(e)
dep = {}
for url in ["https://webapi.depop.com/api/v3/search/products/?what=nike&country=fr&currency=EUR&items_per_page=4",
            "https://webapi.depop.com/api/v4/search/products/?what=nike&country=fr&currency=EUR&items_per_page=4",
            "https://webapi.depop.com/api/v2/search/products/?what=nike&country=fr&currency=EUR&itemsPerPage=4",
            "https://webapi.depop.com/api/v1/search/products/?what=nike&country=fr&currency=EUR&items_per_page=4",
            "https://webapi.depop.com/presentation/api/v1/search/products/?what=nike&country=fr&currency=EUR&items_per_page=4",
            "https://api.depop.com/api/v3/search/products/?what=nike&country=fr&currency=EUR&items_per_page=4"]:
    try:
        r = s.get(url, timeout=15, headers={"Accept": "application/json", "Origin": "https://www.depop.com", "Referer": "https://www.depop.com/"})
        dep[url] = [r.status_code, r.text[:400]]
    except Exception as e:
        dep[url] = repr(e)
out["depop"] = dep
json.dump(out, sys.stdout, indent=1)
