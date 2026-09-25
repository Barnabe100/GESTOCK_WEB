# Profils d'activité et profils UX — Phase 3.1

Mécanisme qui adapte StockManager au métier du client **sans** application ni Core par
secteur. Décision : [ADR-0024](../adr/0024-profils-activite-et-profils-ux.md) (complète
l'[ADR-0003](../adr/0003-resolution-des-capacites.md) et l'[ADR-0009](../adr/0009-i18n-et-terminologie.md)).

```text
ONE CORE + SECTEURS + BUSINESS PROFILES + MODULES + UX PROFILES + PLANS + PERMISSIONS
```

## 1. Concepts

| Concept | Rôle | Où | Exemple |
|---|---|---|---|
| **Secteur** | Classification (grande catégorie) ; aucune règle métier | `catalog/data/sectors.toml` → `business_sectors` | `retail`, `restaurant`, `automobile`, `distribution` |
| **Business Profile** | Activité précise d'un secteur : modules proposés, profil UX, surcharges | `catalog/data/profiles/<secteur>/<activité>.toml` → `business_profiles` | `retail.alimentation`, `restaurant.maquis` |
| **Profil UX** | Présentation d'un métier : navigation, tableau de bord, terminologie, thème, modules par défaut | `catalog/data/ux_profiles/<code>.toml` → `ux_profiles` | `retail.default`, `restaurant.default` |
| **Module** | Fonctionnalité technique (manifeste, permissions, routes) ; statut `available` / `planned` | `app/modules/`, `app/modules/planned.py` | `pos`, `restaurant.tables` (planifié) |
| **Plan** | Ce que le client a acheté : modules, fonctionnalités, limites | `plans.toml` | `STANDARD`, `ENTREPRISE` |
| **Abonnement** | Statut (actif, expiré, suspendu…) et politique d'accès par nature de permission | `subscription_policies.toml` | expiré ⇒ lecture seule |
| **Permission** | Droit d'un utilisateur (rôles RBAC) | manifestes | `pos.terminal.use` |
| **Capacité** | Résultat final pour un utilisateur, un tenant, un site | `CapabilityService`, `/me/capabilities` | — |
| **Site** | Lieu d'exploitation du tenant (boutique, dépôt, salle) ; **jamais** un secteur ni un profil | `sites` | `PRINCIPAL`, `DEPOT` |

Relations : `Tenant → Business Profile → (Secteur, Profil UX)`. Le tenant conserve ses sites,
utilisateurs, rôles, activations de modules et son abonnement. Le profil appartient au
**tenant**, pas au site : il s'applique à tous les sites.

Codes techniques stables en anglais (`<secteur>.<activité>`, minuscules) ; libellés par i18n
(`sectors.<code>`, `businessProfiles.<secteur>.<activité>`), avec repli sur le nom du catalogue.

## 2. Hiérarchie livrée (28 profils)

| Secteur | Profils (profil UX) |
|---|---|
| `retail` (`retail.default`) | `alimentation`, `vetements`, `chaussures`, `cosmetique`, `electronique`, `librairie`, `quincaillerie`, `ameublement`, `maison_decoration`, `sport`, `specialise` (autres commerces) |
| `restaurant` (`restaurant.default`) | `restaurant`, `maquis`, `fast_food`, `cafe`, `bar`, `pizzeria`, `boulangerie`, `traiteur` |
| `automobile` (`automobile.default`) | `pieces_detachees`, `garage`, `moto`, `pneumatique`, `atelier` |
| `distribution` (`distribution.default`) | `grossiste`, `distributeur`, `entrepot`, `depot` |

Surcharges livrées (exemples du mécanisme) : `retail.alimentation` (navigation « caisse
rapide », accent vert), `automobile.pieces_detachees` (modules sans atelier),
`automobile.pneumatique` (terminologie « Pneu / Pneus »), `distribution.entrepot` (ni point de
vente ni caisse).

Codes antérieurs à la 3.1 (migration `0013`) : `alimentation` → `retail.alimentation`,
`quincaillerie` → `retail.quincaillerie`, `commerce_general` → `retail.specialise`,
`restaurant` → `restaurant.restaurant` ; anciens profils désactivés, jamais supprimés.

## 3. Définitions

```toml
# ux_profiles/restaurant.default.toml (extrait)
code = "restaurant.default"
modules = ["catalog", "stock", "sales", "pos", "cash_register", "…",
           "restaurant.menu", "restaurant.tables", "restaurant.orders", "restaurant.kitchen"]
optional_modules = ["restaurant.qr"]

[[navigation]]                      # rubriques, dans l'ordre ; libellé : navGroups.<group>
group = "restaurant"
modules = ["restaurant.tables", "restaurant.orders", "restaurant.kitchen", "restaurant.menu"]

[[navigation]]
group = "stock"
modules = ["catalog", "stock", "inventory_count", "alerts", "suppliers"]

[dashboard]                         # références <module>:<identifiant>
widgets = ["sales:today", "cash_register:open_sessions", "restaurant.tables:occupied", "…"]
shortcuts = ["pos:open", "sales:new", "stock:entry"]

[terminology.fr.catalog]
item = "Produit"
items = "Produits"

[theme]                             # palette contrôlée ; aucune couleur libre
accent = "orange"                   # blue | green | orange | teal | indigo
density = "comfortable"             # comfortable | compact
```

```toml
# profiles/restaurant/maquis.toml
code = "restaurant.maquis"
sector = "restaurant"
ux_profile = "restaurant.default"
name = "Maquis"
description = "Restauration et boissons en service rapide ou en salle."
sort_order = 20
# Surcharges facultatives : modules, optional_modules, [[navigation]] (remplace),
# [dashboard] (remplace), [terminology.<langue>] (fusion), [theme] (fusion).
```

Validation au chargement (`stockmanager catalog check`, et au démarrage des tests) : code
conforme au chemin, secteur et profil UX connus et actifs pour un profil actif, modules
connus et non core, dépendances proposées, rubriques uniques, un module par rubrique au plus,
références de widgets bien formées vers des modules connus, accent et densité de la palette.

## 4. Configuration par défaut et configuration effective

- **Par défaut** (`GET /business-profiles/{code}`) : ce que le profil propose = profil UX +
  surcharges du profil. Rubriques, widgets et modules planifiés compris.
- **Effective** (`GET /me/capabilities`) : ce que le tenant possède réellement.

```text
modules effectifs = core ∪ dépendances(profil ∩ plan ∩ activations du tenant)
permissions       = rôles actifs (tenant + site) ∩ permissions des modules effectifs
                    (∩ fonctionnalités du plan), puis politique de l'abonnement
expérience        = configuration par défaut restreinte aux modules effectifs ET implémentés
                    (rubriques vides retirées ; widgets/raccourcis de modules absents retirés)
à venir           = modules planifiés du profil (information, jamais un lien)
```

Jamais `modules par défaut == modules actifs` : un profil restaurant au plan STANDARD n'a pas
`restaurant.qr` ; un module désactivé par le tenant disparaît du menu et du tableau de bord.
Le frontend filtre encore chaque entrée, widget et raccourci par **permission** et
**fonctionnalité de plan** (ergonomie) ; le backend applique indépendamment les mêmes règles
sur chaque requête (sécurité).

## 5. Navigation

Registre de navigation = entrées déclarées par chaque module frontend (`FrontendModule.navigation` :
identifiant, libellé i18n, icône, route, permission, fonctionnalité, rubrique par défaut).
`buildNavigationSections(registre, capacités)` :

1. rubriques et ordre du profil UX (`caps.ux.navigation`, déjà restreintes aux modules
   effectifs et implémentés) ;
2. entrées de chaque module listé, dans l'ordre du module, filtrées par permission et
   fonctionnalité ;
3. une entrée autorisée non placée par le profil rejoint sa rubrique par défaut (rien n'est
   perdu) ; rubriques vides omises ; titres de rubrique textuels (`aria-labelledby`).

## 6. Tableau de bord

Registre de widgets (`modules/dashboard/widgets.tsx`) : identifiant `<module>:<id>`, type
(indicateur / panneau), permission, fonctionnalité, composant. Widgets disponibles :
`alerts:out_of_stock`, `alerts:low_stock`, `sales:today`, `sales:drafts`,
`stock:draft_transfers`, `receivables:outstanding`, `cash_register:open_sessions`,
`sales:recent` ; raccourcis : `pos:open`, `sales:new`, `stock:entry`, `stock:exit`,
`stock:transfer`. Le profil UX choisit lesquels et dans quel ordre. Un widget déclaré pour un
module futur (`restaurant.tables:occupied`, `restaurant.kitchen:ready`,
`automobile.workshop:open_orders`) n'est jamais affiché tant que le module n'existe pas.
Les modules planifiés du profil apparaissent dans « À venir pour votre activité », avec le
badge « Bientôt disponible », sans lien.

## 7. Terminologie et thème

- Terminologie : profil UX puis surcharges du profil (fusion), appliquée à l'espace i18n
  `terminology` ; les textes y renvoient par imbrication (`$t(terminology:catalog.items)` :
  menu, modules, titres, indicateurs). Les termes techniques restent stables (« Vente » reste
  « Vente » en restauration tant qu'aucun module de commandes n'existe).
- Thème : `data-accent` et `data-density` sur la coquille ; jetons `--sm-accent*`
  (logo, entrée active) et densité (`--sm-control-padding-*`, `--sm-cell-padding`) —
  [`DESIGN_SYSTEM.md`](DESIGN_SYSTEM.md). Aucun composant ni bibliothèque supplémentaire.

## 8. Restaurant, profil de référence

`restaurant.restaurant` déclare Salle/Tables, Commandes, Cuisine, Menu, Recettes, QR
(modules `restaurant.*`, **planifiés**), un tableau de bord orienté service (ventes du jour,
caisses ouvertes, créances ; tables occupées, commandes en préparation et prêtes déclarées
pour plus tard), la terminologie « Produit » et l'accent orange. Aujourd'hui, un restaurant
obtient : ventes et caisse en tête du menu, produits rangés dans « Stock », tableau de bord
orienté encaissement, vocabulaire et identité propres ; Tables, Cuisine… sont annoncées « à
venir » et **aucune** route ne leur répond (`/restaurant/tables` : page introuvable ; API :
404). Livrer un module restaurant = passer son manifeste en `available` + son module frontend :
la navigation, le tableau de bord et les capacités l'afficheront sans autre changement.

## 9. Changement de profil

`PUT /api/v1/tenant/business-profile {code}` (permission `organization.profile.manage`,
nature `admin`) ou `stockmanager change-profile --tenant-id … --profile …` (TechNova) :
profil actif requis ; **aucune donnée supprimée** ; refus `409 profile_change_incompatible`
si un module implémenté et activé n'est pas proposé par le nouveau profil (le désactiver
d'abord, ses données restent) ; modules proposés par défaut, inclus au plan et jamais
paramétrés, activés ; audité (`tenant.profile_changed`). Pas d'assistant de migration.

## 10. Création d'un tenant

`stockmanager create-tenant … --business-profile retail.alimentation` (alias `--profile`) →
`TenantProvisioningService` : profil actif d'un secteur actif, modules proposés initialisés
dans les limites du plan, audit `tenant.provisioned` avec profil, secteur et profil UX.

## 11. Sécurité

Le profil est une configuration de présentation et d'offre ; **jamais** une frontière de
sécurité. Chaîne inchangée : authentification → appartenance au tenant → portée des sites →
RBAC → permission → module → plan → abonnement → RLS. Le profil vient du tenant du jeton (aucun
paramètre client) ; `tenants` est protégé par la RLS ; les tables du catalogue sont en lecture
seule pour le rôle applicatif. Aucun code ne compare un code de profil ou de secteur (tests
statiques backend et frontend).

## 12. Ajouter un profil (ex. `retail.librairie`)

1. `backend/app/platform/catalog/data/profiles/retail/librairie.toml` (code, secteur, profil
   UX, nom, description, ordre ; surcharges éventuelles) ;
2. `businessProfiles.retail.librairie` dans `frontend/src/core/i18n/locales/<lng>/common.json` ;
3. `stockmanager catalog sync`.

Aucun changement de `SalesService`, `StockService`, `PaymentService`, `CashService`, du POS, de
la RLS ni du RBAC (test `test_new_profile_is_added_by_configuration_only`). Un nouveau
**secteur** = une entrée de `sectors.toml` + un profil UX + traductions ; un nouveau
**widget** ou une nouvelle **rubrique** = une entrée de registre frontend + traduction.

## 13. Reporting futur

Dimensions disponibles : tenant, site, utilisateur (saisie / validation), date, canal
(`sales.channel`), module ; profil et secteur via le tenant, leur historique via l'audit
(`tenant.provisioned`, `tenant.profile_changed`). Les tableaux de bord de reporting pourront
être déclarés par profil UX comme les widgets actuels.

## 14. Hors périmètre

Tables, salle, cuisine, commandes restaurant, QR, réservation, fidélité, atelier et
véhicules, pièces spécialisées, comptabilité, reporting avancé, intégrations, hors ligne,
assistant de migration de profil, mode sombre (non supporté par le Design System actuel).
