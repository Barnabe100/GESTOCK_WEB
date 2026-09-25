# Module Ventes simples (Phase 2.4)

Vente **comptant** d'articles du catalogue sur un site, avec client facultatif. Code du
module : `sales` (dépend de `catalog`, `stock`, `customers` ; libellés « Ventes » via la
terminologie du profil). Décisions structurantes :
[ADR-0017](../adr/0017-ventes-prix-validation-annulation.md).

```text
Client (facultatif) ─► Vente (site, brouillon) ─► Lignes (article, quantité, prix figé)
                                  │ validation
                                  ▼
                      StockService.apply ─► mouvements SALE (−q) ─► niveaux du site
```

## 1. Modèle

| Table | Colonnes | Règles |
|---|---|---|
| `sales` | `number` (`VTE-000001`), `site_id`, `customer_id` (nullable), `status`, `sale_date`, `subtotal`, `total` (`NUMERIC(18,2)`), `notes`, auteurs et dates de création / validation / annulation, `cancellation_reason` | `UNIQUE (tenant_id, number)` ; `UNIQUE (tenant_id, id)` (cible des futurs paiements / créances) ; FK composites vers `sites` et `customers` du même tenant ; `CHECK` montants ≥ 0, date de validation si validée, motif si annulée |
| `sale_lines` | `sale_id`, `line_no`, `article_id`, `quantity` (`NUMERIC(18,3)`), `unit_price`, `line_total` (`NUMERIC(18,2)`) | FK composites vers la vente (`ON DELETE CASCADE`, lignes de brouillon) et l'article ; `UNIQUE (sale_id, article_id)` ; `CHECK quantity > 0`, prix et montant ≥ 0 |

- **Numéro** : séquence `sale` de `document_sequences` (par tenant, incrément atomique,
  annulé avec la transaction).
- **Calculs (serveur uniquement)** : `line_total = arrondi(quantity × unit_price, 2)` (demi
  supérieur, `Decimal`) ; `subtotal = Σ line_total` ; `total = subtotal` (aucune remise ni
  taxe dans cette phase : la colonne distincte prépare leur arrivée sans migration de sens).
- **Prix** : `unit_price` est **copié depuis `catalog_articles.sale_price`** à chaque
  enregistrement du brouillon. Le client (navigateur) n'envoie jamais de prix ni de total.
- `subtotal`/`total` sont stockés (figés) : l'historique ne change pas si le catalogue change.

## 2. Cycle de vie

```text
            POST /sales            PUT /sales/{id} (0..n)
   (rien) ─────────────► DRAFT ◄───────────────┐
                          │ │                   │
     POST …/validate      │ └───────────────────┘
     (sortie de stock)    ▼
                      VALIDATED ──── POST …/cancel (motif) ───► CANCELLED
                          (mouvements CANCELLATION : remise en stock)
   DRAFT ── POST …/cancel (motif) ──► CANCELLED (abandon, aucun effet sur le stock)
```

| Transition | Contrôles (backend) | Effets |
|---|---|---|
| Création | permission `create` ; site accessible (`operation_site`) ; ≥ 1 ligne ; articles du tenant, actifs, sans doublon ; quantité > 0 ; client du tenant et **actif** ; date non future | Numéro, prix copiés, totaux, audit `sale.created` |
| Modification | `update` ; statut `DRAFT` (sinon 409 `sale_not_draft`) ; mêmes contrôles | Lignes remplacées, prix relus, audit `sale.updated` (avant / après) si changement ; site non modifiable |
| Validation | `validate` ; verrou de la vente ; `DRAFT` ; articles et client toujours actifs ; **prix inchangés** (sinon 409 `sale_prices_changed`) ; stock suffisant | Mouvements `SALE`, statut `VALIDATED`, audit `sale.validated` |
| Annulation | `cancel` ; verrou ; pas déjà annulée (409 `sale_already_cancelled`) ; motif 5 à 500 caractères | Validée : mouvements inverses `CANCELLATION` ; brouillon : aucun mouvement ; audit `sale.cancelled` |

**Immuabilité** : une vente validée ou annulée n'est plus modifiable (409). Les lignes ne sont
jamais supprimées hors brouillon ; les mouvements de stock sont append-only.

## 3. Interaction avec StockService

- La vente **ne touche jamais** `stock_levels` : elle appelle `StockService.apply` (interface
  publique `stock/api.py`) avec une requête `MovementType.SALE` de quantité négative par
  ligne, `source_type="sale"`, `source_id`, `source_line_id`, `source_number` (numéro lisible
  affiché par le journal des mouvements, sans dépendance du stock vers les ventes).
- `StockService` verrouille les niveaux (`FOR UPDATE`, ordre déterministe), vérifie **toutes**
  les lignes avant d'écrire (stock jamais négatif, contrainte en base en plus), valorise la
  sortie au CMUP du site et écrit les mouvements.
- **Transaction unique** (celle de la requête, validée par l'endpoint) : verrou de la vente,
  contrôles, mouvements, niveaux, statut, audit. Toute erreur (stock insuffisant 422
  `insufficient_stock` avec le détail par article, prix changé, conflit) annule tout.
- **Idempotence / double validation** : la vente est verrouillée (`FOR UPDATE`, rechargée)
  avant le contrôle de statut ; une seconde validation, même concurrente, voit `VALIDATED` et
  échoue (409) sans mouvement. En dernier rempart, l'unicité
  `(tenant_id, source_line_id, movement_type)` des mouvements interdit une double application.
- **Concurrence entre ventes** : deux ventes validées en même temps sur le même article sont
  sérialisées par le verrou du niveau ; la seconde voit le stock restant (stock 5, ventes A = 4
  et B = 4 simultanées : l'une validée, l'autre refusée en 422, stock final 1, un seul
  mouvement — test `test_concurrent_sales_never_oversell`).
- **Annulation d'une vente validée** : un mouvement `CANCELLATION` (+q) par ligne, au coût
  unitaire du mouvement `SALE` d'origine (`origin_movement_id`) : le CMUP n'est pas modifié
  (règle STK-06). Les mouvements d'origine restent intacts.

## 4. Client

Facultatif (vente comptant anonyme si absent). S'il est fourni : même tenant (sinon 422
`customer_not_found`, jamais de fuite d'existence) et **actif** à l'enregistrement et à la
validation (422 `customer_inactive`). Un client désactivé après validation reste affiché sur
ses ventes passées. Lecture via `customers/api.py` uniquement.

## 5. Sites

`site_id` obligatoire : site actif (`X-Site-Id`) ou choisi à la création parmi les sites
accessibles au membre (`operation_site`). Liste, consultation et actions limitées aux sites
visibles (`visible_site_ids`) ; une vente d'un autre site répond 404 `sale_not_found`.

## 6. Permissions et rôles de base

| Permission | Nature | Administrateur | Gestionnaire | Vendeur | Consultant |
|---|---|---|---|---|---|
| `sales.sale.view` | read | ✓ | ✓ | ✓ | ✓ |
| `sales.sale.create` | write | ✓ | ✓ | ✓ | — |
| `sales.sale.update` | write (brouillon) | ✓ | ✓ | ✓ | — |
| `sales.sale.validate` | write (sortie de stock) | ✓ | ✓ | ✓ | — |
| `sales.sale.cancel` | write (remise en stock) | ✓ | — | — | — |

L'annulation d'une vente validée modifie le stock après coup : réservée par défaut à
l'Administrateur ; un rôle personnalisé peut l'accorder. Aucun test sur un nom de rôle ;
un abonnement expiré laisse la consultation et bloque les écritures (ADR-0011).

## 7. Sécurité et isolation

RLS `ENABLE` + `FORCE` et politique `tenant_isolation` sur `sales` et `sale_lines` ; droits du
rôle applicatif : `SELECT, INSERT, UPDATE` (ventes, jamais supprimées) et `SELECT, INSERT,
UPDATE, DELETE` (lignes de brouillon). `tenant_id` issu du jeton uniquement. Tests
d'isolation API et SQL (rôle applicatif sans `BYPASSRLS`).

## 8. Audit

`sale.created` (numéro, statut, site, client, lignes, totaux) · `sale.updated` (avant /
après) · `sale.validated` (statut précédent / nouveau, total, nombre de lignes, client) ·
`sale.cancelled` (statut précédent, motif, `stock_restored`, total). L'utilisateur, le site
et la vente (`entity_type="sale"`, `entity_id`) sont portés par l'entrée d'audit, écrite dans
la transaction de l'opération.

## 9. Interface

Menu « Ventes » (permission `sales.sale.view`). Liste : numéro, date, site (si plusieurs),
client, total, statut, auteur ; recherche (numéro, code / nom / téléphone du client), filtres
statut, site, période ; tri et pagination serveur. Saisie : site, client facultatif
(recherche des clients actifs), lignes article / quantité avec prix catalogue et montants
**indicatifs** (calcul décimal exact, le serveur fait foi), brouillon, validation confirmée
(total rappelé), annulation avec motif ; consultation en lecture seule après validation.

## 10. Hors périmètre et suite

**Paiements** : réalisés en Phase 2.7 — [`PAYMENTS.md`](PAYMENTS.md) (encaissement
indépendant de la validation, état d'encaissement calculé ; une vente encaissée ne peut être
annulée qu'après annulation de ses paiements).

**Créances et limite de crédit** : réalisées en Phase 2.8 — [`RECEIVABLES.md`](RECEIVABLES.md).
La validation contrôle la limite de crédit du client (exposition projetée = restes dus de ses
ventes validées + reste dû de la vente) sous le verrou du client, et accepte des
encaissements immédiats (`{payments: […]}`) dans la même transaction.

**Caisse** : réalisée en Phase 2.9 — [`CASH_REGISTER.md`](CASH_REGISTER.md) (un paiement
espèces exige une session de caisse ouverte sur le site de la vente).

Hors périmètre : échéances et relances, ticket / facture PDF, retours et avoirs, remises et promotions, fidélité, POS,
restaurant. Le module Ventes ne dépend d'aucun module futur ; ceux-ci s'y rattacheront :

```text
Paiements  : Vente VALIDATED ─► Paiement(s) (FK composite sales(tenant_id, id))
Crédit     : Vente VALIDATED + reste dû > 0 = créance (2.8, limite customers.credit_limit)
Remises    : colonnes de remise ligne / pied ; total = subtotal − remises (+ taxes)
Retours    : document de retour ─► mouvements RETURN via StockService
POS        : saisie rapide ─► même SaleService (création + validation en une étape)
```
