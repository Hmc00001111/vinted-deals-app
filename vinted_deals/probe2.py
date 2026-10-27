"""Sonde ciblée : configuration Algolia de Sellpy."""
import json, re, sys, requests
out = {}
s = requests.Session(); s.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
html = s.get("https://www.sellpy.fr/search?query=nike", timeout=20).text
main = re.findall(r'src="(/market/[^"]+\.js)"', html)[0]
js = s.get("https://www.sellpy.fr" + main, timeout=30).text
names = sorted(set(re.findall(r'(?:market/|\./)((?:config|algolia|algoliaItems|getDefaultAlgoliaFilters|constants|region|usePersonalisedSearch)[.\w-]*\.bundle\.js)', js)))
out["chunks"] = names
for n in names:
    try:
        c = s.get("https://www.sellpy.fr/market/" + n, timeout=30).text
    except Exception as e:
        out[n] = repr(e); continue
    info = {"len": len(c),
            "ids10": sorted(set(re.findall(r'["\'`]([A-Z0-9]{10})["\'`]', c)))[:20],
            "keys32": sorted(set(re.findall(r'["\'`]([a-f0-9]{32})["\'`]', c)))[:20],
            "idx": sorted(set(re.findall(r'["\'`]([a-zA-Z0-9_\-]*(?:[Ii]tem|[Ii]ndex|[Pp]roduct)[a-zA-Z0-9_\-]*)["\'`]', c)))[:60]}
    if len(c) < 60000:
        info["text"] = c[:20000]
    else:
        info["ctx"] = [c[max(0,m.start()-300):m.start()+300] for m in re.finditer(r'appId|apiKey|indexName|algoliasearch|initIndex', c)][:20]
    out[n] = info
json.dump(out, sys.stdout, indent=1)
