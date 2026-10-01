# Module Ventes (Phase 2.4, révisée par le Lot 1)

Vente d'articles du catalogue sur un site, comptant (client facultatif, affiché « Ordinaire ») ou
à crédit (client identifié obligatoire). Code du module : `sales` (dépend de `catalog`,
`stock`, `customers` ; libellés « Ventes » via la terminologie du profil). Décisions
structurantes : [ADR-0017](../adr/0017-ventes-prix-validation-annulation.md),
[ADR-0037](../adr/0037-encaissement.md) (Lot 1 : numérotation, crédit, portée).

```text
Client (facultatif) ─► Vente (site, brouillon) ─► Lignes (article, quantité, prix figé)
                                  │ validation
                                  ▼
                      StockService.apply ─► mouvements SALE (−q) ─► niveaux du site
```

## 1. Modèle

| Table | Colonnes | Règles |
|---|---|---|
| `sales` | `number` (nul au brouillon ; `VENT-{SITE}-{ANNÉE}-{SÉQUENCE}` à la validation), `site_id`, `customer_id` (nullable), `status`, `sale_date`, `subtotal`, `total` (`NUMERIC(18,2)`), `notes`, auteurs et dates de création / validation / annulation, `cancellation_reason` | `UNIQUE (tenant_id, number)` ; `UNIQUE (tenant_id, id)` (cible des futurs paiements / créances) ; FK composites vers `sites` et `customers` du même tenant ; `CHECK` montants ≥ 0, date de validation si validée, motif si annulée, **numéro si validée** (`validated_has_number`), exception de crédit complète ou absente (`credit_override_complete`) ; `is_credit`, `credit_override_by` / `_at` / `_reason` / `_amount` (Lot 1) |
| `sale_lines` | `sale_id`, `line_no`, `article_id`, `quantity` (`NUMERIC(18,3)`), `unit_price`, `line_total` (`NUMERIC(18,2)`) | FK composites vers la vente (`ON DELETE CASCADE`, lignes de brouillon) et l'article ; `UNIQUE (sale_id, article_id)` ; `CHECK quantity > 0`, prix et montant ≥ 0 |

- **Numéro (Lot 1)** : `VENT-{CODE_SITE}-{ANNÉE}-{SÉQUENCE}` (ex. `VENT-OUA-2026-000154`),
  attribué **à la validation** — jamais au brouillon (numéro nul, affiché « non numérotée »).
  Compteur `document_sequences` par **tenant, site et année** (clé `{site_id}:sale:{année}`,
  `BIGINT`, incrément atomique, ligne verrouillée jusqu'à la fin de la transaction : numéros
  distincts et consécutifs sous concurrence, annulé avec la transaction). Six chiffres minimum
  (présentation), sans limite : `…-999999` puis `…-1000000`. Année dans le fuseau du tenant.
  Numéro **définitif** après validation (déclencheur `sales_number_immutable`). Lot 2
  (ADR-0038) : un brouillon n'a jamais de numéro et la validation attribue toujours un numéro
  `VENT-…` (aucune reprise d'un ancien numéro ; les `VTE-…` n'étaient que des données de test).
  Le **code du site** devient non modifiable dès son premier numéro (`409 site_code_locked`).
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
| Validation | `validate` ; verrou de la vente ; `DRAFT` ; articles et client toujours actifs ; **prix inchangés** (sinon 409 `sale_prices_changed`) ; moyens de paiement disponibles sur le site ; crédit (§ 4 bis) ; stock suffisant | Numéro `VENT-…`, mouvements `SALE`, paiements immédiats, statut `VALIDATED`, audit `sale.validated` (numéro, `is_credit`) |
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

## 4 bis. Crédit (Lot 1)

Le reste dû à la validation (total − encaissements immédiats) fait de la vente une **vente à
crédit** (`is_credit`) :

- **client identifié obligatoire** : sans client, `422 credit_customer_required` (`remaining`) ;
  une vente ordinaire doit être entièrement payée ;
- permission **`sales.sale.credit_create`** sur le site de la vente (`403 credit_not_allowed`) ;
  elle ne lève aucune autre règle ;
- **limite de crédit** du client (`credit_limit` NULL = pas de limite) : exposition projetée
  > limite ⇒ `422 credit_limit_exceeded` (`credit_limit`, `sale_exposure`,
  `override_allowed` ; exposition consolidée seulement pour qui voit tous les sites) ;
- **exception** : `credit_override: {reason}` (5 à 500 caractères) par un utilisateur détenant
  **`sales.sale.credit_override`** sur le site (`403 credit_override_not_allowed` sinon) ;
  autorisateur (utilisateur authentifié), date, montant du dépassement et motif enregistrés sur
  la vente, audit `sale.credit_limit_overridden` ; même transaction, sous le verrou du client
  (deux validations simultanées pour un même client : la seconde voit la première) ;
- **statut calculé** `credit_status` : `OPEN` (rien payé), `PARTIAL`, `PAID`, `CANCELLED`
  (vente annulée), à partir des paiements effectués.

Reprise : `is_credit` renseigné pour les ventes validées existantes (reste dû d'après les
paiements datés au plus tard de la validation).

## 5. Sites

`site_id` obligatoire : site actif (`X-Site-Id`) ou choisi à la création parmi les sites
accessibles au membre (`operation_site`). Liste, consultation et actions limitées aux sites
visibles (`visible_site_ids`) ; une vente d'un autre site répond 404 `sale_not_found`.

**Portée (Lot 1)** : `sales.sale.view` donne accès à **ses propres ventes** ;
`sales.sale.view_all` à toutes les ventes des sites autorisés (liste, fiche, paiements ;
une vente d'un autre utilisateur hors portée répond 404). Évaluée par site (permissions du
membre sur ce site), jamais par nom de rôle.

## 6. Permissions et rôles de base

| Permission | Nature | Administrateur | Gestionnaire | Vendeur | Consultant |
|---|---|---|---|---|---|
| `sales.sale.view` | read (ses ventes) | ✓ | ✓ | ✓ | ✓ |
| `sales.sale.view_all` | read (toutes celles du site) | ✓ | ✓ | — | ✓ |
| `sales.sale.create` | write | ✓ | ✓ | ✓ | — |
| `sales.sale.update` | write (brouillon) | ✓ | ✓ | ✓ | — |
| `sales.sale.validate` | write (sortie de stock) | ✓ | ✓ | ✓ | — |
| `sales.sale.cancel` | write (remise en stock) | ✓ | — | — | — |
| `sales.sale.credit_create` | write (vente à crédit) | ✓ | ✓ | — | — |
| `sales.sale.credit_override` | write (dépassement de limite justifié) | ✓ | — | — | — |
| `sales.sale.export` | export (historique, Lot 2) | ✓ | ✓ | — | — |

`sales.sale.export` (ADR-0038) s'ajoute à `sales.sale.view` (exigée aussi) et ne remplace
aucun contrôle : tenant, sites visibles, portée `view` / `view_all` ; un site où le membre ne
détient pas l'export n'est jamais exporté.

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

Exports (Lot 2) : `export.generated` (`entity_type="export"`) — fonctionnalité
(`sales.history`), format, filtres réellement renseignés, nombre de lignes.

## 9. Historique, filtres et exports (Lot 2, ADR-0038)

- **Tri par défaut** : `-created_at` (la plus récente d'abord) pour la liste, le tableau de
  bord et le point de vente ; tri par numéro proposé en option (ordre alphabétique).
- **Filtres** (`SaleFilters`, mêmes paramètres pour `GET /sales` et `GET /sales/export`) :
  `search` (numéro, code / nom / téléphone du client), `status`, `site_id`, `customer_id`,
  `date_from` / `date_to`, `payment_status`, `channel` (`BACKOFFICE` / `POS`), `seller_id`
  (vendeur / opérateur = `created_by`, candidats : `GET /sales/sellers`), `mine` (« Mes
  ventes »), `article_id`, `article_reference` (référence ou code-barres d'un article vendu),
  `payment_reference` (n° de transaction d'un paiement) — deux références distinctes.
- **Export** : `GET /sales/export?format=xlsx|csv|pdf&…filtres…&sort=…` — exactement les
  ventes de la liste (même requête `SaleService.query`, même tri), sans pagination, au plus
  `SM_EXPORT_MAX_ROWS` (50 000 ; au-delà `422 export_too_large`). Colonnes : numéro, date de
  vente, site, client, vendeur, canal, statut, total, payé, reste dû, encaissement, crédit,
  date de validation. CSV `;` UTF-8 BOM décimales à virgule ; Excel typé ; PDF A4 paysage
  (titre, entreprise, date de génération, filtres, pagination). Architecture commune :
  [`app/platform/exports.py`](../../backend/app/platform/exports.py).
- **Fiche enrichie** : mouvements de stock de la vente (`GET /stock/movements?source_type=sale
  &source_id=…`, `stock.movement.view`, sites du périmètre) ; chronologie
  (`GET /sales/{id}/history`, `audit.log.view` + vente visible) : évènements réellement
  journalisés de la vente et de ses paiements.

## 9 bis. Interface

Menu « Ventes » (permission `sales.sale.view`). Liste : numéro, date, site (si plusieurs),
client, total, statut, encaissement, canal, vendeur / opérateur ; recherche, filtres statut,
encaissement, site, période, vendeur, « Mes ventes », puis « Plus de filtres » (canal, client,
article, référence article, référence de paiement) ; tri et pagination serveur ; **UNE**
action « Exporter » (menu des formats Excel / CSV / PDF), affichée seulement avec
`sales.sale.export`, qui transmet les filtres affichés. Saisie : site, client facultatif
(recherche des clients actifs), lignes article / quantité avec prix catalogue et montants
**indicatifs** (calcul décimal exact, le serveur fait foi), brouillon, validation confirmée
(total rappelé), annulation avec motif ; consultation en lecture seule après validation, avec
les sections « Mouvements de stock » et « Chronologie » selon les permissions.

## 10. Hors périmètre et suite

**Paiements** : réalisés en Phase 2.7 — [`PAYMENTS.md`](PAYMENTS.md) (encaissement
indépendant de la validation, état d'encaissement calculé ; une vente encaissée ne peut être
annulée qu'après annulation de ses paiements).

**Créances et limite de crédit** : réalisées en Phase 2.8 — [`RECEIVABLES.md`](RECEIVABLES.md).
La validation contrôle la limite de crédit du client (exposition projetée = restes dus de ses
ventes validées + reste dû de la vente) sous le verrou du client, et accepte des
encaissements immédiats (`{payments: […]}`) dans la même transaction.

**Point de vente** : Phase 3.0 — [`POS.md`](POS.md) : `SaleService.checkout` (création +
validation + paiements en une transaction, idempotent), canal `POS` ; mêmes règles.

**Caisse** : réalisée en Phase 2.9 — [`CASH_REGISTER.md`](CASH_REGISTER.md), optionnelle par
site depuis le Lot 1 (site avec caisse : un paiement espèces exige la session de
l'utilisateur ; site sans caisse : aucune session).

Hors périmètre : échéances et relances, ticket / facture PDF, retours et avoirs, remises et promotions, fidélité, POS,
restaurant. Le module Ventes ne dépend d'aucun module futur ; ceux-ci s'y rattacheront :

```text
Paiements  : Vente VALIDATED ─► Paiement(s) (FK composite sales(tenant_id, id))
Crédit     : Vente VALIDATED + reste dû > 0 = créance (2.8, limite customers.credit_limit)
Remises    : colonnes de remise ligne / pied ; total = subtotal − remises (+ taxes)
Retours    : document de retour ─► mouvements RETURN via StockService
POS        : saisie rapide ─► même SaleService (création + validation en une étape)
```
