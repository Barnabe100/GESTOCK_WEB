# Architecture Decision Records (ADR)

Chaque décision structurante est consignée dans un fichier numéroté.
Une ADR acceptée n'est pas réécrite : on en crée une nouvelle qui la remplace.

Statuts : `Proposée` → `Acceptée` | `Rejetée` | `Remplacée par ADR-XXXX`.

| N° | Titre | Statut |
|---|---|---|
| [0001](0001-monolithe-modulaire.md) | Monolithe modulaire FastAPI + SPA React | Acceptée |
| [0002](0002-strategie-multi-tenant.md) | Stratégie multi-tenant : base partagée, `tenant_id`, RLS | Acceptée |
| [0003](0003-resolution-des-capacites.md) | Résolution des capacités (profil, plan, modules, permissions) | Acceptée |
| [0004](0004-stock-service-central.md) | Stock : service central et journal de mouvements | Acceptée |
| [0005](0005-versions-frontend.md) | Versions frontend : PrimeReact 10 (MIT), TypeScript 6.0 | Acceptée |
| [0006](0006-provisioning-des-tenants.md) | Provisioning des tenants : service dédié + CLI TechNova | Acceptée |
| [0007](0007-utilisateurs-memberships-mot-de-passe-provisoire.md) | Utilisateurs multi-tenants, TenantMembership, mot de passe provisoire | Acceptée |
| [0008](0008-sqlalchemy-synchrone.md) | SQLAlchemy 2 en mode synchrone | Acceptée |
| [0009](0009-i18n-et-terminologie.md) | Internationalisation (react-i18next) et terminologie par profil | Acceptée |
| [0010](0010-authentification-et-tenant-actif.md) | Authentification, sessions et tenant actif | Proposée |
| [0011](0011-politique-abonnement.md) | Plans, statuts d'abonnement et politique d'accès | Proposée |

Modèle : [`TEMPLATE.md`](TEMPLATE.md).
