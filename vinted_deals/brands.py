"""Catalogue de marques : liste intégrée + marques vues sur les sites + marques ajoutées par l'utilisateur.

- suggest(q)  : autocomplétion tolérante aux fautes (« carhart » → Carhartt, « ralf loren » → Ralph Lauren)
- verify(name): marque inconnue → recherche Vinted, on lit les marques des annonces trouvées pour
                en déduire l'orthographe exacte, puis on l'ajoute au catalogue.
"""
from __future__ import annotations

import difflib
import re
import threading
import unicodedata
from collections import Counter

BUILTIN = """
A.P.C.|Abercrombie & Fitch|Acne Studios|Adidas|Adidas Originals|Aéropostale|Aigle|Alain Delon|Alexander McQueen|
Alexander Wang|All Saints|Alpha Industries|Ami Paris|Anine Bing|Arc'teryx|Armani|Armani Exchange|Asics|
Avirex|Axel Arigato|Ba&sh|Balenciaga|Balmain|Barbour|Bape|Bash|Bellerose|Ben Sherman|Benetton|Bershka|
Billabong|Birkenstock|Blundstone|Boss|Bottega Veneta|Brooks Brothers|Burberry|C.P. Company|Calvin Klein|
Calvin Klein Jeans|Camper|Canada Goose|Carhartt|Carhartt WIP|Cartier|Celine|Champion|Chanel|Chevignon|
Chloé|Chrome Hearts|Church's|Claudie Pierlot|Clarks|Columbia|Comme des Garçons|Converse|Corteiz|Cos|
Crocs|Daniel Wellington|Desigual|Diadora|Diesel|Dior|Dickies|Dolce & Gabbana|Dr. Martens|Dsquared2|
Eastpak|Eden Park|Ellesse|Emporio Armani|Essentials|Fear of God|Fendi|Fila|Fred Perry|Gant|Gap|
Gas|Gucci|Guess|H&M|Hackett|Hanes|Harrington|Helly Hansen|Hermès|Hoka|Hollister|Hugo|Hugo Boss|
Iro|Isabel Marant|Jack & Jones|Jacquemus|Jordan|Jott|Juicy Couture|K-Way|Kaporal|Kappa|Kaporal|Kenzo|
Kickers|Kiabi|Lacoste|Lacoste Live|Le Coq Sportif|Le Temps des Cerises|Lee|Lee Cooper|Levi's|Loewe|
Longchamp|Louis Vuitton|Lululemon|Maison Kitsuné|Maison Margiela|Maje|Mango|Marc Jacobs|Marc O'Polo|
Massimo Dutti|Merrell|Michael Kors|Miu Miu|Moncler|Monki|Moschino|Napapijri|New Balance|New Era|
New Look|Nike|Nike ACG|Nudie Jeans|Oakley|Obey|Off-White|Olympique de Marseille|On Running|Only|
Palace|Palm Angels|Paraboot|Patagonia|Paul & Joe|Paul Smith|Pepe Jeans|Petit Bateau|Polo Ralph Lauren|
Prada|Primark|Pull & Bear|Puma|Quiksilver|Ralph Lauren|Ray-Ban|Reebok|Replay|Rick Owens|Roxy|
Saint Laurent|Salomon|Salsa|Sandro|Saucony|Schott|Sézane|Sergio Tacchini|Seiko|Shein|Skechers|Stone Island|
Stradivarius|Stüssy|Supreme|Superdry|Swatch|Teddy Smith|The Kooples|The North Face|Timberland|
Tommy Hilfiger|Tommy Jeans|Trapstar|UGG|Umbro|Under Armour|Uniqlo|Valentino|Vans|Veja|Versace|
Vero Moda|Vivienne Westwood|Weekday|Woolrich|Wrangler|Y-3|Yeezy|Yves Saint Laurent|Zadig & Voltaire|Zara|
Aape|Acqua di Parma|Adererror|Adolfo Dominguez|Aesop|Agnès b.|Alaïa|Alberta Ferretti|Aldo|Allbirds|
American Apparel|American Eagle|American Vintage|And Wander|Anthropologie|Aquascutum|Arket|Asos|Atmosphere|
Autry|Bally|Banana Republic|Barena|Baracuta|Bellroy|Belstaff|Berghaus|Bershka|Bobbies|Bonpoint|
Boohoo|Bonobo|Bosideng|Brandy Melville|Brax|Breitling|Burton|Caterpillar|Catimini|Cerruti 1881|
Chevignon|Christian Louboutin|Closed|Coach|Cotélac|Cyrillus|Dc Shoes|Dc|Des Petits Hauts|Diane von Furstenberg|
Dkny|Dockers|Dolce Vita|Drykorn|Dune London|Ecco|Element|Emilio Pucci|Erl|Esprit|Etam|Etro|Fendi|
Ferragamo|Filson|Fjällräven|Forever 21|Fossil|Free People|French Connection|Fruit of the Loom|Fusalp|
G-Star Raw|Ganni|Geox|Gerard Darel|Givenchy|Golden Goose|Gramicci|Grenson|Hackett London|Haglöfs|
Harris Wilson|Heron Preston|Herno|Hoka One One|Hunter|IKKS|Isabel Marant Étoile|Issey Miyake|J.Crew|
J.M. Weston|Jack Wolfskin|Jacadi|Jil Sander|Jimmy Choo|Joe Fresh|John Smedley|Jules|Junya Watanabe|
Karl Lagerfeld|Kate Spade|Kith|Kooples|Lanvin|Le Slip Français|Lemaire|Les Deux|Levi's Vintage Clothing|
Liu Jo|Loro Piana|Lyle & Scott|Mackage|Marimekko|Marni|Max Mara|MCM|Mephisto|Michael Michael Kors|
Mih Jeans|Mismo|Missoni|Miu Miu|Moncler Grenoble|Montblanc|Mulberry|Mustang|Naf Naf|Nanushka|
Neighborhood|Nike SB|Nixon|Noah|Norse Projects|North Sails|Nudie|Oasis|Officine Générale|Omega|
Orlebar Brown|Our Legacy|Paco Rabanne|Parajumpers|Patrizia Pepe|Peak Performance|Penfield|Pinko|
Polo Sport|Pretty Green|Promod|Proenza Schouler|Rains|Rapha|Reiss|Repetto|Rimowa|River Island|Roger Vivier|
Rolex|Rotate|Rouje|Sacai|Saint James|Samsøe Samsøe|Scotch & Soda|See by Chloé|Sessùn|Simone Rocha|
Sinéquanone|Six Pieds Trois Pouces|Sorel|Spyder|Stella McCartney|Stone Island Shadow Project|Suicoke|
Sunspel|Superga|Swarovski|Tag Heuer|Ted Baker|Tissot|Tod's|Tory Burch|Toms|True Religion|Trussardi|
Uggs|Undercover|United Colors of Benetton|Urban Outfitters|Valentino Garavani|Vanessa Bruno|Vilebrequin|
Visvim|Wales Bonner|Wood Wood|Wtaps|Yohji Yamamoto|YMC|Zimmermann|Zara Kids|& Other Stories|
114 Studio|1017 Alyx 9SM|3.1 Phillip Lim|47 Brand|7 For All Mankind|Adidas by Stella McCartney|Ader Error|
Aime Leon Dore|Alpha|Andersen-Andersen|Arte Antwerp|Asics Tiger|Australian|Autry Action Shoes|Avnier|
Bershka Man|Billionaire Boys Club|Blue Blue Japan|Brunello Cucinelli|C.P. Company Undersixteen|Cav Empt|
Comme des Garçons Play|Converse x Comme des Garçons|Daily Paper|Denim Tears|Dime|Drôle de Monsieur|
Ed Hardy|Engineered Garments|Evisu|Fred Perry x Raf Simons|Gallery Dept|Godspeed|Hélas|Human Made|
Iceberg|Kangol|Karhu|Kenzo Paris|Kangaroos|Mammut|Marine Serre|Martine Rose|Massimo Dutti Kids|
Minimum|Mizuno|Montbell|Napapijri Skidoo|Nautica|Needles|Nigel Cabourn|Nike x Off-White|Noon Goons|
Onitsuka Tiger|Patta|Pleasures|Polar Skate Co|Raf Simons|Represent|Ripndip|Sporty & Rich|Stüssy x Nike|
The Hundreds|Thrasher|Tiger of Sweden|Union|Vetements|Vision Street Wear|Wacko Maria|Wasted Paris|
Wtaps|Yardsale|Yves Salomon|Zara Man|Sézane|Rouje|Balzac Paris|Jonak|Minelli|San Marina|André|Bocage|
Eram|Gémo|La Halle|Celio|Brice|Devred|Armand Thiery|Jennyfer|Pimkie|Cache Cache|Bonobo Jeans|Camaïeu|
Morgan|Kookaï|Caroll|Comptoir des Cotonniers|Mango Man|Tara Jarmon|Paule Ka|Lancel|Le Tanneur|
Lancaster|Furla|Guess Jeans|Liu Jo Jeans|Elisabetta Franchi|Twinset|Hogan|Santoni|Valextra|Goyard|
Delvaux|Moynat|Polène|Mansur Gavriel|Staud|Cult Gaia|By Far|Wandler|Jacquemus Le Chiquito|Coperni|
Courrèges|Pierre Cardin|Lanvin Paris|Thierry Mugler|Mugler|Jean Paul Gaultier|Kenzo Jungle|Chloé Kids|
Tartine et Chocolat|Absorba|Okaïdi|Obaïdi|Du Pareil au Même|DPAM|Vertbaudet|Sergent Major|Orchestra|
Tape à l'œil|Lego Wear|Disney|Marvel|Pokémon|Decathlon|Quechua|Kalenji|Domyos|Kipsta|Tribord|Wed'ze|
Rossignol|Millet|Lafuma|Eider|The North Face Purple Label|Peak Performance|Picture Organic|Volcom|
Element Skateboards|Santa Cruz|Independent|Spitfire|Huf|Primitive|Diamond Supply Co|Brixton|Rvca|
O'Neill|Rip Curl|Hurley|Oxbow|Lightning Bolt|Hawaiian Tropic|Speedo|Arena|Tommy Bahama|Vineyard Vines|
Brooks|Mizuno Wave|Altra|Hoka Clifton|On Cloud|Merrell Moab|La Sportiva|Scarpa|Lowa|Meindl|Danner|
Red Wing|Wolverine|Grenson|Tricker's|Crockett & Jones|Alden|Allen Edmonds|Loake|Sebago|Sperry|
Paraboot Michael|Heschung|Weston|Berluti|Corthay|Aubercy|Bexley|Finsbury|Bensimon|Le Mont Saint Michel|
Armor Lux|Saint James Marinière|Orcival|Bleu de Paname|Vêtements Bleu de Chauffe|Bleu de Chauffe|
Le Laboureur|Vetra|Arpenteur|Maison Labiche|Faguo|Hast|Asphalte|Loom|1083|Atelier Tuffery|Noyoco|
Sessun|Sezane|Musier|Petite Mendigote|Louise Misha|Bellerose Kids|Bonton|Zef|Imps & Elfs|Liewood|
Mini Rodini|Bobo Choses|Konges Sløjd|Stella McCartney Kids|Ralph Lauren Kids|Tommy Hilfiger Kids
"""

ALIASES = {"nb": "New Balance", "tnf": "The North Face", "rl": "Ralph Lauren", "prl": "Polo Ralph Lauren",
           "cdg": "Comme des Garçons", "lv": "Louis Vuitton", "ysl": "Saint Laurent", "dg": "Dolce & Gabbana",
           "cp": "C.P. Company", "si": "Stone Island", "tn": "Nike", "af1": "Nike", "ck": "Calvin Klein",
           "th": "Tommy Hilfiger", "ea7": "Emporio Armani", "ax": "Armani Exchange", "mk": "Michael Kors",
           "zv": "Zadig & Voltaire", "apc": "A.P.C.", "ow": "Off-White", "fog": "Fear of God", "cg": "Canada Goose",
           "docs": "Dr. Martens", "doc martens": "Dr. Martens", "wip": "Carhartt WIP", "tnf purple": "The North Face Purple Label"}

_brands_lock = threading.Lock()


def phon(k: str) -> str:
    """Clé « phonétique » simplifiée (français/anglais) pour rattraper les fautes de son."""
    for a, b in (("ph", "f"), ("ck", "k"), ("qu", "k"), ("c", "k"), ("y", "i"), ("eau", "o"), ("au", "o"),
                 ("ou", "u"), ("z", "s"), ("w", "v"), ("h", ""), ("ee", "i")):
        k = k.replace(a, b)
    k = re.sub(r"(.)\1+", r"\1", k)
    return k.rstrip("e") or k


def key(name: str) -> str:
    """Forme de comparaison : minuscules, sans accents, sans ponctuation ni espaces."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", "and")
    return re.sub(r"[^a-z0-9]", "", s)


def _clean(name: str) -> str:
    return " ".join((name or "").replace("\n", " ").split()).strip(" ,;|")


class BrandCatalog:
    def __init__(self, db=None, market_idx=None):
        self.db = db
        self.idx = market_idx
        self.names: dict[str, str] = {}      # clé → nom affiché
        self.popularity: Counter = Counter()
        for n in BUILTIN.split("|"):
            n = _clean(n)
            if n:
                self.names.setdefault(key(n), n)
                self.popularity[key(n)] += 5
        if db:
            for n in db.get_setting("custom_brands", []) or []:
                self.names[key(n)] = n
                self.popularity[key(n)] += 20
            self._learn_from_db()

    def _learn_from_db(self) -> None:
        """Toutes les marques vues dans les annonces (avec leur orthographe la plus fréquente)."""
        try:
            with self.db.lock:
                rows = self.db.conn.execute(
                    "SELECT brand, COUNT(*) FROM listings WHERE brand IS NOT NULL AND brand != '' "
                    "GROUP BY brand").fetchall()
        except Exception:
            return
        for name, n in rows:
            self.learn(name, n)

    def learn(self, name: str, weight: int = 1) -> None:
        name = _clean(name)
        k = key(name)
        if len(k) < 2 or len(name) > 40:
            return
        with _brands_lock:
            if k not in self.names:
                self.names[k] = name
            self.popularity[k] += weight

    def all(self) -> list[str]:
        with _brands_lock:
            return [self.names[k] for k, _ in sorted(self.names.items(), key=lambda kv: -self.popularity[kv[0]])]

    # ---------- recherche tolérante aux fautes ----------
    def suggest(self, q: str, limit: int = 12) -> list[str]:
        qk = key(q)
        if not qk:
            return self.all()[:limit]
        with _brands_lock:
            items = list(self.names.items())
            pop = dict(self.popularity)
        scored = []
        alias = ALIASES.get(q.strip().lower()) or ALIASES.get(qk)
        if alias:
            scored.append((9.0, alias))
        qp = phon(qk)
        for k, name in items:
            if k.startswith(qk):
                s = 3.0 - len(k) / 100
            elif qk in k:
                s = 2.0
            else:
                r = difflib.SequenceMatcher(None, qk, k[: max(len(qk) + 2, 1)]).ratio()
                r2 = difflib.SequenceMatcher(None, qk, k).ratio()
                kp = phon(k)
                r3 = 1.0 if kp == qp else difflib.SequenceMatcher(None, qp, kp).ratio()
                s = max(r, r2, r3 - 0.02)
                if s < 0.72:
                    continue
            scored.append((s + min(0.3, pop.get(k, 0) / 500), name))
        scored.sort(key=lambda x: -x[0])
        return list(dict.fromkeys(n for _, n in scored))[:limit]

    def best_match(self, q: str) -> tuple[str | None, float]:
        qk = key(q)
        if not qk:
            return None, 0.0
        alias = ALIASES.get(q.strip().lower()) or ALIASES.get(qk)
        if alias:
            return alias, 1.0
        with _brands_lock:
            if qk in self.names:
                return self.names[qk], 1.0
            items = list(self.names.items())
        best, score = None, 0.0
        qp = phon(qk)
        for k, name in items:
            r = max(difflib.SequenceMatcher(None, qk, k).ratio(),
                    0.97 if phon(k) == qp else difflib.SequenceMatcher(None, qp, phon(k)).ratio() - 0.02)
            if r > score:
                best, score = name, r
        return best, score

    # ---------- vérification d'une marque inconnue ----------
    def verify(self, q: str, search=None) -> dict:
        """search(texte) -> liste d'annonces (avec .brand). Renvoie le nom correct si on le trouve."""
        q = _clean(q)
        if not q:
            return {"status": "empty", "name": "", "suggestions": []}
        name, score = self.best_match(q)
        if score >= 1.0:
            return {"status": "known", "name": name, "suggestions": []}
        if score >= 0.86:
            return {"status": "corrected", "name": name, "suggestions": self.suggest(q, 5)}
        sug = self.suggest(q, 5)
        if sug and len(key(q)) >= 4 and key(sug[0]).startswith(key(q)):
            return {"status": "corrected", "name": sug[0], "suggestions": sug}
        if score >= 0.72 and name:  # faute probable sur une marque connue : pas besoin d'interroger Vinted
            return {"status": "corrected", "name": name, "suggestions": sug}
        found = None
        if search:
            try:
                items = search(q) or []
            except Exception:
                items = []
            brands = Counter(_clean(getattr(i, "brand", "")) for i in items)
            brands.pop("", None)
            qk = key(q)
            cands = []
            for b, n in brands.items():
                r = difflib.SequenceMatcher(None, qk, key(b)).ratio()
                if qk in key(b) or key(b) in qk:
                    r = max(r, 0.8)
                if r >= 0.6:
                    cands.append((r * 2 + n / max(1, len(items)), b))
            if cands:
                found = max(cands)[1]
        if found:
            self.add(found)
            return {"status": "verified", "name": found, "suggestions": []}
        if score >= 0.72:
            return {"status": "corrected", "name": name, "suggestions": self.suggest(q, 5)}
        return {"status": "unknown", "name": q, "suggestions": self.suggest(q, 5)}

    def add(self, name: str) -> None:
        name = _clean(name)
        self.learn(name, 20)
        if self.db:
            cur = self.db.get_setting("custom_brands", []) or []
            if key(name) not in {key(x) for x in cur}:
                self.db.set_settings({"custom_brands": cur + [name]})
