# ADR-0011 — Plans, statuts d'abonnement et politique d'accès

- **Statut** : Proposée (mise en œuvre des décisions TechNova du 2026-09-23, à valider)
- **Date** : 2026-09-23

## Contexte

Offres STANDARD et ENTREPRISE, facturation mensuelle ou annuelle, sans licence perpétuelle.
Les fonctionnalités ne doivent pas être figées dans le code. L'expiration ne supprime jamais
de données ; la connexion et certaines fonctions (consultation, export, renouvellement)
restent possibles ; les règles de blocage sont centralisées et configurables.

## Décision

- **Plans = données** (`catalog/data/plans.toml` → tables `plans`, `plan_modules`) :
  modules inclus, limites (`max_sites`, `max_users` ; absente = illimitée), délai de grâce.
  Les valeurs actuelles sont **provisoires**.
- **Abonnement** (`subscriptions`, un par tenant) : plan, période (`monthly`/`annual`),
  statut stocké (`trial`, `active`, `past_due`, `expired`, `suspended`, `cancelled`),
  dates de période.
- **Statut effectif** calculé à la lecture (sans tâche planifiée) : fin de période
  dépassée → `past_due` pendant le délai de grâce du plan, puis `expired` ; un essai échu
  expire sans grâce.
- **Nature des permissions** : chaque permission déclare sa nature — `read`, `write`,
  `export`, `admin`, `billing`.
- **Politique centrale** (`subscription_policies.toml` → `subscription_access_policies`) :
  natures autorisées par statut. Actuellement : essai/actif/échéance dépassée = tout ;
  expiré = `read`, `export`, `billing` ; suspendu = `billing` ; résilié = `read`, `export`.
- La résolution des capacités retire les permissions non autorisées et les expose en
  `restricted_permissions` ; une action bloquée répond `403 subscription_restricted`.
- Aucune ligne n'est supprimée par un changement de statut ; le rôle SQL applicatif n'a
  d'ailleurs pas le droit `DELETE` sur les tenants ni les abonnements.

## Conséquences

- Modifier une offre ou une règle de blocage = modifier un fichier de catalogue puis
  `stockmanager catalog sync` (plus tard : console d'administration), sans code.
- Les futurs modules métier devront qualifier chaque permission (ex. `sales.sale.create` =
  `write`, `reports.sales.export` = `export`) : c'est ce qui les rend automatiquement
  soumis à la politique.
- Paiement et renouvellement en ligne : hors Phase 1.
