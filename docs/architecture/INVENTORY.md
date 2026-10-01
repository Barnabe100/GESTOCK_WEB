# Inventaires de stock — Phase 2.6

Module `inventory_count` (déjà déclaré dans les plans STANDARD et ENTREPRISE et dans tous les
profils ; il passe de `planned` à `available`). API sous `/api/v1/inventories`, menu
**Stock ▸ Inventaires**. Décisions : [ADR-0019](../adr/0019-inventaires.md).

## 1. Règle centrale

Un inventaire compare le **stock théorique** à la **quantité physique constatée**. À la
validation :

```text
écart = quantité physique − stock courant AU MOMENT DE LA VALIDATION
```

Le stock courant est relu (et verrouillé) à la validation : les entrées, sorties, ventes et
transferts intervenus pendant le comptage sont pris en compte. Le stock théorique initial
(capturé au démarrage du comptage) n'est qu'une **information de traçabilité**.

Exemple : stock 100 au début, 95 comptés, +10 (entrée) et −5 (vente) pendant le comptage.
Stock courant à la validation : 105 ; écart **−10** ; stock final **95**. (Et non 95 − 100 = −5,
qui laisserait 100 en stock.)

## 2. Modèle

| Table | Rôle |
|---|---|
| `inventories` | En-tête : `number` (`INV-000001`, séquence `inventory`, unique par tenant), `site_id`, `status`, `inventory_type`, `comment`, `created_by`, `started_at`/`_by`, `completed_at`/`_by`, `validated_at`/`_by`, `cancelled_at`/`_by`, `cancellation_reason`, horodatages |
| `inventory_lines` | Une ligne par article (`UNIQUE (inventory_id, article_id)`) : `stock_theoretical_initial`, `stock_theoretical_at_validation`, `quantity_physical` (≥ 0), `quantity_variance`, `unit_cost` (CMUP figé), `adjustment_value`, `counted_at`/`counted_by` ; comptage en conditionnement (Lot 3-C, §10) : `count_packaging_id`, `count_packaging_name`, `count_packaging_conversion`, `count_packaging_quantity`, `count_unit_quantity` |

Quantités `NUMERIC(18,3)`, CMUP `NUMERIC(18,4)`, montants `NUMERIC(18,2)` — `Decimal` côté
serveur, chaînes dans l'API. Contraintes en base : FK composites `(tenant_id, …)` vers le site,
l'article et l'inventaire ; quantités ≥ 0 ; écart figé cohérent
(`quantity_variance = quantity_physical − stock_theoretical_at_validation`) ; dates obligatoires
selon le statut ; motif obligatoire pour une annulation.

## 3. Types

- **Complet (`FULL`)** : tous les articles **actifs gérés sur le site** (niveau de stock
  existant, règle des niveaux de la Phase 2.2). La liste est générée par le serveur à la
  création, puis **recalée au démarrage du comptage** (nouveaux articles gérés ajoutés,
  articles désactivés retirés). Elle n'est pas modifiable à la main.
- **Ciblé (`TARGETED`)** : articles choisis (recherche serveur `GET /inventories/candidates`,
  jamais tout le catalogue). Un article actif jamais géré sur le site est accepté (stock
  théorique 0) : un excédent constaté crée alors son niveau de stock.

Règles communes : article actif, du tenant, une seule fois par inventaire ; un article ne
figure que dans **un seul inventaire en cours par site** (`article_in_open_inventory`).

## 4. Cycle de vie

```text
DRAFT ──start──▶ COUNTING ──complete-counting──▶ READY_TO_VALIDATE ──validate──▶ VALIDATED
  │                 ▲  │                              │
  │                 │  └──────── reopen-counting ◀────┘
  └──────── cancel (DRAFT, COUNTING, READY_TO_VALIDATE) ──────────▶ CANCELLED
```

Transitions centralisées (`TRANSITIONS` dans le service) ; toute autre transition →
`409 inventory_invalid_transition` (ex. DRAFT → VALIDATED, VALIDATED → CANCELLED). Un inventaire
**validé est immuable** (ni modification, ni annulation, ni suppression) : une erreur se corrige
par un nouvel inventaire.

| Étape | Effet |
|---|---|
| Brouillon | Site, type, articles (ciblé : ajout / retrait), commentaire. Aucun effet sur le stock. |
| Démarrage | Recalage de la liste (complet) ; **stock théorique initial** relevé pour chaque ligne. |
| Comptage | Saisie progressive (`PATCH /inventories/{id}/lines`, par lots) ; `null` efface. Progression « Articles comptés : n / N ». |
| Fin du comptage | Toutes les lignes comptées (`inventory_not_fully_counted` sinon). Reprise possible. |
| Validation | Voir §5. |
| Annulation | Motif obligatoire (5–500 caractères) ; aucun effet sur le stock. |

## 5. Validation (transactionnelle, atomique, idempotente)

1. Verrou de l'inventaire (`SELECT … FOR UPDATE`) et contrôle du statut `READY_TO_VALIDATE`.
2. Contrôle du comptage complet et des quantités (≥ 0, aussi garanti en base).
3. `StockService.lock_levels` : verrou des niveaux (site, articles) dans **l'ordre global** du
   moteur de stock (celui des ventes, entrées, sorties et transferts : pas d'interblocage) ;
   relecture du stock courant et du CMUP.
4. Par ligne : `stock_theoretical_at_validation`, `quantity_variance`, `unit_cost` (CMUP courant),
   `adjustment_value = écart × CMUP` (arrondi au centime).
5. `StockService.apply` : un mouvement `ADJUSTMENT` par écart non nul (aucun mouvement sans
   écart), contrôle global du stock négatif avant toute écriture.
6. Statut `VALIDATED`, audit `inventory.validated` ; commit par l'endpoint.

Toute erreur annule tout (aucun mouvement, aucun niveau, aucun statut modifié). Deux validations
simultanées : la seconde attend le verrou puis reçoit `409 inventory_invalid_transition` ; aucun
double mouvement (garde supplémentaire : unicité `(ligne source, type, site)` des mouvements).

## 6. Mouvements et CMUP

- Type **`ADJUSTMENT`** (réservé depuis la Phase 2.2), quantité signée : + excédent, − manquant ;
  `source_type = 'inventory_count'`, `source_id` = inventaire, `source_line_id` = ligne,
  `source_number = INV-…` (le journal des mouvements l'affiche et permet de le rechercher).
- **Excédent** : valorisé au **CMUP courant** ; le CMUP n'est **pas recalculé** (aucun prix
  saisi, `ADJUSTMENT` ne fait pas partie des types d'entrée à coût d'acquisition).
- **Manquant** : sortie au CMUP courant ; CMUP inchangé.
- Stock jamais négatif : quantité physique ≥ 0 ⇒ stock final ≥ 0 ; contrôle du moteur et
  contrainte en base en plus.
- Pas de nouveau motif : type + source identifient l'ajustement sans ambiguïté.

## 7. Sécurité

| Permission | Nature | Administrateur | Gestionnaire | Vendeur | Consultant |
|---|---|---|---|---|---|
| `inventory_count.inventory.view` | lecture | ✅ | ✅ | ✅ | ✅ |
| `inventory_count.inventory.create` | écriture | ✅ | ✅ | — | — |
| `inventory_count.inventory.update` | écriture | ✅ | ✅ | — | — |
| `inventory_count.inventory.count` | écriture | ✅ | ✅ | — | — |
| `inventory_count.inventory.validate` | écriture | ✅ | ✅ | — | — |
| `inventory_count.inventory.cancel` | écriture | ✅ | ✅ | — | — |

`count` couvre démarrer, saisir, terminer et reprendre le comptage. Les rôles personnalisés
reçoivent chaque permission individuellement (ex. un « compteur » : `view` + `count`).

- **Module** : routes protégées par `require_module("inventory_count")` (`403 module_unavailable`).
- **Abonnement** : aucune restriction de plan (STANDARD et ENTREPRISE) ; la politique de statut
  s'applique telle quelle — expiré : consultation seule (`403 subscription_restricted`).
- **Sites** : site de l'opération = site sélectionné ou site accessible au membre
  (`site_access_denied`) ; un inventaire d'un site non accessible est introuvable (`404`).
- **Tenant / RLS** : `ENABLE` + `FORCE ROW LEVEL SECURITY`, politique `tenant_isolation` ;
  droits minimaux (inventaires : `SELECT, INSERT, UPDATE` ; lignes : + `DELETE` pour le
  brouillon) ; FK composites : aucune référence croisée entre tenants, même hors application.

## 8. Audit

`inventory.created`, `inventory.updated`, `inventory.started`, `inventory.counted` (changements
de quantités : référence, avant, après), `inventory.count_completed`,
`inventory.counting_reopened`, `inventory.validated` (lignes, excédents, manquants, sans écart,
mouvements, valeurs), `inventory.cancelled` (motif). Chaque entrée porte tenant, utilisateur,
site, horodatage, numéro de l'inventaire.

## 9. Performance

Aucune requête par article : lignes créées par insertion groupée, instantanés et relectures par
requêtes ensemblistes, lignes paginées côté serveur (`GET /inventories/{id}/lines` : recherche,
filtres, tri), résumé calculé par une requête d'agrégation, verrous des niveaux en une requête.
L'interface ne charge jamais tout le catalogue ni toutes les lignes d'un inventaire complet.

## 10. Comptage en conditionnement (Lot 3-C, [ADR-0041](../adr/0041-presentations-operations-de-stock.md))

Une ligne se compte en unité de base (`quantity_physical`) **ou** dans un conditionnement actif
de l'article : `packaging_id`, `packaging_quantity` (conditionnements comptés) et
`unit_quantity` (unités de base en vrac, facultatif). Le serveur calcule la quantité physique :
**8 cartons de 24 + 5 bouteilles = 197** ; l'écart reste « physique − stock courant relu à la
validation » (§1). Instantané du comptage sur la ligne (`count_packaging_*`,
`count_unit_quantity`, contrainte de cohérence en base). Article entier : quantités saisies
entières (`422 quantity_not_whole`). À la validation, le conditionnement est relu sous verrou
partagé : désactivé → `422 packaging_inactive` (recompter la ligne), conversion modifiée →
`409 packaging_conversion_changed`. Un comptage fige la conversion du conditionnement
(`packaging_in_use`). L'interface propose la présentation par ligne et affiche l'équivalence
(« = 197 bouteille ») ; l'ajustement (`ADJUSTMENT`) est en unité de base, sans présentation.

## 11. Scan (Lot 3-D, [ADR-0042](../adr/0042-codes-barres-multiples.md))

Pendant le comptage, le champ « Scanner un code-barres » résout le code EXACT (article en unité
de base ou conditionnement) ; la liste se restreint à la ligne de l'article (`article_id`), la
présentation est présélectionnée et le champ de quantité prend le focus. **La quantité comptée
n'est jamais devinée** : si la présentation change, la saisie repart à vide. Article absent de
l'inventaire : signalé, rien n'est compté.

## 12. Emplacements (Lot 3-F, [ADR-0044](../adr/0044-emplacements-par-site.md))

Chaque ligne affiche l'emplacement COURANT de l'article sur le site de l'inventaire (« Non rangé »
sinon) ; le tri `location` ordonne le parcours de comptage (non rangés en fin). Aucun instantané :
l'emplacement n'influence ni le stock théorique, ni l'écart, ni la validation.

## 13. Hors périmètre (V1)

Application mobile native (scan : §11), import / export Excel, comptage multi-équipe,
double comptage, circuit d'approbation, sessions de comptage simultanées sur un même article,
analyses avancées.
