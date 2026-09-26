# ADR-0032 — Paiements d'abonnement : déclaration par l'entreprise, décision définitive de TechNova, aucune activation

- **Statut** : Acceptée (Phase 3.3-A)
- **Date** : 2026-09-26

## Contexte

La chaîne commerciale retenue (ADR-0025, ADR-0031) est
**PLAN → SUBSCRIPTION → PAYMENT → LICENCE → ACTIVATION**. Jusqu'en 3.2-G, seule l'activation
manuelle transitoire de la console existait : aucun paiement n'était enregistré. La Phase 3.3-A
introduit l'étape **PAYMENT** seule ; la licence signée (Ed25519) et l'activation viennent en
3.3-B.

Le mot « paiement » désigne déjà les encaissements des ventes (`payments`, module `sales`,
ADR-0020) : une donnée **métier** de l'entreprise, qui n'a rien à voir avec ce qu'elle doit à
TechNova et à laquelle TechNova n'a jamais accès.

## Décision

1. **Entité distincte** : `SubscriptionPayment` (table `subscription_payments`, actions d'audit
   `subscription_payment.*`), dans le socle `platform/subscriptions` (module `subscription`,
   *core*). Aucune réutilisation de `payments` : autre sens, autre cycle de vie, autres
   acteurs.
2. **Déclaration par l'entreprise** (`POST /subscription/payments`) : montant (> 0, 2
   décimales), période couverte (fin postérieure au début, 24 mois au plus), moyen
   (`BANK_TRANSFER`, `MOBILE_MONEY`, `CASH`, `CHECK`, `CARD`, `OTHER`), référence obligatoire,
   clé d'idempotence. Statut `PENDING`, déclarant = utilisateur du jeton. La **devise est fixée
   par le serveur** (devise figée de l'abonnement, sinon celle de l'entreprise). Le client ne
   peut envoyer ni statut, ni décideur, ni date de décision, ni motif, ni devise, ni tenant
   (`extra="forbid"` → `422`).
3. **Permission dédiée** `subscription.payment.declare`, de nature **`billing`** : autorisée
   même abonnement en attente d'activation ou expiré (c'est le moyen de régulariser),
   refusée abonnement résilié (`403 subscription_restricted`) ; rôle Administrateur (`*`) et
   propriétaire. Gestionnaire, Vendeur et Consultant ne déclarent pas ; la consultation
   réutilise `subscription.subscription.view` (le Consultant la détient). Entreprise suspendue :
   `403 tenant_suspended` comme partout.
4. **Idempotence** (même règle que ADR-0020) : clé unique `(tenant_id, idempotency_key)` en
   base ; l'abonnement est verrouillé pendant la déclaration ; même clé et même demande →
   `200` et la déclaration existante ; même clé, autre demande → `409 idempotency_key_reused`.
5. **Décision de TechNova seule**, dans la console (`POST /payments/{id}/confirm` ·
   `/reject`) : raison obligatoire (convention de la console ; pour un rejet, c'est le motif
   visible par l'entreprise), verrou de la ligne, statut `PENDING` exigé, `decided_by`,
   `decided_at`, **double audit** (journal de la plateforme + miroir dans le journal de
   l'entreprise, ADR-0031) dans la même transaction. Transitions **définitives** :
   `PENDING → CONFIRMED` ou `PENDING → REJECTED`, aucune autre. Une seconde décision
   (concurrente ou non) répond `409 payment_already_decided`.
6. **Défense en profondeur en base** : contraintes `CHECK` (montant positif, devise ISO,
   période ordonnée, référence non vide, cohérence décision ⇔ statut, motif ⇔ rejet) ;
   déclencheur `subscription_payments_final` (une ligne décidée n'est plus modifiable ; les
   données déclarées ne changent jamais, même pour le propriétaire du schéma) ; FK composite
   `(tenant_id, subscription_id)`.
7. **Rôles SQL** : applicatif — RLS `tenant_isolation`, `SELECT, INSERT` seulement (aucune
   décision possible, même par SQL). Console (`stockmanager_platform`, sans `BYPASSRLS`) —
   lecture (`platform_read`), `UPDATE` des seules colonnes de décision (`status`,
   `decided_by`, `decided_at`, `rejection_reason`, `updated_at`) d'une ligne `PENDING` vers
   `CONFIRMED` / `REJECTED` (`platform_decide`), ni insertion ni suppression. La console voit
   le nom de l'entreprise et le plan, jamais l'identité du déclarant ni une donnée métier ;
   l'entreprise ne voit jamais l'identité de l'agent TechNova.
8. **Payment CONFIRMED ≠ activation** : confirmer un paiement ne modifie ni l'abonnement ni
   l'entreprise (tests backend et E2E). Aucune licence, aucune signature, aucune clé : la
   licence (3.3-B) portera la référence du paiement confirmé et l'activation viendra de son
   import. L'activation manuelle transitoire de 3.2-G reste inchangée d'ici là.

## Conséquences

- L'entreprise a un historique de ses paiements (statut, motif de rejet) ; TechNova une file
  de vérification filtrable (statut, entreprise, référence).
- Aucun paiement ne peut être confirmé par l'entreprise, ni par l'API, ni par SQL.
- 3.3-B : la licence référencera un paiement `CONFIRMED` ; l'import d'une licence ne créera
  jamais, à lui seul, de paiement confirmé.
- Le type partagé `PositiveMoney` accepte encore un nombre JSON (convention existante des
  montants) ; le serveur reste la seule validation (2 décimales, > 0).

## Alternatives écartées

- **Réutiliser `payments`** (ventes) : mélange donnée métier et relation commerciale TechNova ;
  la console aurait dû lire une table métier.
- **Confirmation = activation automatique** : contraire à la chaîne décidée (la licence signée
  fait foi) ; rendrait l'activation dépendante d'une saisie manuelle.
- **Devise saisie par l'entreprise** : source d'incohérences avec le prix figé ; la devise est
  une donnée de l'abonnement.
- **Réutiliser `subscription.subscription.view` pour déclarer** : une lecture n'autorise pas
  une écriture ; la nature `billing` exprime exactement ce que la politique d'abonnement doit
  laisser passer.
