# Créances / comptes clients — Phase 2.8

Consultation et contrôle de ce que doivent les clients, à partir des ventes validées et des
paiements (module `receivables`, API `/api/v1/receivables` et `/api/v1/customers/{id}/…`).
Décisions : [ADR-0021](../adr/0021-creances-comptes-clients.md).

## 1. Définition (source de vérité unique)

```text
Créance ouverte = vente VALIDATED + reste dû > 0
reste dû        = total de la vente − Σ paiements COMPLETED
```

| Situation | Créance ouverte ? |
|---|---|
| Vente 100 000, aucun paiement | oui, 100 000 |
| Vente 100 000, paiement 40 000 | oui, 60 000 |
| Vente 100 000, payée 100 000 | non |
| Paiement de 120 000 sur 100 000 | impossible (Phase 2.7 : `payment_exceeds_balance`) |
| Paiement de 60 000 annulé | ne compte plus : créance 100 000 |
| Brouillon / vente annulée | jamais |

Aucune table de créances, aucun solde stocké (ni sur la vente, ni sur le client) : les montants
sont calculés à chaque lecture par agrégation SQL (`sales/credit.py`, exposé par `sales/api.py`).
Aucune migration en Phase 2.8.

Une vente validée **sans client** et non soldée est listée (« Sans client ») et comptée dans le
total dû, pas dans les clients débiteurs ; elle ne crée aucune exposition client.

## 2. Exposition et limite de crédit

```text
exposition actuelle = Σ restes dus des créances ouvertes du client (tous sites)
exposition projetée = exposition actuelle + (total de la vente − encaissements immédiats)
refus si exposition projetée > credit_limit
```

| `credit_limit` | Comportement |
|---|---|
| `NULL` | **non configurée** : aucune limite (jamais « crédit interdit ») |
| `0` | aucune vente à crédit : seule une vente intégralement payée à la validation passe |
| `100 000` / `500 000` | refus au-delà ; limite atteinte exactement : acceptée |

- **Où** : à la **validation** de la vente (`SaleService.validate`), quand l'exposition naît —
  jamais à la création ou à la modification d'un brouillon.
- **Vente payée immédiatement** : `POST /sales/{id}/validate` accepte
  `{payments: [{amount, method, provider?, reference?}]}`, encaissés dans la même transaction
  via `PaymentService` (droit `sales.payment.create` requis). Exemple : limite 500 000,
  créances 300 000, vente 100 000 payée à la validation → exposition 300 000, acceptée ;
  limite 500 000, créances 450 000, vente 100 000 sans paiement → 550 000, refusée
  (`422 credit_limit_exceeded`).
- **Tout ou rien** : un refus annule la validation entière (ni stock, ni statut, ni paiement,
  ni audit).
- **Concurrence** : verrou de la ligne client (`FOR NO KEY UPDATE`) après celui de la vente ;
  deux validations simultanées de 70 000 pour une limite de 100 000 → une seule acceptée,
  exposition 70 000 (testé, y compris sur deux sites). Paiements et annulations de paiements ne
  prennent pas ce verrou : aucun interblocage.
- La limite est une règle de vente : appliquée même si le module Créances est désactivé.
- Modifier la limite : `PATCH /customers/{id}`, audité (`customer.updated`, avant / après).

## 3. API (lecture seule)

| Méthode | Chemin | Rôle |
|---|---|---|
| GET | `/receivables` | Créances ouvertes paginées : `search` (numéro de vente, code / nom / téléphone du client), `customer_id`, `site_id`, `date_from`, `date_to` (date de vente), `min_amount`, `max_amount` (reste dû), `status` (`UNPAID` \| `PARTIALLY_PAID`) ; tri `sale_date` (défaut : plus anciennes d'abord), `sale_number`, `total`, `paid_amount`, `remaining_amount`, `customer_name` |
| GET | `/receivables/summary` | `total_receivables`, `receivables_count`, `debtor_customers_count` (mêmes filtres) |
| GET | `/receivables/{sale_id}` | Détail d'une vente validée : client, vente, date, site, total, payé, solde, `is_open`, historique des paiements (annulés compris) |
| GET | `/customers/{id}/receivables` | Créances ouvertes du client (mêmes filtres et tri) |
| GET | `/customers/{id}/credit-exposure` | `credit_limit`, `limit_configured`, `current_exposure`, `available_credit`, `over_limit`, `open_receivables_count`, `consolidated` |

Permission `receivables.receivable.view` (toutes les routes). Aucun indicateur de retard :
aucune échéance n'existe. Codes : `receivable_not_found`, `customer_not_found` (404),
`site_mismatch` (403), `invalid_sort` (400), `credit_limit_exceeded` (422, à la validation d'une
vente : `credit_limit`, `sale_exposure` et, en vue consolidée, `current_exposure`,
`available_credit`).

## 4. Sécurité

| | Administrateur | Gestionnaire | Vendeur | Consultant |
|---|---|---|---|---|
| `receivables.receivable.view` | ✅ | ✅ | ✅ | ✅ |

Attribuable à un rôle personnalisé. Tenant : RLS et filtre ORM (aucun `tenant_id` venant du
client). Sites : un membre limité à certains sites ne voit que leurs créances (détail d'une
autre : `404`) ; l'exposition consolidée et le crédit disponible ne sont communiqués qu'à un
membre voyant tous les sites sans site sélectionné (sinon `consolidated: false`,
`available_credit: null`). Abonnement expiré : consultation normale. Module désactivé :
`403 module_unavailable`. Client désactivé : son historique et ses créances restent
consultables. La consultation n'écrit aucun audit.

## 5. Interface

- **Ventes et clients ▸ Créances** : indicateurs (total dû, créances ouvertes, clients
  débiteurs), filtres (recherche, client, site, état, solde minimal, période), tableau (client,
  vente, date, site, total, payé, solde dû, état), ouverture de la fiche de vente (articles,
  paiements, solde dû).
- **Fiche client ▸ Compte client** : limite de crédit (« Non configurée » si nulle, sans
  montant disponible), exposition actuelle, crédit disponible, créances ouvertes.
- **Validation d'une vente** : option « Encaisser un paiement maintenant » (montant, moyen) ;
  refus de limite expliqué avec les montants renvoyés par le serveur.

## 6. Hors périmètre (phases suivantes)

Échéances, pénalités, intérêts, échéanciers, relances (SMS, email), remboursements, avoirs,
compensation, écritures comptables, caisse, POS, intégrations Mobile Money, facturation.
