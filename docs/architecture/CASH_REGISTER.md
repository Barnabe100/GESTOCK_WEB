# Caisse — Phase 2.9

Module `cash_register`, API sous `/api/v1/cash`. Décisions :
[ADR-0022](../adr/0022-caisse.md).

## 1. Modèle

```text
Tenant
 └── Site
      └── Caisse (cash_registers)         CAI-001 ; active / désactivée ; jamais supprimée
           └── Session (cash_sessions)     SES-000001 ; OPEN → CLOSED ; une seule OPEN
                └── Mouvements (cash_movements)   append-only
```

| Concept | Rôle |
|---|---|
| Caisse | Ressource d'un site (jamais d'un utilisateur). Son site ne change pas. |
| Session | Période d'utilisation : ouverture (utilisateur, date serveur, fond initial figé), clôture (comptage). |
| Mouvement | Argent entré ou sorti de la caisse pendant une session. |
| Paiement | Paiement d'une vente (module `sales`) ; en espèces, il produit un mouvement de caisse. |

```text
Vente ─► Paiement (CASH, COMPLETED) ─► Mouvement SALE_CASH_IN ─► Session ─► Caisse ─► Site
```

Traçabilité sans duplication : mouvement → `payment_id` (FK composite, même tenant et même
site) → vente → client. Le numéro de la vente est copié (`source_number`) pour l'affichage,
comme dans les mouvements de stock.

### Types de mouvements (montant > 0 ; le type fixe le sens)

| Type | Sens | Origine |
|---|---|---|
| `OPENING_FLOAT` | + | fond initial, à l'ouverture (si > 0) |
| `SALE_CASH_IN` | + | paiement espèces d'une vente (même transaction) |
| `SALE_CASH_REVERSAL` | − | annulation d'un paiement espèces (même session, encore ouverte) |
| `MANUAL_CASH_IN` | + | entrée manuelle (apport, correction…) |
| `MANUAL_CASH_OUT` | − | sortie manuelle (dépense, remise en banque, retrait…) |

Entrées et sorties manuelles : montant, nature (`CASH_ADDITION`, `EXPENSE`, `BANK_DEPOSIT`,
`WITHDRAWAL`, `CORRECTION`, `OTHER`, selon le sens), motif (3 à 255 caractères), référence
facultative ; utilisateur, date, caisse et session fixés par le serveur.

## 2. Solde et clôture

```text
solde théorique = fond initial + Σ entrées − Σ sorties = Σ mouvements signés de la session
écart           = montant compté − solde théorique        (calculé par le serveur)
```

Exemple : fond 100 000, ventes 50 000 et 75 000, dépense 20 000, remise en banque 50 000 →
théorique 155 000 ; compté 153 500 → écart −1 500 (manquant).

- Aucun solde mutable : le journal permet toujours de reconstruire le solde (colonne « Solde »
  = solde de la session après chaque mouvement, calculé par une fenêtre SQL).
- **Sortie** : refusée si elle rend le solde négatif (`422 cash_insufficient_balance`, `balance`).
- **Clôture atomique** : verrou de la session, solde recalculé, compté enregistré, écart
  calculé, statut `CLOSED` ; théorique, compté et écart figés (écart = compté − théorique,
  `CHECK`). Aucun mouvement d'écart créé en V1.
- **Session fermée** : consultable, immuable ; plus aucun paiement espèces, entrée, sortie ni
  annulation de ses paiements espèces (`409 cash_session_closed`).

## 3. Paiements espèces

- `POST /sales/{id}/payments` (ou encaissement immédiat de `POST /sales/{id}/validate`) avec
  `method = CASH` : session ouverte d'une caisse active **du site de la vente** exigée.
  Caisse : `cash_register_id` fourni (vérifié), sinon la session ouverte par l'utilisateur,
  sinon l'unique session ouverte du site. Codes : `422 cash_session_required` (aucune),
  `422 cash_register_required` (plusieurs, `cash_register_ids`).
- Tout ou rien : jamais un paiement `COMPLETED` sans son mouvement, ni l'inverse.
- Idempotence : même `idempotency_key` → réponse rejouée, un seul paiement, un seul mouvement.
- Annulation d'un paiement espèces : sortie `SALE_CASH_REVERSAL` dans la session d'origine,
  si elle est ouverte et si son solde le permet (sinon `409 cash_session_closed` ou
  `422 cash_insufficient_balance`) ; paiement antérieur à la caisse : aucun mouvement.
- Moyens électroniques (`MOBILE_MONEY`, `CARD`, `BANK_TRANSFER`, `OTHER`) : aucune caisse
  requise, aucun mouvement.

## 4. Concurrence

| Cas | Garantie |
|---|---|
| Deux ouvertures | verrou de la caisse + index unique partiel : une seule session `OPEN` |
| Deux sorties | verrou de la session : la seconde voit la première ; jamais de solde négatif |
| Vente espèces + sortie | sérialisées (verrou de la session) |
| Paiement espèces + clôture | l'un puis l'autre : le paiement est dans la session clôturée, ou refusé |
| Double paiement (même clé) | un paiement, un mouvement |
| Deux clôtures | une seule réussit (`409 cash_session_closed`) |

Ordre des verrous : vente → paiement → session ; la clôture ne verrouille que la session.
Testé (rejoué 5 fois ; contre-épreuve sans verrou : échec).

## 5. API

| Méthode | Chemin | Permission |
|---|---|---|
| GET | `/cash/registers` (`search`, `site_id`, `status`) | `register.view` |
| POST | `/cash/registers` `{site_id?, name, description?}` | `register.manage` |
| GET / PATCH | `/cash/registers/{id}` (`name`, `description`) | `register.view` / `register.manage` |
| POST | `/cash/registers/{id}/activate`, `/deactivate` | `register.manage` |
| GET | `/cash/sessions` (`search`, `cash_register_id`, `site_id`, `status`, `opened_by`, `date_from`, `date_to`) | `session.view` |
| POST | `/cash/sessions` `{cash_register_id, opening_float}` | `session.open` |
| GET | `/cash/sessions/{id}` (totaux, théorique, compté, écart) | `session.view` |
| POST | `/cash/sessions/{id}/close` `{counted_balance, note?}` | `session.close` |
| GET | `/cash/sessions/{id}/movements`, `/cash/movements` (`movement_type`, `created_by`, `search`, `min_amount`, `max_amount`, `date_from`, `date_to`, tri `occurred_at` / `amount`) | `session.view` |
| POST | `/cash/sessions/{id}/movements` `{movement_type, amount, category, reason, reference?, idempotency_key?}` | `movement.create` |

Permissions préfixées `cash_register.`. Aucune suppression (405). Codes :
`cash_register_not_found`, `cash_session_not_found` (404, dont autre site ou autre entreprise),
`site_mismatch`, `site_access_denied` (403), `cash_register_inactive`,
`cash_session_already_open`, `cash_register_has_open_session`, `cash_session_closed`,
`idempotency_key_reused` (409), `cash_insufficient_balance`, `cash_movement_type_invalid`,
`cash_movement_category_invalid`, `cash_session_required`, `cash_register_required` (422).

## 6. Sécurité

| | Administrateur | Gestionnaire | Vendeur | Consultant |
|---|---|---|---|---|
| `register.view` / `session.view` | ✅ | ✅ | ✅ | ✅ |
| `register.manage` | ✅ | ✅ | — | — |
| `session.open` / `session.close` | ✅ | ✅ | ✅ | — |
| `movement.create` | ✅ | ✅ | — | — |

Rôles personnalisés : chaque permission séparément. RLS `ENABLE` + `FORCE` sur les trois tables
(rôle applicatif sans `BYPASSRLS` ; mouvements : `SELECT, INSERT` seulement). Sites : caisses,
sessions et mouvements d'un site non accessible introuvables (404), autre site sélectionné :
403. Abonnement expiré : consultation seule (`403 subscription_restricted`). Module désactivé :
`403 module_unavailable` (les ventes en dépendent). Audit : `cash_register.created`,
`.updated` (avant / après), `.activated`, `.deactivated`, `cash_session.opened` (fond),
`cash_session.closed` (théorique, compté, écart, observation), `cash_movement.created` (type,
montant, nature, motif, solde après) ; `payment.created` / `.completed` portent la session.

## 7. Interface

Rubrique **Caisse** : *Caisses* (statut ouverte / fermée / désactivée, caissier, solde
théorique ; ouvrir, voir la session, clôturer, gérer), *Sessions de caisse* (filtres statut,
caisse, site, période), *Journal de caisse* (tous les mouvements). Fiche de session : résumé
(fond, encaissements et entrées, sorties, solde théorique ; après clôture : compté et écart),
entrée / sortie, clôture (récapitulatif, montant compté, écart indicatif, confirmation
explicite), journal paginé et filtrable. Paiement espèces (fiche vente, validation) : caisse
ouverte du site indiquée, à choisir s'il y en a plusieurs, avertissement s'il n'y en a aucune.

## 8. Hors périmètre (V1)

POS, reporting & analytics, profils métier spécialisés, intégrations électroniques (Mobile
Money, TPE, banques), remboursements et avoirs, comptabilisation des écarts, annulation des
mouvements manuels (une erreur se corrige par un mouvement inverse motivé : `CORRECTION`),
transfert entre caisses, facturation, comptabilité.
