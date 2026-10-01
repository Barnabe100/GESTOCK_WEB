# Point de vente (POS) — Phase 3.0

Interface de vente rapide au-dessus des services existants. Décisions :
[ADR-0023](../adr/0023-point-de-vente.md) ; caisse : [ADR-0022](../adr/0022-caisse.md) (révisée).

## 1. Architecture

```text
POS (frontend)
 ├─ GET  /pos/articles ─► stock.api.list_levels (site, recherche) + catalog.api (prix)
 └─ POST /pos/checkout ─► SaleService.checkout ─┬─► SaleService.create (prix du catalogue)
                                                └─► SaleService.validate
                                                     ├─► StockService (sortie, verrous)
                                                     ├─► limite de crédit (créances, verrou client)
                                                     └─► PaymentService (par paiement)
                                                          └─► port caisse ─► CashService (CASH seulement)
```

Aucune seconde logique de vente, de stock, de paiement, de crédit ou de caisse. Une seule
transaction : tout ou rien.

**Règle caisse** (Lot 1, [ADR-0037](../adr/0037-encaissement.md)) : une vente ne dépend pas
de la caisse, **optionnelle par site**. Site sans caisse (ou module Caisse inactif) : tous les
moyens, espèces comprises, sans session. Site avec caisse : un paiement espèces exige la
session **de l'utilisateur** sur ce site (`cash_session_required`, ou `cash_register_required`
s'il a plusieurs postes ouverts sans choix). Les autres moyens n'en dépendent jamais.

**Encaissement** (Lot 1) : moyens **configurés** de l'entreprise disponibles sur le site ;
espèces : montant **reçu**, monnaie calculée par le serveur sur la seule partie espèces
(affichée sur le reçu) ; référence exigée si le moyen l'impose. Reste dû ⇒ vente à crédit :
client obligatoire (F4), permission `sales.sale.credit_create`, limite de crédit (dépassement
seulement avec `sales.sale.credit_override` et une justification, proposé si le serveur le
permet : `credit_override: {reason}`). Numéro `VENT-{SITE}-{ANNÉE}-{SÉQUENCE}` attribué à la
validation, affiché sur le reçu.

## 2. API

| Méthode | Chemin | Permissions |
|---|---|---|
| GET | `/pos/articles?site_id&search&limit` (≤ 50) | `pos.terminal.use` |
| POST | `/pos/checkout` `{site_id?, customer_id?, sale_date?, notes?, lines: [{article_id, quantity}], payments: [{payment_method_id, amount?, amount_received?, reference?, cash_register_id?}], credit_override?: {reason}, idempotency_key}` | `pos.terminal.use` + `sales.sale.create` + `sales.sale.validate` (+ `sales.payment.create` si paiement) |

Réponse du checkout : `{sale (avec lignes, payé, reste, état), payments, replayed}` ; `201`,
ou `200` si la clé a déjà été traitée. Erreurs : celles des ventes, paiements, créances et de
la caisse (`insufficient_stock`, `article_inactive`, `customer_inactive`,
`credit_limit_exceeded`, `payment_exceeds_balance`, `cash_session_required`,
`cash_register_required`, `site_access_denied`, `site_mismatch`…), toutes traduites.

## 3. Écran

- **En-tête** : vendeur, site (choisi s'il n'est pas sélectionné), état de la caisse du site,
  ventes récentes (`GET /sales?channel=POS&site_id`).
- **Catalogue** : recherche serveur (référence, nom, code-barres ; 24 résultats), tuiles tactiles
  (nom, référence, prix, stock indicatif, rupture, inactif non ajoutable).
- **Panier** : un article une seule fois, quantité (+ / − / saisie), suppression, sous-total et
  total **indicatifs** (décimal exact), client (F4), paiements (F8), validation (F10).
- **Paiements** : aucun, partiel ou plusieurs moyens configurés ; reste proposé ; surpaiement
  hors espèces signalé ; espèces : montant reçu (monnaie calculée par le serveur) ; site avec
  caisse : session de l'utilisateur indiquée, poste à choisir s'il en a plusieurs.
- **Confirmation** : récapitulatif, avertissement de créance ; après validation, reçu affiché
  à partir de la réponse du serveur (point d'extension du futur ticket).
- **Raccourcis** : F2 recherche, Entrée = **scan** (Lot 3-A, ADR-0039 : correspondance EXACTE du
  code-barres demandée au serveur avec la valeur saisie, `GET /pos/articles/by-barcode` ; code
  inconnu → « Code-barres inconnu », rien n'est ajouté ; jamais le premier résultat affiché), F4 client,
  F8 paiement, F10 validation, Échap ferme un dialogue (inactifs quand un dialogue est ouvert).
- **Mobile** (≤ 800 px) : onglets Articles / Panier, grandes zones tactiles, aucun débordement
  horizontal (vérifié à 1440, 1024, 800, 390 px).

## 4. Sécurité

Chaîne habituelle : tenant (jeton, RLS), module `pos`, permissions cumulées, site (sélectionné
ou demandé et accessible, sinon `403`), abonnement (écriture bloquée si expiré), tenant
suspendu (`403`). Le frontend ne fait que masquer ; le serveur décide.

## 5. Hors périmètre (V1)

Restauration (tables, cuisine, QR), garage, fidélité, remboursements, impression / PDF du
ticket, création rapide de client, remises, mode hors ligne et synchronisation, intégrations
Mobile Money / TPE / banques, reporting, profils métier spécialisés.

## Lot 3-A — articles non gérés en stock et scan exact (ADR-0039)

Un article `stock_managed = false` (service) apparaît dans la recherche (« Non géré en stock »,
sans quantité) et se vend sans mouvement ni contrôle de stock : `SaleService` relit le drapeau
sous verrou partagé de l'article à la validation. Le scan n'utilise jamais un résultat de la
recherche textuelle (différée de 250 ms) : la valeur saisie est envoyée telle quelle au serveur.
