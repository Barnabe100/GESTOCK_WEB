# ADR-0009 — Internationalisation (react-i18next) et terminologie par profil

- **Statut** : Acceptée (décision TechNova du 2026-09-23)
- **Date** : 2026-09-23

## Contexte

Langue initiale : français. D'autres langues viendront. Chaque secteur a son vocabulaire
(« Article », « Produit », « Plat »…), sans conditions `if (businessType === …)`.

## Décision

- **react-i18next** ; ressources par langue dans `frontend/src/core/i18n/locales/<lng>/`,
  trois espaces de noms :
  - `common` : textes de l'interface ;
  - `errors` : messages des **codes d'erreur** renvoyés par l'API (Problem Details) ;
  - `terminology` : vocabulaire métier par défaut.
- Aucun texte d'interface en dur dans les composants.
- **Terminologie** : chaque profil d'activité fournit (fichier TOML du catalogue) des
  surcharges par langue, renvoyées dans `/me/capabilities` :
  `[terminology.fr.catalog] item = "Produit"`. Le frontend les applique à l'espace
  `terminology` (après réinitialisation aux valeurs par défaut). Les autres textes y font
  référence par imbrication i18next : `"modules.catalog": "$t(terminology:catalog.items)"`.
- Le backend renvoie des **codes** d'erreur stables ; la traduction est faite côté client.
- Langue et terminologie restent **distinctes des règles métier**.

## Conséquences

- Ajouter une langue = ajouter un dossier de ressources (+ la terminologie des profils).
- Ajouter un secteur = ajouter ses surcharges dans son fichier de profil.
- Un test vérifie que chaque entrée de menu, module et permission affichés est traduite.
