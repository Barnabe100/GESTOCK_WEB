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
| [0010](0010-authentification-et-tenant-actif.md) | Authentification, sessions et tenant actif | Acceptée |
| [0011](0011-politique-abonnement.md) | Plans, statuts d'abonnement et politique d'accès | Acceptée |
| [0012](0012-politiques-de-plan.md) | Plan : limites, modules, fonctionnalités, politiques | Acceptée |
| [0013](0013-roles-systeme-dynamiques.md) | Rôles système dynamiques | Acceptée |
| [0014](0014-documents-et-mouvements-de-stock.md) | Documents de stock, mouvements et état des niveaux | Proposée |
| [0015](0015-rbac-roles-de-base-et-personnalises.md) | RBAC : rôles de base, rôles personnalisés, portée et anti-escalade | Acceptée |
| [0016](0016-recherche-trigrammes.md) | Recherche « contient » servie par des index trigrammes (pg_trgm) | Acceptée |
| [0017](0017-ventes-prix-validation-annulation.md) | Ventes : prix du catalogue, validation et annulation | Acceptée |
| [0018](0018-transferts-inter-sites.md) | Transferts inter-sites : atomicité, CMUP, annulation, sites, fonctionnalité de plan | Acceptée |
| [0019](0019-inventaires.md) | Inventaires : écart sur le stock courant, ajustements via StockService, module `inventory_count` | Proposée |
| [0020](0020-paiements-des-ventes.md) | Paiements des ventes : module `sales`, solde calculé, verrou de la vente, clé d'idempotence | Proposée |
| [0021](0021-creances-comptes-clients.md) | Créances / comptes clients : calculées sans table, limite de crédit à la validation sous verrou du client | Acceptée |
| [0022](0022-caisse.md) | Caisse : caisse de site, sessions, mouvements append-only, encaissements espèces dans la transaction du paiement (révisée 3.0 : la vente ne dépend pas de la caisse) | Proposée |
| [0023](0023-point-de-vente.md) | Point de vente : interface au-dessus des services métier, encaissement en une étape idempotent | Proposée |
| [0024](0024-profils-activite-et-profils-ux.md) | Profils d'activité : secteurs, Business Profiles, profils UX (complète 0003 et 0009) | Proposée |
| [0025](0025-inscription-publique-et-activation.md) | Inscription publique, abonnement `pending_activation`, chaîne plan → abonnement → paiement → licence → activation | Acceptée |
| [0026](0026-onboarding-persistant.md) | Onboarding persistant : étapes déclarées par les modules, validation automatique, statuts qui n'avancent que, onboarding ≠ activation | Acceptée |
| [0027](0027-identite-documentaire.md) | Identité documentaire : le tenant source unique, en-tête (`DocumentIdentity`) construit par le serveur, jamais « N/A » | Acceptée |
| [0028](0028-fuseau-horaire-du-tenant.md) | Fuseau horaire du tenant obligatoire : référence du temps métier (dates, caisse, rapports, documents) | Acceptée |
| [0029](0029-identite-globale-et-appartenance.md) | Identité globale (`User`) ≠ appartenance au tenant (`TenantMembership`) : l'administrateur du tenant ne gère que l'appartenance | Acceptée |
| [0030](0030-delegation-rbac.md) | Délégation RBAC calculée par le serveur (`/permissions/delegable`, `/roles/delegable`), contrôle sur les permissions réellement accordées dans l'offre | Acceptée |
| [0031](0031-console-technova.md) | Console TechNova : processus et rôle SQL distincts, administrateurs `is_platform_admin` par CLI, journal de la plateforme append-only, catalogue technique en lecture seule, paramètres commerciaux en base ; complétée en 3.2-G (tenants et abonnements : métadonnées seulement, activation manuelle transitoire, double audit) | Acceptée |
| [0032](0032-paiements-abonnement.md) | Paiements d'abonnement (`SubscriptionPayment`) : déclaration idempotente par l'entreprise (permission `billing`, devise fixée par le serveur), décision définitive de TechNova (`CONFIRMED` / `REJECTED`, verrou, double audit), aucune activation | Acceptée |

Modèle : [`TEMPLATE.md`](TEMPLATE.md).
