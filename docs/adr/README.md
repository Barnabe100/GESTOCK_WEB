# Architecture Decision Records (ADR)

Chaque décision structurante est consignée dans un fichier numéroté.
Une ADR acceptée n'est pas réécrite : on en crée une nouvelle qui la remplace.

Statuts : `Proposée` → `Acceptée` | `Rejetée` | `Remplacée par ADR-XXXX`.

| N° | Titre | Statut |
|---|---|---|
| [0001](0001-monolithe-modulaire.md) | Monolithe modulaire FastAPI + SPA React | Proposée |
| [0002](0002-strategie-multi-tenant.md) | Stratégie multi-tenant : base partagée, `tenant_id`, RLS | Proposée |
| [0003](0003-resolution-des-capacites.md) | Résolution des capacités (profil, plan, modules, permissions) | Proposée |
| [0004](0004-stock-service-central.md) | Stock : service central et journal de mouvements | Proposée |
| [0005](0005-versions-frontend.md) | Versions frontend : PrimeReact 10 (MIT), TypeScript 6.0 | Proposée |

Modèle : [`TEMPLATE.md`](TEMPLATE.md).
