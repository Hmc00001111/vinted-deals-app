# Vinted Deals

Surveille en continu les nouvelles annonces de Vinted, Vestiaire Collective, Grailed, Sellpy, Jaiio, Leboncoin, Depop, eBay et StockX et fait remonter les bonnes affaires
(prix sous le marché, articles rares) selon tes filtres : sites, marque, mots-clés, prix, état, taille,
catégories, fréquence de scan — puis Gemini analyse les photos des meilleures annonces.

- `vinted_deals/` : le programme Python (version PC) — voir `vinted_deals/README.md`.
- `android/` : l'application Android. Elle embarque **le même code Python** (via Chaquopy),
  le fait tourner dans un service en arrière-plan et envoie une notification par bonne affaire.

## Récupérer l'APK

À chaque modification poussée sur `main`, GitHub Actions compile l'APK
(onglet **Actions**) et la publie dans la release **latest** (onglet **Releases**).

## Installer sur le téléphone

1. Télécharge `VintedDeals.apk` sur le téléphone et ouvre-le.
2. Autorise l'installation depuis cette source si Android le demande
   (Play Protect peut afficher un avertissement : « Installer quand même »).
3. Au premier lancement, accepte les notifications et l'exception d'optimisation de batterie
   (sinon Android peut endormir la surveillance).

Une notification permanente « Vinted Deals surveille les annonces » indique que la
surveillance tourne ; son bouton **Arrêter** coupe tout. Toucher une notification de bonne
affaire ouvre l'annonce dans l'appli Vinted.

## Compiler soi-même

Ouvrir le dossier `android/` dans Android Studio (Python 3.12 doit être installé sur le PC),
puis *Build > Build APK(s)*.
