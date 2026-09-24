# ADR-0017 — Ventes : prix du catalogue, validation et annulation

- **Statut** : Proposée
- **Date** : 2026-09-24

## Contexte

La Phase 2.4 introduit la vente comptant : brouillon, validation (sortie de stock), annulation.
Trois questions engagent les modules futurs (POS, remises, paiements, retours) :

1. D'où vient le prix d'une ligne, et qui calcule les montants ?
2. Que faire si le prix catalogue change entre l'enregistrement du brouillon et la validation ?
3. Comment annuler une vente déjà validée sans réécrire l'historique du stock ?

## Décision

1. **Prix = `catalog_articles.sale_price`, copié dans la ligne par le serveur** à chaque
   enregistrement du brouillon. L'API n'accepte ni prix ni total : un prix saisi serait une
   remise, fonctionnalité non encore conçue (droits, plafonds, audit). Le serveur calcule
   `line_total`, `subtotal`, `total` (`Decimal`, arrondi au centime demi supérieur). Les
   montants affichés pendant la saisie sont indicatifs.
2. **Validation refusée si un prix a changé** (409 `sale_prices_changed`, liste des
   références) : le total annoncé au client ne peut pas changer silencieusement ; un nouvel
   enregistrement relit les prix. Les montants d'une vente validée sont figés.
3. **Validation et annulation exclusivement via `StockService`** : mouvements `SALE` (−q) à
   la validation ; à l'annulation d'une vente validée, mouvements `CANCELLATION` (+q) au coût
   du mouvement d'origine (CMUP inchangé, règle STK-06), reliés par `origin_movement_id`.
   Un brouillon annulé n'a aucun effet sur le stock. Tout se fait dans une seule transaction,
   après verrouillage de la vente (idempotence de la validation).
4. **`stock_movements.source_number`** (nullable) : numéro lisible du document source, écrit
   par l'appelant. Le journal des mouvements l'affiche sans que le module stock connaisse les
   tables des ventes (dépendance à sens unique `sales → stock`).
5. **Annulation réservée par défaut à l'Administrateur** (`sales.sale.cancel` absent des
   modèles Gestionnaire et Vendeur) : elle modifie le stock après coup ; un rôle
   personnalisé peut l'accorder.

## Conséquences

- Aucune manipulation de prix possible depuis le navigateur ; le futur module Remises
  ajoutera des champs explicites (et des permissions) plutôt que de détourner le prix.
- Un changement de prix pendant une vente impose une action du vendeur (réenregistrer) :
  friction rare, acceptée pour l'exactitude.
- Le POS réutilisera `SaleService` (création + validation dans la même requête).
- Les anciens mouvements (entrées / sorties) ont `source_number` nul : le journal retombe
  sur le numéro du document de stock (`COALESCE`).

## Alternatives écartées

- **Prix envoyé par le client** : contournement trivial du tarif, remise non tracée.
- **Prix relu silencieusement à la validation** : le total validé pourrait différer du total
  annoncé au client.
- **Annulation par suppression ou modification des mouvements** : contraire au journal
  append-only (ADR-0004, ADR-0014).
- **Jointure du journal des mouvements vers `sales`** : dépendance du stock vers un module
  métier, cycle de modules.
