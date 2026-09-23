# ADR-0003 — Résolution des capacités (profil, plan, modules, permissions)

- **Statut** : Proposée
- **Date** : 2026-09-23

## Contexte

L'interface et les fonctionnalités doivent varier selon le secteur, l'abonnement,
les modules activés et les droits de l'utilisateur, sans disperser des conditions
`if business_type == "restaurant"` dans le code.

## Décision

- Le code ne teste **jamais** un secteur ; il teste une **capacité** :
  un module actif (`require_module("restaurant.kitchen")`) ou une permission
  (`require_permission("restaurant.order.create")`).
- Les **modules** sont déclarés par des manifestes (code, dépendances, permissions,
  routeur) collectés dans un registre explicite.
- Les **profils d'activité** et les **plans** sont des **données** (configuration
  versionnée puis stockée en base) qui listent des modules, une terminologie,
  des préréglages et des limites.
- Un **service unique** calcule :
  `modules effectifs = profil ∩ plan ∩ activations du tenant` (dépendances comprises),
  puis `permissions effectives = permissions des rôles ∩ permissions des modules effectifs`.
- Le backend applique ces capacités sur chaque requête et les expose via
  `GET /api/v1/me/capabilities` ; le frontend construit routes et menu à partir de
  cette réponse et de son propre registre de modules.

## Conséquences

- Ajouter un secteur = ajouter un profil (+ éventuellement des modules), sans toucher au Core.
- Un appel forgé vers un module non souscrit est refusé côté serveur.
- Le calcul de capacités doit être mis en cache par requête (et invalidé lors
  d'un changement d'abonnement / de rôles).

## Alternatives écartées

- **Conditions par secteur dans le code** : non évolutif, explicitement exclu.
- **Navigation entièrement pilotée par le backend (JSON de menus/écrans)** : couple
  le backend à la présentation ; on garde un registre frontend typé, filtré par les
  capacités, avec l'ordre et la terminologie fournis par le profil.
