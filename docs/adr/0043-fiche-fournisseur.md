# ADR-0043 — Fiche fournisseur : réceptions, synthèse, articles et chronologie

- **Statut** : Acceptée et validée (Lot 3-E — consultation et exploitation des fournisseurs)
- **Date** : 2026-10-01
- **Prolonge** : [ADR-0038](0038-historique-ventes-exports.md) (chronologie d'audit),
  [ADR-0039](0039-catalogue-stock-gere-prix-couts.md) (coûts réservés à `cost_view`),
  [ADR-0041](0041-presentations-operations-de-stock.md) (coût par unité de base)

## Contexte

Le cœur « fournisseurs » existait déjà : fiche (un contact, statut, audit, SUP-01 à SUP-06) et
réception directe = entrée de stock `PURCHASE` à fournisseur obligatoire, qui alimente le stock
et le CMUP par `StockService`. Il manquait l'**exploitation** de ces données : aucune fiche
détail, aucun historique des réceptions d'un fournisseur, aucun récapitulatif, aucun filtre
fournisseur dans les écrans. Le Desktop (référence) n'apporte que la recherche des entrées par
nom du fournisseur.

## Décision

Lot de **consultation** : aucune écriture nouvelle, aucune modification de `StockService`, du
CMUP, des ventes, du POS, des transferts, des inventaires ni des conditionnements.

1. **Fiche fournisseur** `/suppliers/:id` : identité, statut, contact actuel (modèle inchangé :
   un seul contact — D1), observations, actions existantes (modifier, activer / désactiver).
   Aucun champ nouveau (pas d'IFU, de RCCM ni de conditions de paiement — D7).
2. **Réceptions du fournisseur** : liste paginée de TOUTES ses réceptions (brouillons et
   annulées comprises, statut affiché) par le filtre existant `GET /stock/entries?supplier_id=`
   (`stock.entry.view`, sites visibles, montants seulement avec `cost_view`).
3. **Agrégats calculés par le serveur** (module `stock`, propriétaire des entrées, lecture seule,
   `stock.entry.view`, sites visibles du membre — filtre de site facultatif) sur les seules
   réceptions `PURCHASE` **VALIDÉES** (D5 : brouillons et annulées exclus) :
   - `GET /stock/suppliers/{id}/summary` : nombre de réceptions validées, date de la dernière,
     total reçu (`received_total`) ;
   - `GET /stock/suppliers/{id}/articles` : articles ayant au moins une réception validée —
     nombre de réceptions, quantité reçue **en unité de base**, dernière réception (date, numéro),
     **dernier coût** (`last_unit_cost`) = coût par unité de base de la dernière réception
     validée (date d'opération, puis date de validation ; plusieurs lignes du même article dans
     cette réception, une par présentation : moyenne pondérée par les quantités de base).
   Le dernier coût est une information **historique** : le prix d'achat de référence du
   catalogue n'est jamais mis à jour par une réception (D2) et `catalog.article.price_update`
   n'est jamais contourné.
4. **Fournisseur principal** : articles dont il est `main_supplier` (filtre existant
   `GET /catalog/articles?supplier_id=`, `catalog.article.view`). Un article peut figurer dans
   les deux vues (D3).
5. **Chronologie** `GET /suppliers/{id}/history` : `audit.log.view` ET `suppliers.supplier.view`,
   évènements réellement journalisés de l'entité (`supplier.created`, `updated`, `activated`,
   `deactivated`), du plus ancien au plus récent — aucun évènement créé pour l'alimenter (D8).
6. **Recherche et filtres** : la recherche des entrées porte aussi sur le **nom du fournisseur**
   (sous-requête `suppliers.api.suppliers_named`, sans import des modèles du module
   `suppliers`) ; filtre fournisseur dans les écrans Entrées et Articles (fournisseurs actifs
   et inactifs proposés).
7. **Coûts** (D4) : `received_total` et `last_unit_cost` déclarés dans `STOCK_COST_FIELDS` —
   retirés de toute réponse sans `catalog.article.cost_view` ; le tri n'est jamais proposé sur
   un coût. L'interface ne fait que masquer pour l'ergonomie.
8. **Permissions** : aucune nouvelle ; pas d'export (D6) ; pas d'indicateur au tableau de bord
   (D9).
9. **Index** `stock_entries (tenant_id, supplier_id)` (migration 0032) : toutes les requêtes de
   la fiche filtrent les entrées par fournisseur ; sans lui, chaque consultation parcourt toutes
   les entrées du tenant, dont le nombre croît sans limite. Aucune table nouvelle (RLS et droits
   inchangés).

## Hors périmètre

Contacts multiples, commandes, factures, paiements, dettes et retours fournisseurs, conditions
de paiement, exports, lots, péremption, images, mise à jour automatique du prix d'achat.

## Conséquences

- Le module `stock` lit les fournisseurs uniquement par `suppliers.api` (`get_supplier_ref`,
  `supplier_names`, `suppliers_named`) ; un fournisseur inconnu ou d'une autre entreprise donne
  `404 supplier_not_found` sur la synthèse, les articles et la chronologie.
- Les agrégats suivent la portée des sites : un membre limité à un site ne voit ni les
  réceptions, ni les quantités, ni les coûts des autres sites.
- **Validation** (TechNova, 2026-10-01) : décisions D1 à D9 appliquées ; lot validé et clôturé
  sur le commit `77a3b56` (CI #46 verte).
