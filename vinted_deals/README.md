# Vinted Deals — version PC

Surveille en continu les nouvelles annonces de plusieurs sites de mode et fait remonter les
bonnes affaires (prix sous le marché, articles rares), avec un avis de Gemini sur les photos.

## Installation

```bash
cd vinted_deals
python -m venv .venv
# Windows : .venv\Scripts\activate    |    macOS/Linux : source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

L'interface s'ouvre sur http://127.0.0.1:5000. Ta base existante (`vinted_deals.db`) est reprise
automatiquement, avec tes surveillances et l'historique des prix.

## Sites (« moteurs de recherche »)

Chaque surveillance a ses propres cases à cocher. L'état de chaque site s'affiche en haut de
l'écran (✓ nombre d'annonces lues, ⛔ bloqué, ✗ erreur — passer la souris dessus pour le détail).

| Site | Méthode | Testé en direct (oct. 2026) |
|---|---|---|
| Vinted | API du site | ✓ |
| Vestiaire Collective | API de recherche du site | ✓ |
| Grailed | Index de recherche Algolia du site | ✓ (prix convertis depuis le dollar) |
| Sellpy | Index de recherche Algolia du site | ✓ |
| Jaiio | Catalogue Shopify (dernières mises en ligne) | ✓ |
| Leboncoin | API de l'appli mobile | ⚠️ protégé (Datadome) : marche par moments, nécessite `curl_cffi` |
| Depop | API du site | ⚠️ protégé (Cloudflare) |
| eBay | Page de résultats « plus récents » | ⚠️ protégé (Akamai) |
| StockX | Page de recherche | ⚠️ très protégé |

Les sites ⚠️ ont bloqué les tests depuis un serveur ; depuis une connexion personnelle ils
peuvent passer, surtout avec `curl_cffi` installé (inclus dans `requirements.txt`). Un site
bloqué n'empêche pas les autres de fonctionner.

## Comment ça marche

1. Tu crées une **surveillance** : mots-clés et/ou marques, sites cochés, prix, états, tailles,
   **catégories** (optionnel) et **fréquence de scan** (30 s à 1 h).
2. À chaque passage, seules les nouvelles annonces sont traitées. Le premier passage sur un
   site sert à construire la référence de prix (pas d'alerte).
3. **Premier tri** — score sur 100 : décote par rapport au prix de référence (le tien, ou la
   médiane observée sur ce site, ou tous sites confondus au début), mots « rares », état, favoris.
4. **Deuxième tri — Gemini** : les annonces retenues sont envoyées à Gemini avec leurs photos.
   Il rend un verdict (✅ Acheter / 🔍 À vérifier / ⛔ À éviter), une note sur 10, un avis sur
   l'authenticité et l'état visible, une valeur estimée et les points d'attention.
5. Une alerte part ensuite (navigateur, Discord, Telegram, notification Android). Si Gemini
   dit « Acheter » avec un score très élevé, l'alerte est **prioritaire**.

### Gemini

Clé gratuite sur https://aistudio.google.com/apikey, à coller dans **Réglages**. Elle reste
dans la base locale. Le nombre d'analyses par heure est limité (30 par défaut) pour rester
dans le quota gratuit ; le modèle est choisi automatiquement s'il n'existe plus.

## Valeur estimée (articles similaires)

La valeur d'un article n'est plus la moyenne de la marque : elle est calculée à partir des annonces
**les plus proches** — même marque **et** même type (pull, jean, veste, doudoune, baskets…), même
**modèle** (mots distinctifs : Detroit, 501, Nuptse, cachemire…, les mots rares comptant plus),
prix ramenés au même **état**, couleur, fraîcheur de l'observation. Les articles **réellement vendus**
comptent double et corrigent l'écart entre prix demandés et prix de vente. Chaque valeur a une
fourchette, le nombre de comparables et un indice de confiance (bouton « Valeur & comparables »).

Le bouton **🤖 Identifier** envoie les photos à Gemini avec la **recherche Google** : modèle exact,
référence, prix neuf, prix d'occasion constatés (avec les sources), authenticité. Son estimation est
combinée à celle des comparables.

## 📊 Analyste

Classe les articles **les plus vendus** sur 24 h, 7 jours ou 30 jours (filtres type / marque), groupés
par marque + type + modèle : nombre de ventes, prix de vente médian, délai de vente, taux
d'écoulement, tendance des prix. Pour chaque groupe : **les annonces très similaires à acheter
maintenant**, avec la marge attendue.

Les ventes sont détectées en **re-vérifiant** les annonces Vinted vues (3 h, 8 h, 24 h, 48 h après,
puis tous les 3 jours pendant 30 jours) : une annonce qui passe « vendue » donne un vrai prix et un
vrai délai de vente. Tant qu'il n'y a pas assez de ventes, le classement se fait sur les favoris.

## 💎 Pépites

Pour chaque annonce en vente : coût réel (prix + protection acheteur + port), revente visée
(un peu sous la valeur), **bénéfice net**, rentabilité, **probabilité de revendre et délai** d'après
les ventes détectées, confiance. Le score combine bénéfice espéré, rentabilité, vitesse et
confiance, et pénalise les risques (prix suspect, marque très contrefaite non vérifiée, peu de
données). Alerte (notification) au-dessus d'un score réglable ; les meilleures sont identifiées
automatiquement par Gemini.

**Veille marché** (Réglages) : liste de marques / mots-clés scannés en continu sur Vinted pour
nourrir l'analyste et trouver des pépites en dehors de tes surveillances. Le bouton « Marques de
revente populaires » en propose une vingtaine.

Données mobiles : le suivi des ventes fait par défaut 60 vérifications par heure (réglable).

## 🤖 Robot (appli Android et PC)

Le robot agit sur **ton compte Vinted** via un navigateur intégré à l'appli : tu t'y connectes
toi-même (onglet Robot → « Connexion Vinted »), ton mot de passe reste sur ton téléphone.

- **Négocier** : sur chaque pépite, offre conseillée (la plus basse ayant de bonnes chances d'être
  acceptée selon l'ancienneté de l'annonce, les favoris et les baisses de prix, sans dépasser le prix
  qui garde ton bénéfice) + message au vendeur. Le robot remplit et envoie l'offre.
- **Acheter** : le robot ouvre l'annonce, clique sur « Acheter » et s'arrête sur la page de paiement :
  une notification te permet de vérifier et valider (ou paiement automatique si tu l'autorises).
- **Modes** : Arrêt / Offres auto / Achat auto, avec règles (score, bénéfice, confiance, marques,
  authenticité Gemini) et budgets (€ par article, € par jour, achats et offres par jour), pause 1 h / 24 h.
- **Journal** : chaque action, son résultat et, en cas d'échec, un diagnostic (boutons visibles sur
  la page) pour adapter le robot si Vinted change son site.
- **Stock & bénéfices** : achats ajoutés automatiquement, statut (reçu, en vente, vendu), prix de
  revente, bénéfice réel, rentabilité et délai de revente.

**Sur PC** : le robot pilote une fenêtre Chrome dédiée (profil mémorisé dans le dossier
`vinted-robot-profile`). Onglet Robot → « Connexion Vinted » : la fenêtre s'ouvre, tu te connectes
une fois, c'est mémorisé. Les achats que tu lances toi-même s'arrêtent toujours sur la page de
paiement, dans cette fenêtre. Installation : `pip install playwright` puis `playwright install chromium`
(Google Chrome est utilisé s'il est installé).

⚠️ Les conditions de Vinted interdisent les robots depuis le 8 octobre 2026 : risque de suspension
du compte. Utilise de préférence un compte dédié aux achats et des budgets raisonnables.

## Marques

Liste déroulante de toutes les marques connues (≈ 640 intégrées + toutes celles vues dans les
annonces + celles que tu ajoutes). Les fautes sont corrigées (« ralf loren » → Ralph Lauren,
« tnf » → The North Face) ; une marque inconnue est vérifiée sur Vinted (orthographe des annonces
trouvées) puis ajoutée à la liste.

## Catégories

Vêtements, Chaussures & sneakers, Sacs & maroquinerie, Accessoires, Montres & bijoux.
Quand le site a une catégorie native connue (Leboncoin, eBay), elle est utilisée ; sinon
l'annonce est filtrée sur les mots de son titre.

## Notifications externes

- **Discord** : définir `DISCORD_WEBHOOK_URL` avant de lancer.
- **Telegram** : définir `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID`.

## Diagnostic

`python probe.py nike > rapport.json` interroge chaque site et indique ce qui passe, et teste la
détection des ventes Vinted (section `vinted_item_check`).

## Limites

Aucun de ces sites n'a d'API publique : le programme utilise les accès internes de leurs sites
web, qui peuvent changer sans prévenir. Le scraping n'est généralement pas autorisé par leurs
conditions d'utilisation : usage personnel, à fréquence raisonnable, à tes risques.
