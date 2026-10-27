# Vinted Deals

Surveille en continu les nouvelles annonces Vinted et fait remonter les bonnes affaires
(prix sous la valeur du marché, articles rares) selon tes filtres : marque, mots-clés,
prix, état, taille.

## Installation

```bash
cd vinted_deals
python -m venv .venv
# Windows : .venv\Scripts\activate    |    macOS/Linux : source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

L'interface s'ouvre sur http://127.0.0.1:5000

Options :

```bash
python app.py --interval 90          # secondes entre deux cycles (défaut 60)
python app.py --domain www.vinted.be # autre pays
python app.py --port 8000 --no-browser
```

## Comment ça marche

1. Tu crées une **surveillance** (mots-clés et/ou marques + filtres).
2. Toutes les ~60 s, le programme récupère les annonces les plus récentes
   (au premier passage, 3 pages pour amorcer l'historique).
3. Chaque annonce est notée sur 100 :
   - **Décote** par rapport au prix de référence : soit celui que tu saisis,
     soit la médiane des annonces vues sur 30 jours (10 % extrêmes retirés, 8 annonces minimum).
   - **Mots rares** dans le titre (vintage, deadstock, made in Italy, édition limitée…).
   - **État** (neuf avec étiquette = bonus, satisfaisant = malus).
   - **Favoris** déjà reçus.
4. Une annonce est une « bonne affaire » si score ≥ score min **et** décote ≥ décote min,
   et si elle respecte tes filtres. Les annonces hors filtres servent quand même à calculer
   le prix de marché (sinon un prix max biaiserait la référence vers le bas).
5. Prix suspect (> 80 % sous le marché) : signalé en orange — souvent une arnaque, un accessoire ou un lot.

## Notifications

- **Navigateur** : bouton « Activer les notifications » + petit bip.
- **Discord** : définir `DISCORD_WEBHOOK_URL` avant de lancer.
- **Telegram** : définir `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID`.

```bash
# Windows PowerShell
$env:DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python app.py
```

## Conseils

- Une surveillance précise (« Stone Island veste ») donne une référence de prix bien plus
  fiable qu'une recherche large (« veste »). Pour les articles très spécifiques, saisis
  toi-même le prix de référence.
- Ne descends pas l'intervalle sous ~45 s et limite-toi à une dizaine de surveillances :
  Vinted bloque temporairement (HTTP 429/403) les clients trop insistants. Le programme
  fait des pauses automatiques dans ce cas.
- Les données sont dans `vinted_deals.db` (SQLite). Supprime le fichier pour repartir de zéro.

## Limites

Vinted n'a pas d'API publique : le programme utilise l'API interne du site web
(`api.vinted.fr/svc-catalogue/items` depuis septembre 2026), qui peut changer sans prévenir.
En cas d'erreur, la dernière réponse brute est enregistrée dans `debug_last_response.json`
pour adapter `vinted_client.py`. Le scraping n'est pas autorisé
par les conditions d'utilisation de Vinted : usage personnel, à faible fréquence, à tes risques.
