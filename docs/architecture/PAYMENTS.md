# Paiements des ventes — Phase 2.7, révisée par le Lot 1

Encaissements d'une vente, sur sa fiche (module `sales`). API sous
`/api/v1/sales/{sale_id}/payments`. Décisions : [ADR-0020](../adr/0020-paiements-des-ventes.md),
[ADR-0037](../adr/0037-encaissement.md) (Lot 1 : moyens configurables, montant reçu et monnaie,
caisse optionnelle par site, crédit avec client obligatoire).

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
| `payment_method_id` | moyen **configuré** choisi (Lot 1) — FK composite `(tenant_id, payment_method_id)` → `payment_methods` |
| `method_label` | libellé du moyen **figé** au paiement (instantané : un renommage ultérieur ne le change pas) |
| `method` | **type** figé : `CASH`, `MOBILE_MONEY`, `CARD`, `BANK_TRANSFER`, `OTHER` — seul il gouverne le comportement |
| `provider` | précision historique facultative (conservée ; les libellés configurés la remplacent) |
| `amount_received`, `change_given` | espèces seulement : montant remis et monnaie rendue, **calculée par le serveur** (`CHECK` : `change_given = amount_received − amount`, `amount_received ≥ amount`, type `CASH`) |
| `status` | `PENDING` (réservé aux encaissements asynchrones futurs), `COMPLETED`, `CANCELLED` |
| `reference` | n° de transaction, de reçu, de virement (facultatif) |
| `paid_at` | date d'encaissement (serveur) |
| `idempotency_key` | clé fournie par le client, unique par tenant (double soumission) |
| `created_by`, `cancelled_at` / `_by`, `cancellation_reason` | traçabilité ; annulé ⇒ date et motif (`CHECK`) |

Index : `(tenant_id, sale_id)`, `(tenant_id, paid_at)`, `site_id`. Jamais supprimé ; le rôle
applicatif ne peut modifier que les colonnes d'annulation (`status`, `cancelled_*`,
`cancellation_reason`, `updated_at` — droits par colonne, Lot 1) : montant, moyen, instantané et
monnaie sont immuables en base.

### Moyens de paiement configurables (Lot 1)

Table `payment_methods` (tenant-scoped, RLS) : `label` (unique par entreprise, casse ignorée),
`kind` (type, **immuable**), `integration_mode` (`MANUAL` ; `API` réservé à une intégration
future, refusé aujourd'hui : `422 payment_integration_unavailable`), `reference_required`,
`is_active` (désactivation, jamais de suppression), `sort_order`. Table `payment_method_sites` :
disponibilité **par site** (ligne absente : disponible ; `is_enabled = false` : désactivé sur ce
site). Moyens par défaut d'une nouvelle entreprise (modifiables) : Espèces, Mobile Money,
Carte bancaire, Virement, Autre. Exemples de configuration : Orange Money, Moov Money, Telecel
Money, Sank Money, Wave (`MOBILE_MONEY`, référence obligatoire).

| Type | Comportement |
|---|---|
| `CASH` | montant reçu, **monnaie** calculée par le serveur ; passe par la session de caisse **si** la caisse du site est activée |
| `MOBILE_MONEY`, `CARD`, `BANK_TRANSFER`, `OTHER` | montant payé ; **jamais de monnaie** (`422 change_not_allowed`) ; jamais de mouvement de caisse |

Un paiement désigne son moyen par `payment_method_id` (compatibilité : `method` seul désigne
l'**unique** moyen disponible de ce type sur le site, sinon `422 payment_method_required`).
Moyen inactif ou désactivé sur le site : `422 payment_method_unavailable` ; référence manquante
si exigée : `422 payment_reference_required`.

### Montant reçu et monnaie (espèces, Lot 1)

- `amount_received` seul : montant imputé = min(reçu, reste dû), monnaie = reçu − imputé.
  Ex. total 7 500, reçu 10 000 → imputé 7 500, monnaie 2 500.
- `amount` et `amount_received` : reçu ≥ montant (sinon `422 cash_received_insufficient`).
- Paiements immédiats d'une validation / du POS : autres moyens imputés **d'abord**, espèces
  ensuite ; la monnaie ne porte que sur la partie espèces. Ex. total 50 000 : espèces 20 000 +
  Orange Money 10 000, puis 30 000 remis en espèces pour solder 20 000 → monnaie 10 000.
- Le mouvement de caisse éventuel porte le montant **imputé** (la monnaie ne reste pas en caisse).

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
- **Vente sans client** (« Ordinaire ») : doit être **entièrement payée** à la validation — le
  reste dû d'une vente est un crédit, qui exige un client identifié (Lot 1,
  [`SALES.md`](SALES.md)). Une vente historique sans client restée non soldée reste payable.
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
| `sales.payment_method.manage` | admin | ✅ | — | — | — |

`GET /payment-methods` : `sales.payment.view`, `sales.payment.create` ou
`sales.payment_method.manage` ; création, modification et disponibilité par site :
`sales.payment_method.manage`, revérifiée pour le site modifié. Audit `payment_method.created`,
`payment_method.updated` (avant / après), `payment_method.site_enabled|site_disabled`.

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

- **Créances** : réalisées en Phase 2.8 ([`RECEIVABLES.md`](RECEIVABLES.md)) à partir du même
  « payé » (paiements `COMPLETED`), sans modifier les règles de paiement.
- **Caisse** : réalisée en Phase 2.9 ([`CASH_REGISTER.md`](CASH_REGISTER.md)), **optionnelle
  par site** depuis le Lot 1 : sur un site avec caisse, un paiement `CASH` exige la session
  ouverte **de l'utilisateur** sur ce site et crée son mouvement de caisse dans la même
  transaction ; sur un site sans caisse, aucune session ni mouvement (port `sales/cash_port.py` : la vente et les autres moyens
  ne dépendent jamais de la caisse) ; son annulation crée la sortie inverse (session ouverte).
- **Encaissements asynchrones** (Mobile Money par API, TPE) : statut `PENDING` réservé, déjà
  compté dans le solde engagé pour empêcher un doublon ; confirmation → `COMPLETED`. Le module
  planifié `payments` (« Paiements électroniques ») accueillera ces intégrations.
- Surpaiement et remboursements : non autorisés. Rendu de monnaie : espèces seulement, calculé
  par le serveur (Lot 1).
