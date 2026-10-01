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
| [0033](0033-abonnement-par-site.md) | 1 site = 1 abonnement : `subscriptions.site_id` (abonnement d'inscription rattaché au premier site), capacités par site, écritures revérifiées pour le site ciblé, limites par site, console par abonnement | Acceptée |
| [0034](0034-licences-et-signing-service.md) | Licences des sites : Signing Service séparé (Ed25519, clé privée hors StockManager, HMAC), `.lic` v1 canonique, génération depuis un paiement confirmé, conditions figées, révocation définitive, réémission, trousseau public avec rotation | Acceptée |
| [0035](0035-postes-activations.md) | Postes : activations d'installations (identifiant aléatoire) sous la licence en vigueur, quota par site sous verrou, idempotence, refus distincts journalisés, libération (entreprise, TechNova) sans effet sur la licence | Acceptée |
| [0036](0036-renouvellement-et-notifications.md) | Renouvellement par site : devis serveur (période, postes, montant), continuité pendant la grâce, postes reconduits sauf demande explicite, offre en vigueur ≠ prochaine offre, tarif plan + postes figé ; rappels d'échéance idempotents (job CLI, lu / non lu par membre) | Acceptée |
| [0037](0037-encaissement.md) | Encaissement : moyens de paiement configurables (type = comportement, instantané sur le paiement, API future), caisse optionnelle par site (désactivation refusée si une session est ouverte), poste = machine, session = site + poste + utilisateur, monnaie calculée par le serveur sur la seule partie espèces, crédit avec client obligatoire (`sales.sale.credit_create`, dépassement justifié `sales.sale.credit_override`), numéro `VENT-{SITE}-{ANNÉE}-{SÉQUENCE}` à la validation, portée `sales.sale.view_all` | Acceptée |
| [0038](0038-historique-ventes-exports.md) | Historique des ventes : tri chronologique par défaut, filtres (vendeur, « Mes ventes », article, client, canal, référence article ≠ référence de paiement), UNE action « Exporter » (Excel, CSV, PDF A4) au périmètre exact de la liste, `sales.sale.export`, audit des exports, limite de crédit `customers.credit_limit.manage` auditée, fiche enrichie (mouvements, chronologie) | Acceptée |
| [0039](0039-catalogue-stock-gere-prix-couts.md) | Catalogue : scan exact du code-barres au POS (`404 barcode_unknown`), articles gérés ou non en stock (`stock_managed`, garde centrale `StockService`, géré → non géré à stock nul partout), historique des prix lu dans l'audit, `catalog.article.price_update` et `catalog.article.cost_view` (coûts absents des réponses sans elle) | Acceptée |
| [0040](0040-quantites-decimales-conditionnements.md) | Quantités décimales (`decimal_quantity_allowed`, entières par défaut, contrôle serveur) et conditionnements de vente (conversion > 0 figée dès qu'une vente l'utilise, prix propre, jamais supprimés) ; vente par présentation, quantité de base = quantité × conversion sans arrondi, stock toujours en unité de base, instantané figé sur la ligne | Acceptée |
| [0041](0041-presentations-operations-de-stock.md) | Conditionnements dans les opérations de stock : entrée, sortie, transfert et comptage en unité de base ou dans un conditionnement actif, quantité de base calculée par le serveur (stock toujours en unité de base), règle décimale étendue au stock, instantané sur les lignes et les mouvements, revalidation à la validation, conversion figée par tout usage (`catalog.usage_port`), équivalences affichées | Acceptée |
| [0042](0042-codes-barres-multiples.md) | Codes-barres multiples et codes des conditionnements : registre `catalog_barcodes` (code principal miroir du champ existant), un code = une présentation, unicité commune au tenant parmi les présentations actives (index unique partiel), éléments désactivés = codes libérés et revérifiés à la réactivation, scan exact vers la présentation (POS : 1 carton ; stock et inventaire : présélection sans quantité devinée), recherche étendue, audit, aucune permission nouvelle | Acceptée |
| [0043](0043-fiche-fournisseur.md) | Fiche fournisseur (consultation) : réceptions du fournisseur (toutes, statut affiché), synthèse et articles reçus calculés par le serveur sur les seules réceptions VALIDÉES des sites visibles (dernier coût par unité de base, historique ; prix d'achat de référence jamais modifié), fournisseur principal, chronologie d'audit réelle, recherche des entrées par nom du fournisseur, coûts absents sans `cost_view`, index `stock_entries (tenant_id, supplier_id)`, aucune permission nouvelle | Acceptée et validée |

Modèle : [`TEMPLATE.md`](TEMPLATE.md).
