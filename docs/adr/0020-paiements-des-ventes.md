# ADR-0020 — Paiements des ventes : dans le module `sales`, solde calculé, verrou de la vente, clé d'idempotence

- **Statut** : Proposée (en attente de validation TechNova)
- **Date** : 2026-09-24

## Contexte

Règle validée : la validation d'une vente est indépendante de son encaissement (payée,
partiellement payée, non payée, future créance). La Phase 2.7 ajoute les paiements (partiels,
successifs, mixtes), leur annulation, sans caisse ni créances. Un module `payments` était
déclaré `planned` (dépendant de `sales`) depuis la Phase 1.

## Décision

1. **Les paiements des ventes font partie du module `sales`** (comme les transferts dans
   `stock`) : permissions `sales.payment.{view,create,cancel}`, routes
   `/api/v1/sales/{sale_id}/payments`. Raisons : la vente doit connaître ses paiements (état
   d'encaissement sur la fiche et la liste, refus d'annuler une vente encaissée) ; un module
   séparé dépendant de `sales` aurait imposé une dépendance circulaire ou un mécanisme
   d'extension disproportionné. Le module planifié `payments` est conservé pour les **paiements
   électroniques** futurs (intégrations Mobile Money, TPE, banques ; libellé « Paiements
   électroniques ») ; ses dépendants planifiés (caisse, POS) restent inchangés.
2. **Aucun état stocké** : payé = somme des paiements `COMPLETED`, reste = total − payé, état
   calculé (`UNPAID` / `PARTIALLY_PAID` / `PAID`) par agrégation SQL, jamais matérialisé sur
   `sales` (une seule source de vérité, aucune désynchronisation possible). Liste des ventes :
   une agrégation pour la page et un filtre `payment_status`.
3. **Pas de surpaiement** ; solde recalculé par le serveur **sous le verrou de la vente**
   (`SELECT … FOR UPDATE`) : paiements concurrents et annulation de la vente sérialisés. Les
   paiements `PENDING` (futurs) comptent dans le solde engagé.
4. **Immutabilité** : pas de modification ni de suppression d'un paiement ; correction par
   annulation motivée (`COMPLETED → CANCELLED`) puis nouveau paiement. Annuler un paiement ne
   touche ni la vente (toujours `VALIDATED`) ni le stock. Annuler une vente encaissée est
   refusé (`sale_has_payments`) tant que ses paiements ne sont pas annulés.
5. **Cohérence tenant / site en base** : FK composite `(tenant_id, sale_id, site_id)` →
   `sales (tenant_id, id, site_id)` (nouvelle contrainte d'unicité), `site_id` copié pour la
   future caisse.
6. **Double soumission** : aucune infrastructure d'idempotence n'existait ; ajout d'une clé
   **facultative** par paiement (`idempotency_key`, unique par tenant), générée par l'interface
   à l'ouverture du formulaire. Même clé et même paiement → réponse rejouée (`200`) ; même clé
   pour un autre paiement → `409`. Proportionné (une colonne, une contrainte) et réutilisable
   par la caisse et le POS hors ligne.
7. **Moyens de paiement** : catégories techniques stables (`CASH`, `MOBILE_MONEY`, `CARD`,
   `BANK_TRANSFER`, `OTHER`) + champ libre `provider` pour l'opérateur ; un nouvel opérateur ne
   modifie pas le modèle. `PENDING` réservé aux confirmations asynchrones.
8. **Rôles de base** : Vendeur et Gestionnaire consultent et encaissent ; l'annulation d'un
   paiement suit la convention des autres annulations (Administrateur par défaut, attribuable à
   un rôle personnalisé) ; Consultant : consultation.
9. Pas de date d'encaissement saisie en V1 (`paid_at` = heure du serveur) ; pas de mouvement de
   caisse (module Caisse non créé).

## Conséquences

- Migration `0010` : table `payments` (RLS, droits sans `DELETE`) et unicité
  `(tenant_id, id, site_id)` sur `sales`.
- `SaleOut` expose `paid_amount`, `remaining_amount`, `payment_status` (vente validée).
- L'annulation d'une vente validée exige désormais qu'elle n'ait aucun paiement actif.

## Alternatives écartées

- **Module `payments` distinct** : dépendance circulaire `sales` ↔ `payments` pour l'état
  d'encaissement et le refus d'annulation ; API parallèle hors de la vente.
- **Colonnes `paid_amount` / `payment_status` sur `sales`** : seconde source de vérité à
  maintenir à chaque paiement et annulation (risque d'incohérence) ; l'agrégation suffit.
- **Refuser les doublons sans clé** (même montant dans un délai) : faux positifs sur des
  paiements légitimes identiques (paiements successifs du même montant).
- **Modifier un paiement** : perte de traçabilité ; l'annulation motivée conserve l'historique.
