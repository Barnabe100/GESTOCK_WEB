# Paiements des ventes — Phase 2.7

Encaissements d'une vente, sur sa fiche (module `sales`). API sous
`/api/v1/sales/{sale_id}/payments`. Décisions : [ADR-0020](../adr/0020-paiements-des-ventes.md).

## 1. Règle fondamentale (validée par TechNova)

La **validation** d'une vente est **indépendante** de son **encaissement**. Une vente validée
sort immédiatement le stock et peut être totalement payée, partiellement payée ou non payée
(le reste dû d'une vente avec client est une future créance).

| Dimension | Valeurs | Source |
|---|---|---|
| Statut commercial `sale.status` | `DRAFT`, `VALIDATED`, `CANCELLED` | stocké (cycle de vente, [`SALES.md`](SALES.md)) |
| État d'encaissement `payment_status` | `UNPAID`, `PARTIALLY_PAID`, `PAID` | **calculé** à partir des paiements |

Exemple : vente 100 000 validée (stock sorti) → payé 0, solde 100 000, non payée ; paiement de
30 000 → payé 30 000, solde 70 000, partiellement payée ; paiement de 70 000 → payée, solde 0.

## 2. Modèle

Table `payments` (tenant-scoped, RLS `ENABLE` + `FORCE`) :

| Colonne | Rôle |
|---|---|
| `number` | `PAY-000001` (séquence `payment` de `document_sequences`), unique par tenant |
| `sale_id`, `site_id` | vente payée et son site (copié) — FK composite `(tenant_id, sale_id, site_id)` → `sales` : même tenant **et** même site que la vente |
| `amount` | `NUMERIC(18,2)`, `CHECK amount > 0` |
| `method` | `CASH`, `MOBILE_MONEY`, `CARD`, `BANK_TRANSFER`, `OTHER` (catégorie, code technique) |
| `provider` | précision facultative (Orange Money, Moov Money, Wave…) — sans nouveau code de moyen |
| `status` | `PENDING` (réservé aux encaissements asynchrones futurs), `COMPLETED`, `CANCELLED` |
| `reference` | n° de transaction, de reçu, de virement (facultatif) |
| `paid_at` | date d'encaissement (serveur) |
| `idempotency_key` | clé fournie par le client, unique par tenant (double soumission) |
| `created_by`, `cancelled_at` / `_by`, `cancellation_reason` | traçabilité ; annulé ⇒ date et motif (`CHECK`) |

Index : `(tenant_id, sale_id)`, `(tenant_id, paid_at)`, `site_id`. Jamais supprimé (droits du
rôle applicatif : `SELECT, INSERT, UPDATE`).

## 3. Calcul du solde (aucun état stocké)

```text
payé  = somme des paiements COMPLETED de la vente
reste = total de la vente − payé
état  = UNPAID si payé = 0 ; PARTIALLY_PAID si 0 < payé < total ; PAID si payé = total
```

Aucune colonne matérialisée : les paiements sont l'unique source de vérité. Le calcul est une
agrégation SQL — une requête pour une page de ventes (`GET /sales`, filtre `payment_status`),
une pour la fiche. L'état n'existe que pour une vente **validée**.

## 4. Règles

- **Encaissement** : vente `VALIDATED` seulement (`409 sale_not_payable` pour un brouillon ou
  une vente annulée) ; montant > 0, 2 décimales ; **pas de surpaiement** : le solde est recalculé
  par le serveur sous le **verrou de la vente** (`SELECT … FOR UPDATE`) —
  `422 payment_exceeds_balance` (`remaining`) ou `sale_already_paid`. Le montant envoyé par
  l'interface ne sert jamais à calculer le solde.
- **Paiement partiel, successifs, mixte** : autant de paiements que nécessaire, chacun conservé
  individuellement, avec son moyen (paiement mixte = plusieurs paiements).
- **Vente sans client** : payable comme toute vente validée.
- **Immuable après encaissement** : ni montant ni moyen modifiables (aucune route de
  modification ou de suppression). Une erreur se corrige par **annulation**
  (`COMPLETED → CANCELLED`, motif 5–500 caractères, auteur, date) puis nouveau paiement.
  Le paiement annulé reste dans l'historique (montant d'origine conservé) et ne compte plus.
- **Annuler un paiement n'annule pas la vente** : elle reste `VALIDATED`, le stock ne bouge pas.
- **Annuler une vente encaissée** est refusé (`409 sale_has_payments`) : annuler d'abord ses
  paiements (le remboursement est hors périmètre).
- **Stock** : un paiement ne déclenche jamais de mouvement de stock.

## 5. Concurrence et double soumission

- Deux encaissements simultanés (ex. 70 000 + 70 000 sur un solde de 100 000) : le verrou de la
  vente les sérialise ; le second voit le premier et est refusé. Jamais payé > total.
- Encaissement et annulation de la vente simultanés : sérialisés par le même verrou.
- **Clé d'idempotence** (`idempotency_key`, UUID généré par l'interface à l'ouverture du
  formulaire) : une seconde soumission identique renvoie le paiement déjà créé (`200`, aucun
  doublon) ; la même clé pour un autre paiement → `409 idempotency_key_reused`. Unicité en base
  en dernier recours. Mécanisme réutilisable par la future caisse / le POS hors ligne.

## 6. Sécurité

| Permission | Nature | Administrateur | Gestionnaire | Vendeur | Consultant |
|---|---|---|---|---|---|
| `sales.payment.view` | lecture | ✅ | ✅ | ✅ | ✅ |
| `sales.payment.create` | écriture | ✅ | ✅ | ✅ | — |
| `sales.payment.cancel` | écriture | ✅ | — | — | — |

L'annulation d'un paiement suit la convention des autres annulations (ventes, documents de
stock) : réservée à l'Administrateur par défaut ; un rôle personnalisé peut la recevoir.
Module `sales` exigé (`403 module_unavailable`), politique d'abonnement inchangée (expiré :
consultation seule, `403 subscription_restricted`), périmètre des sites de la vente (vente
d'un site non accessible : `404` ; autre site sélectionné : `403 site_mismatch`), RLS et FK
composites.

## 7. Audit

`payment.created`, `payment.completed` (solde après encaissement), `payment.cancelled` (motif,
solde après annulation) : tenant, utilisateur, site, vente (id, numéro), paiement (id, numéro),
montant, moyen, date. Aucune donnée sensible (pas de numéro de carte).

## 8. Évolutions prévues (hors périmètre 2.7)

- **Créances** : le reste dû des ventes validées avec client (`remaining_amount`,
  `customer_id`, filtre `payment_status`) est directement exploitable.
- **Caisse** : `method`, `site_id`, `paid_at`, `amount` suffisent à rattacher les
  encaissements en espèces à une session de caisse (aucun mouvement de caisse créé en 2.7).
- **Encaissements asynchrones** (Mobile Money par API, TPE) : statut `PENDING` réservé, déjà
  compté dans le solde engagé pour empêcher un doublon ; confirmation → `COMPLETED`. Le module
  planifié `payments` (« Paiements électroniques ») accueillera ces intégrations.
- Surpaiement, rendu monnaie, remboursements : non autorisés en V1.
