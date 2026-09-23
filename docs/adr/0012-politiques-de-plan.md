# ADR-0012 — Plan : limites, modules, fonctionnalités, politiques

- **Statut** : Acceptée (demande TechNova du 2026-09-23)
- **Date** : 2026-09-23

## Contexte

TechNova doit pouvoir faire évoluer ses offres (STANDARD, ENTREPRISE, d'autres demain) sans
modifier le cœur applicatif. En Phase 1, les limites `max_sites`/`max_users` étaient
contrôlées par deux services qui lisaient directement les valeurs du plan : un début de
dispersion des règles commerciales dans le code métier.

## Décision

Un plan est une **donnée** (`catalog/data/plans.toml`) structurée ainsi :

```text
Plan
 ├── limites            [limits]      max_sites = 1, max_users = 5 (absente = illimitée)
 ├── modules            modules = […] (les modules core sont toujours inclus)
 ├── fonctionnalités    features = […] options activables (ex. « stock.transfers »)
 └── politiques         grace_days + politique d'accès par statut (ADR-0011)
```

- **Les modules déclarent ce qui est paramétrable** dans leur manifeste :
  - `LimitDef(code, counter)` : une limite et la façon de compter l'usage (ex. le module
    `organization` déclare `max_sites` = nombre de sites actifs) ;
  - `features` : des fonctionnalités optionnelles, préfixées par le code du module.
- **Le catalogue valide** qu'un plan ne référence que des limites, modules et fonctionnalités
  déclarés (une fonctionnalité exige que son module soit inclus dans le plan).
- **Un seul point d'application** : `PlanPolicy`
  (`app/platform/subscriptions/plan_policy.py`). Le code métier appelle
  `ensure_capacity("max_sites")` ou `require_feature("stock.transfers")` ; il ne contient
  aucune valeur d'offre.
- Les capacités (`/me/capabilities`) exposent `features` et `limits` (plafond + usage) ;
  l'interface peut donc adapter ses écrans (sans valeur de sécurité).

## Conséquences

- Nouvelle offre ou nouvelle limite d'une offre : modifier `plans.toml` puis
  `stockmanager catalog sync` (plus tard : console d'administration).
- Nouvelle limite (ex. `max_articles`) : le module concerné la déclare dans son manifeste ;
  elle devient paramétrable par plan sans autre changement.
- Valeurs validées le 2026-09-23 : STANDARD = 1 site, 5 utilisateurs, sans `restaurant.qr` ;
  ENTREPRISE = sans limite.
