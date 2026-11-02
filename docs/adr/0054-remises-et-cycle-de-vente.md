# ADR-0054 — Remises sur les ventes et cycle de vente (palier R9)

- **Statut** : Proposée — remise globale livrée (migration 0061) ; remboursements et retours :
  décisions **à valider** (section « Hors périmètre et propositions »)
- **Date** : 2026-11-02
- **Prolonge** : ADR-0017 (ventes), ADR-0020 (paiements), ADR-0021 (créances), ADR-0022
  (caisse), ADR-0023 (point de vente), ADR-0037 (encaissement), ADR-0047 (reçu),
  [ADR-0049](0049-restauration-commandes.md) (D13, limite W1)

## Contexte

La mission « Finalisation » demande, pour le point de vente et les ventes : des **remises selon
les permissions**, le **cycle de vente** (remboursement, correction contrôlée) et la
vérification des contrôles de caisse. Existant :

- prix **toujours lus dans le catalogue** par le serveur (`sale_prices_changed` si un prix
  change entre l'enregistrement et la validation) ; `sales.subtotal` existait depuis 0007 mais
  `total = subtotal` sans exception ;
- correction d'une vente validée : annulation de chaque paiement (`sales.payment.cancel` ;
  espèces : sortie inverse dans la session d'ORIGINE encore ouverte), puis annulation de la vente
  (`sales.sale.cancel`, un `CANCELLATION` par mouvement d'origine, même lot, même coût), puis
  nouvelle vente — jamais de modification d'une vente validée ;
- limite connue W1 (ADR-0049 D13) : aucun remboursement d'espèces après clôture de la session
  d'origine (`409 cash_session_closed`).

## Décisions (livrées)

### D1 — Une remise GLOBALE par vente, montant calculé par le serveur

- Saisie : `discount = {mode: "AMOUNT" | "PERCENT", value, reason}` sur la création, la
  modification d'un brouillon et l'encaissement du point de vente (`POST /pos/checkout`) ;
  `value > 0`, 2 décimales ; motif obligatoire (3 à 200 caractères) ; tout autre champ refusé
  (`extra = forbid`).
- Montant : `AMOUNT` → la valeur ; `PERCENT` → `round_half_up(sous-total × valeur / 100, 0,01)`,
  pourcentage ≤ 100. Remise > sous-total ou pourcentage > 100 → `422 sale_discount_too_large`.
- `total = subtotal − discount_amount` : **seule base** des paiements (surpaiement refusé sur
  le total remisé), de la monnaie rendue, du crédit (exposition et limite), des créances, des
  exports et du reçu. Aucun total n'est accepté du client.
- La remise porte sur le **prix**, jamais sur la quantité : stock, lots, CMUP et recettes
  inchangés (la sortie de stock est celle des lignes).
- Remise par ligne : **non retenue** en V1 (une remise globale couvre les usages demandés sans
  toucher au prix figé des lignes ni au contrôle `sale_prices_changed`).
- Pourcentage : aide à la saisie seulement ; la vente conserve le **montant** (rechargé comme
  un montant dans un brouillon).

### D2 — Permission `sales.sale.discount` (nature `write`), par site

- Contrôlée par le serveur sur le **site de la vente** (`has_site_permission`) à chaque
  enregistrement portant une remise ; sans remise, rien ne change pour les autres rôles.
- Rôles de base : **Administrateur seulement** (motif `*`). Gestionnaire, Vendeur et Consultant
  ne la reçoivent pas : ajouter une permission aux rôles de base est une décision commerciale et
  de sécurité réservée (méthode §12). Un rôle personnalisé peut l'inclure (délégation calculée,
  ADR-0030). *À valider* : l'ouvrir au Gestionnaire, et un plafond (en %) par rôle.
- Brouillon remisé modifié par un utilisateur sans la permission : l'enregistrement qui
  conserve la remise est refusé (`403`) ; la retirer n'exige rien (prix du catalogue).

### D3 — Garanties en base et audit

- Colonnes `sales.discount_amount` (`NUMERIC(18,2)`, défaut 0), `discount_reason`,
  `discount_by` (auteur, FK `users`) — migration **0061**, additive ; ventes existantes à 0.
- Contraintes : `0 ≤ discount_amount ≤ subtotal` et `total = subtotal − discount_amount`
  (`ck_sales_discount_consistent`) ; motif et auteur présents si et seulement si la remise est
  non nulle (`ck_sales_discount_has_reason`).
- Audit : `sale.created` / `sale.updated` (avant / après) portent `subtotal`,
  `discount_amount`, `discount_reason` ; `sale.validated` porte le total remisé. Une vente
  validée est figée (remise comprise) ; la corriger suit la procédure d'annulation existante.
- Ventes issues d'une origine (règlement des commandes) : aucune remise (prix figés de
  l'origine) ; inchangé.
- Descente de 0061 refusée dès qu'une vente porte une remise.

### D4 — Interface

- Formulaire de vente et point de vente : bouton « Ajouter une remise » visible avec la
  permission ; dialogue (Montant / Pourcentage, valeur, motif, aperçu indicatif) ;
  sous-total, remise et total indicatifs ; la remise change le total : les paiements saisis
  au point de vente sont remis à zéro.
- Fiche d'une vente : sous-total, remise et motif ; reçu : sous-total et remise (sans motif ni
  auteur, données internes).

## Hors périmètre et propositions (à valider avant implémentation)

Ces points changent des règles métier, de caisse ou des permissions ; ils ne sont pas
implémentés sans décision explicite (méthode §12).

1. **Remboursement d'espèces après clôture de la session d'origine (W1)** — proposition : un
   mouvement de caisse `REFUND` dans une session **ouverte du même site** (pas nécessairement
   celle d'origine), permission nouvelle `sales.payment.refund` (Administrateur), motif,
   référence au paiement annulé, audit ; l'écart de la session d'origine n'est jamais modifié
   a posteriori.
2. **Retour partiel de marchandise** — proposition : document « avoir » lié à la vente
   (lignes et quantités retournées ≤ vendues, réintégration en stock au coût de sortie
   d'origine et sur le même lot, ou sans réintégration avec un motif de perte), remboursement
   ou crédit au client ; aujourd'hui seule l'annulation totale existe.
3. **Plafond de remise par rôle** et ouverture de `sales.sale.discount` au Gestionnaire.
4. **Correction contrôlée** : la procédure actuelle (annuler les paiements, annuler la vente,
   ressaisir) est conservée ; une aide « reprendre en brouillon » (copie des lignes d'une
   vente annulée vers un nouveau brouillon, prix relus) est possible sans règle nouvelle.

## Contrôles de caisse (vérifiés, inchangés)

Session ouverte exigée pour un paiement en espèces d'un site dont la caisse est activée ;
montant reçu et monnaie calculés par le serveur sur la seule partie espèces (désormais sur le
total remisé) ; annulation d'un paiement en espèces = sortie inverse dans la session d'origine
ouverte, sinon `409 cash_session_closed` ; clôture avec écart calculé par le serveur ;
mouvements en ajout seul.

## Conséquences

- Les rapports (R10) calculent le chiffre d'affaires sur `total` (remises déduites) et
  exposent les remises (`discount_amount`) séparément.
- Aucune permission accordée à un rôle de base existant hors Administrateur ; aucune offre
  modifiée.
