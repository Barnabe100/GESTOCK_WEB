# ADR-0038 — Historique des ventes : filtres, exports multi-formats audités, limite de crédit et fiche enrichie

- **Statut** : Acceptée (Lot 2 — droits, historique et exports des ventes)
- **Date** : 2026-10-01

## Contexte

Après le Lot 1 (ADR-0037), l'historique des ventes restait limité : tri par défaut sur le
**numéro** (ordre alphabétique : `…-1000000` classé avant `…-999999`), filtres réduits
(numéro / client, statut, site, période, encaissement), aucun export, aucune vue des
mouvements de stock ni de l'historique d'une vente. La limite de crédit d'un client était
modifiable par quiconque pouvait modifier le client. Un brouillon pouvait encore conserver un
ancien numéro `VTE-…` à la validation (comportement de compatibilité devenu inutile : les
données `VTE-…` ne sont que des données de test, l'application n'étant pas en production).

## Décision

1. **Tri par défaut chronologique** : liste des ventes, ventes récentes du tableau de bord et
   du point de vente triées par `created_at` décroissant. Le tri par numéro reste une option
   explicite de la liste (ordre alphabétique du numéro).
2. **Numérotation** : un brouillon n'a jamais de numéro ; la validation attribue toujours
   `VENT-{SITE}-{ANNÉE}-{SÉQUENCE}` (aucune reprise d'un ancien numéro). La migration 0025 n'est
   pas modifiée.
3. **Filtres** de l'historique (un seul objet `SaleFilters`, lu de la même façon par la liste et
   par l'export) : vendeur / opérateur (`seller_id` = `sales.created_by`, l'utilisateur qui a
   enregistré la vente ; candidats calculés par le serveur parmi les auteurs des ventes
   visibles, `GET /sales/sellers`), « Mes ventes » (`mine`, utilisateur courant — jamais un nom
   de rôle), article (`article_id`), client, canal (`BACKOFFICE` / `POS`), et deux références
   **distinctes** : référence article (`article_reference` : référence ou code-barres d'un
   article vendu) et référence de paiement (`payment_reference` : n° de transaction, ex.
   Orange Money).
4. **Exports — UNE action « Exporter » par fonctionnalité**, qui ouvre le choix du format parmi
   ceux que la fonctionnalité déclare pertinents (ici Excel `.xlsx`, CSV `.csv`, PDF `.pdf`).
   Architecture commune (`app/platform/exports.py`) :
   `filtres → portée + permissions → requête commune → ExportTable → CSV | Excel | PDF`.
   La fonctionnalité sélectionne ses données **une seule fois** (même requête que sa liste :
   `SaleService.query`) ; le format ne change que la représentation, jamais le périmètre.
   - CSV : séparateur `;`, UTF-8 avec BOM, décimales à virgule, dates `jj/mm/aaaa` (Excel FR) ;
   - Excel : nombres et dates typés, en-tête figé, filtre automatique ;
   - PDF : rapport A4 paysage (titre, entreprise, date de génération, filtres appliqués,
     en-tête répété, pagination) — jamais le format des reçus 80 mm.
   Dates métier dans le fuseau du tenant (ADR-0028). Garde-fou technique : au-delà de
   `SM_EXPORT_MAX_ROWS` lignes (50 000 par défaut), refus `export_too_large` (affiner les
   filtres) plutôt qu'une génération partielle.
5. **Permission `sales.sale.export`** (nature `export`, filtrée par la politique d'abonnement) :
   Administrateur et Gestionnaire oui, Vendeur et Consultant non. Elle s'ajoute à
   `sales.sale.view` (exigée aussi) et ne remplace aucun contrôle : tenant (RLS), sites
   visibles, portée `view` / `view_all` site par site ; un site où le membre ne détient pas
   `sales.sale.export` n'est jamais exporté.
6. **Audit de chaque export** (`export.generated`, même transaction) : utilisateur, tenant et
   date portés par l'entrée ; données = fonctionnalité, format, filtres **réellement
   renseignés** (aucune valeur inventée), nombre de lignes. Un export refusé n'est pas tracé
   comme généré.
7. **Limite de crédit : permission dédiée `customers.credit_limit.manage`** (nature `admin`,
   Administrateur seul par défaut). Créer ou modifier un client ne l'accorde pas : fixer une
   limite à la création ou la changer est refusé (`credit_limit_not_allowed`) ; renvoyer la
   valeur inchangée reste accepté (formulaire). Toute modification est auditée
   (`customer.credit_limit_changed` : avant / après, utilisateur, date, tenant). Le registre
   impose le préfixe du module (`customers.`) : le code demandé `customer.credit_limit.manage`
   devient donc `customers.credit_limit.manage`, conforme à la convention
   `module.ressource.action`.
8. **Fiche enrichie** :
   - mouvements de stock générés par la vente : journal des mouvements filtré par document
     (`GET /stock/movements?source_type=sale&source_id=…`), visible seulement avec
     `stock.movement.view` et dans les sites du périmètre ;
   - chronologie : `GET /sales/{id}/history`, exige `audit.log.view` **et** une vente visible
     (portée de `sales.sale.view`) ; uniquement les évènements réellement journalisés de la
     vente et de ses paiements (`entity_history` de l'audit), du plus ancien au plus récent.

## Conséquences

- Aucune migration : permissions et rôles sont des données (registre, `role_templates.toml`) ;
  les rôles de base existants sont résolus à l'exécution.
- Toute nouvelle fonctionnalité exportable réutilise `ExportTable`, `check_format`,
  `export_response` et `audit_export`, et le composant `ExportMenu` côté interface.
- Les rôles personnalisés n'obtiennent l'export ou la gestion de la limite de crédit que si
  l'administrateur leur ajoute la permission.
- Non traité (lots ultérieurs) : prix appliqué différent du prix catalogue (`price_override`),
  promotions, taxes, retours / avoirs, exports d'autres fonctionnalités, rapports.
