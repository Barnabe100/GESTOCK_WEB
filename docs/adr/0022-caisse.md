# ADR-0022 — Caisse : caisse de site, sessions, mouvements append-only, encaissements espèces dans la transaction du paiement

- **Statut** : Proposée (implémentée et testée en Phase 2.9 ; validation TechNova en attente)
- **Date** : 2026-09-25

## Contexte

Depuis la Phase 2.7, un paiement `CASH` est un simple enregistrement : aucune trace de l'argent
dans une caisse physique. La Phase 2.9 introduit la caisse comme module financier
transactionnel (ouverture, fond initial, encaissements espèces, entrées et sorties, journal,
solde théorique, comptage, écart, clôture), sans POS, sans reporting complet, sans
comptabilité.

## Décision

1. **Module `cash_register`** (déjà déclaré « planned » dans les plans et profils, passé
   « available ») ; API sous `/api/v1/cash` (`route_prefix`). Permissions
   `cash_register.{register.view, register.manage, session.view, session.open,
   session.close, movement.create}` (préfixe = code du module, règle du registre).
2. **Modèle** `Tenant → Site → Caisse → Session → Mouvement`, FK composites de bout en bout :
   `cash_sessions (tenant_id, cash_register_id, site_id) → cash_registers` ;
   `cash_movements (tenant_id, cash_session_id, cash_register_id, site_id) → cash_sessions`.
   Une caisse appartient à un site, jamais à un utilisateur ; son site ne change pas ; elle est
   désactivée, jamais supprimée (refus si une session est ouverte).
3. **Une seule session ouverte par caisse** : verrou de la caisse (`SELECT … FOR UPDATE`) à
   l'ouverture + index unique partiel `(tenant_id, cash_register_id) WHERE status = 'OPEN'`.
   Statuts `OPEN` / `CLOSED` seulement (pas de `CLOSING` : la clôture est atomique).
4. **Solde calculé, jamais stocké comme valeur mutable** : solde théorique = Σ mouvements signés
   (montant toujours > 0, le type fixe le sens). Le fond initial est un mouvement
   `OPENING_FLOAT` (traçable) et une colonne figée de la session. À la clôture, solde théorique,
   montant compté et écart (= compté − théorique, `CHECK` en base) sont figés : instantané
   immuable, vérifiable contre les mouvements.
5. **Mouvements append-only** (droits `SELECT, INSERT` du rôle applicatif) :
   `OPENING_FLOAT`, `SALE_CASH_IN`, `MANUAL_CASH_IN`, `MANUAL_CASH_OUT` et
   **`SALE_CASH_REVERSAL`** (ajouté : l'annulation d'un paiement espèces, qui existe depuis la
   2.7, doit sortir l'argent de la caisse). Une sortie ne rend jamais le solde négatif.
6. **Encaissement espèces = même transaction que le paiement** : le module `sales` dépend
   désormais de `cash_register` et appelle son API publique (`CashService.record_sale_cash_in`)
   dans `PaymentService.create`, après le verrou de la vente : paiement et mouvement sont créés
   ensemble ou pas du tout. Le mouvement référence le paiement par FK composite
   `(tenant_id, payment_id, site_id) → payments` (nouvelle unicité `(tenant_id, id, site_id)`
   sur `payments`) : même tenant et même site garantis en base ; au plus un encaissement et une
   annulation par paiement. Aucun mouvement pour `MOBILE_MONEY`, `CARD`, `BANK_TRANSFER`,
   `OTHER`. La caisse ne dépend pas des ventes (numéro de vente copié, comme les mouvements de
   stock) : pas de cycle.
7. **Choix de la caisse par le serveur** : session ouverte d'une caisse active **du site de la
   vente** ; celle demandée (`cash_register_id`, vérifiée), sinon celle ouverte par
   l'utilisateur, sinon l'unique session ouverte du site ; plusieurs possibles sans choix :
   `422 cash_register_required` ; aucune : `422 cash_session_required` (jamais de caisse
   arbitraire ou d'un autre site, jamais d'encaissement sans session). L'autorisation réutilise
   l'accès aux sites existant ; `sales.payment.create` suffit pour encaisser.
8. **Concurrence** : verrou de la session pour tout mouvement et pour la clôture ; ordre constant
   vente → paiement → session (la clôture ne verrouille que la session) : sorties, encaissements,
   entrées et clôture concurrents sont sérialisés, sans interblocage. Idempotence : clé des
   paiements réutilisée (réponse rejouée avant toute écriture de caisse) ; clé facultative
   équivalente pour les mouvements manuels.
9. **Clôture** : montant compté saisi ; écart recalculé par le serveur ; **aucun mouvement
   d'écart** en V1 (comptabilisation des écarts : décision future). Session fermée : immuable,
   aucun paiement, entrée, sortie ni annulation d'un de ses paiements espèces (refus
   `cash_session_closed` ; remboursement hors périmètre).
10. **Nature des mouvements manuels** : liste fixe (`CASH_ADDITION`, `EXPENSE`, `BANK_DEPOSIT`,
    `WITHDRAWAL`, `CORRECTION`, `OTHER`) + motif libre obligatoire. Les motifs de sortie de stock
    ne sont **pas** réutilisés : ils décrivent des pertes de marchandise (paramétrables par
    tenant, liés aux mouvements de stock) — les forcer ici brouillerait les deux domaines.
11. **Rôles de base** : Gestionnaire `cash_register.*` ; Vendeur : consultation, ouverture et
    clôture de sa caisse, encaissement (via les paiements), sans gestion des caisses ni
    mouvements manuels ; Consultant : consultation ; Administrateur : tout.
12. **Reporting futur** : chaque mouvement porte tenant, site, caisse, session, utilisateur,
    date, type, nature, montant et source (vente, paiement) ; chaque session, ouverture /
    clôture, fond, théorique, compté, écart. Index `(tenant_id, occurred_at)`,
    `(tenant_id, cash_session_id)`, `(tenant_id, opened_at)`.

## Conséquences

- Un paiement espèces (ou un encaissement espèces à la validation) exige une session de caisse
  ouverte sur le site de la vente ; les tests et E2E des phases 2.7 / 2.8 ouvrent une caisse.
- Désactiver la caisse désactive aussi les ventes (dépendance) ; l'API d'administration refuse
  de la désactiver tant que les ventes sont actives (`module_has_dependents`).
- Les paiements espèces antérieurs à la 2.9 n'ont pas de mouvement : leur annulation ne touche
  pas la caisse.
- Migration `0011` (tables, RLS, droits minimaux, index partiel, unicité sur `payments`).

## Alternatives écartées

- **Paiement = mouvement de caisse** (une seule table) : confond encaissement d'une vente et
  trésorerie physique ; les moyens électroniques n'ont pas de caisse.
- **Solde stocké et mis à jour** : seconde source de vérité ; le verrou de la session et
  l'agrégation suffisent.
- **Mouvement automatique d'écart à la clôture** : décision comptable reportée.
- **Caisse dépendant des ventes et « crochet » d'extension** : mécanisme d'inversion inutile ;
  la dépendance directe ventes → caisse est explicite, comme ventes → stock.
- **Caisse choisie par le client sans contrôle** : le serveur vérifie site, caisse active et
  session ouverte.
