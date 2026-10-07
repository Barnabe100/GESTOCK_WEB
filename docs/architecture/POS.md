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
  caisse : session de l'utilisateur indiquée, poste à choisir s'il en a plusieurs. Résumé
  indicatif (palier POS, [ADR-0047](../adr/0047-recu-de-vente-pos.md)) : Total, Montant reçu,
  Reste dû, **« Monnaie rendue »** — jamais un reste dû et une monnaie rendue positifs ensemble.
- **Confirmation** : récapitulatif (même résumé), avertissement de créance ; une erreur ne vaut
  que pour son panier (effacée à toute modification du panier, du client ou des paiements, et à
  la réussite) ; après validation, résultat du serveur (montant reçu, reste dû, monnaie rendue)
  et actions **« Voir le reçu »** / **« Imprimer »** (reçu 80 mm relu depuis la vente
  persistée, jamais le panier).
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

Restauration (tables, cuisine, QR), garage, fidélité, remboursements, reçu PDF et formats
d'impression autres que le ticket 80 mm (58 mm, A4 : prévus, non implémentés), création rapide
de client, remises, mode hors ligne et synchronisation, intégrations
Mobile Money / TPE / banques, reporting, profils métier spécialisés.

## Lot 3-A — articles non gérés en stock et scan exact (ADR-0039)

Un article `stock_managed = false` (service) apparaît dans la recherche (« Non géré en stock »,
sans quantité) et se vend sans mouvement ni contrôle de stock : `SaleService` relit le drapeau
sous verrou partagé de l'article à la validation. Le scan n'utilise jamais un résultat de la
recherche textuelle (différée de 250 ms) : la valeur saisie est envoyée telle quelle au serveur.

## Lot 3-B — unité de base, quantités décimales et conditionnements (ADR-0040)

- **Recherche** : chaque article indique sa règle de quantité (`decimal_quantity_allowed`) et
  ses conditionnements **actifs au prix configuré** (nom, conversion, prix) ; un conditionnement
  désactivé ou au prix non configuré n'est pas proposé (le serveur refuse aussi l'encaissement :
  `packaging_price_not_set`). La tuile affiche le prix de l'unité de base (« 500 F / pièce ») et le nombre de
  conditionnements.
- **Panier** : une ligne par présentation (article en unité de base, ou article ×
  conditionnement) ; un clic sur la tuile ou un scan ajoute l'unité de base (toujours
  disponible), le choix « Présentation » de la ligne bascule vers un conditionnement (prix,
  quantité de base — « Soit 48 pièce » —, total et disponibilité recalculés ; deux lignes de
  même présentation fusionnent). Quantités entières seulement pour un article sans décimales
  (saisie signalée, validation désactivée) ; disponibilité indicative en unité de base, toutes
  présentations de l'article confondues. Le panier ne réserve aucun stock.
- **Encaissement** : `lines: [{article_id, packaging_id, quantity}]` ; le serveur relit
  l'article, le conditionnement (actif, de cet article), la conversion, le prix, la règle
  décimale et le stock, puis sort la **quantité de base** (`SaleService`, aucune logique propre
  au POS).
- **Reçu (80 mm)** : désignation puis présentation vendue et prix unitaire figé
  (« 2 Carton 24 × 10 500 F … 21 000 F » ; unité de base : « 3 pièce × 500 F … 1 500 F »).

## Lot 3-D — codes-barres multiples et codes des conditionnements (ADR-0042)

- **Scan** : la valeur saisie (lecteur clavier : code puis Entrée) est résolue EXACTEMENT par le
  serveur parmi tous les codes des présentations actives — code principal ou supplémentaire de
  l'article (unité de base), code d'un conditionnement. Jamais de recherche partielle ni de
  résultat affiché auparavant ; inconnu : « Code-barres inconnu ».
- **Code d'un conditionnement** : `scanned_packaging_id` ; le panier ajoute **1 conditionnement**
  (« Quantité de Coca (Carton 24) » = 1, un second scan passe à 2) — jamais N unités de base ; la
  conversion reste celle du Lot 3-B à l'encaissement.
- **Prix non configuré** : `422 packaging_price_not_set` (règle existante), message explicite,
  rien n'est ajouté.
- **Recherche** : la recherche « contient » des tuiles trouve aussi les codes supplémentaires et
  ceux des conditionnements.


## Recette, étape 1 — assortiment par site (ADR-0046)

- **Recherche** : la caisse ne propose que l'**assortiment ACTIF** du site de vente
  ([ADR-0046](../adr/0046-assortiment-par-site.md)) ; un article hors assortiment n'est jamais
  proposé, même s'il reste du stock sur le site (stock recréé par une annulation après un
  retrait). Site sans assortiment : « Aucun article dans l'assortiment de ce site ».
- **Scan** : un article connu mais hors assortiment est refusé
  (`422 article_not_in_site_assortment`, message traduit) ; rien n'est ajouté au panier. Code
  inconnu : `404 barcode_unknown` (inchangé).
- **Encaissement** : `SaleService.checkout` revérifie l'assortiment sous verrou partagé pour
  toutes les lignes (article non géré en stock compris) ; refus total, rien n'est enregistré.
- **Lots disponibles** (`/pos/articles/{id}/lots`) : lecture conservée hors assortiment (la
  consultation n'ouvre aucune opération).
