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

## Catégories

Vêtements, Chaussures & sneakers, Sacs & maroquinerie, Accessoires, Montres & bijoux.
Quand le site a une catégorie native connue (Leboncoin, eBay), elle est utilisée ; sinon
l'annonce est filtrée sur les mots de son titre.

## Notifications externes

- **Discord** : définir `DISCORD_WEBHOOK_URL` avant de lancer.
- **Telegram** : définir `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID`.

## Diagnostic

`python probe.py nike > rapport.json` interroge chaque site et indique ce qui passe.

## Limites

Aucun de ces sites n'a d'API publique : le programme utilise les accès internes de leurs sites
web, qui peuvent changer sans prévenir. Le scraping n'est généralement pas autorisé par leurs
conditions d'utilisation : usage personnel, à fréquence raisonnable, à tes risques.
