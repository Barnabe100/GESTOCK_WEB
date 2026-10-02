# ADR-0045 — Lots et péremption : stock et réception

- **Statut** : Acceptée — Lot 3-G (décisions D1 à D20 et P1-b) livré et validé (état de
  référence `4fd303f`) ; Lot 3-H (décisions H-D1 à H-D18, section « Lot 3-H ») **validé sur
  le plan des décisions, implémentation non commencée** ; P1-b toujours active
- **Date** : 2026-10-02
- **Prolonge** : [ADR-0019](0019-inventaires.md) (inventaires),
  [ADR-0028](0028-fuseau-horaire-du-tenant.md) (dates métier dans le fuseau du tenant),
  [ADR-0039](0039-catalogue-stock-gere-prix-couts.md) (articles gérés en stock, coûts),
  [ADR-0041](0041-presentations-operations-de-stock.md) (présentations dans le stock),
  [ADR-0044](0044-emplacements-par-site.md) (FK composites contre les affectations croisées)

## Contexte

Le stock est tenu par **(site, article)** dans `stock_levels` (quantité, CMUP par site), modifié
uniquement par `StockService` (STK-01 à STK-09). Les réceptions (`stock_entries`, natures
`PURCHASE` et `INITIAL_STOCK`) suivent le cycle brouillon → validée → annulée (ENT-01 à ENT-09),
avec un mouvement `ENTRY` par ligne, le CMUP recalculé à la validation et une annulation par
mouvements inverses refusée si un stock devenait négatif (ENT-08).

L'audit préalable du Lot 3-G (2026-10-02, état de référence `366332d` / `56635ac`, sans
modification du dépôt) a établi que :

- **aucune notion de lot ni de péremption n'existe** dans le Web (modèles, migrations jusqu'à
  0033, API, interface, tests) ; les seules « expirations » du code concernent les abonnements,
  les licences et les sessions ;
- le Desktop (`GESTOK_ENTREP`, commit `a05ca9d`) n'en a jamais eu (`EntreeLigne` : article,
  quantité, prix, montant) : aucune règle métier héritée à reprendre ;
- l'ancrage « niveaux de stock par lot optionnels, derrière le module `stock.lots` »
  (ARCHITECTURE §10) n'a jamais été déclaré (ni `planned.py`, ni `plans.toml`) ;
- trois contraintes interdisent aujourd'hui toute ventilation par lot : une ligne par
  (document, article, présentation) sur les entrées, sorties et transferts (index
  `uq_*_article_base` / `uq_*_packaging`, contrôle `duplicate_article_line`), un mouvement d'un
  type donné par (ligne source, site) (`uq_stock_movements_line_type_site`), une ligne par article
  et par inventaire.

## Problème

Certaines activités (alimentaire, pharmacie, cosmétique, pièces) doivent savoir quels lots sont
en stock, sur quel site, en quelle quantité et jusqu'à quelle date ils sont utilisables — sans
remettre en cause la tenue du stock par (site, article), le CMUP ni l'historique existant, et
sans introduire de comptabilité de stock par lot.

## Décisions (D1 à D20 validées par TechNova le 2026-10-02)

1. **D1 — Modèle (variante B)** : un article suivi par lot possède un **solde par lot et par
   site** dès le Lot 3-G. Le stock `(site, article)` reste la référence de contrôle (stock
   jamais négatif) ; les soldes par lot en sont une **ventilation**. Invariant :
   **Σ quantités des lots = quantité du stock (site, article)**. Les flux sortants consommeront
   les lots (Lot 3-H). Aucune variante où les lots ont un solde sans être consommés : le suivi
   par lot n'est activable en exploitation qu'avec la consommation (décision P1-b ci-dessous).
2. **D2 — Unicité** : numéro de lot unique par **(tenant, article)**, comparé **sans
   distinction de casse** ; espaces de bord retirés selon la convention existante (schémas
   `strip_whitespace`, contrôle `btrim(…) = …` en base comme `stock_locations`). Un même numéro
   peut exister pour deux articles différents.
3. **D3 — Fournisseur** : le lot appartient à l'**article**, pas au fournisseur. Le fournisseur
   reste une information historique de la réception ; le même numéro chez deux fournisseurs
   désigne le même lot pour un même article (sous réserve de D4).
4. **D4 — Lot déjà connu** : même article + même numéro + même péremption → la quantité
   s'ajoute au lot existant ; même article + même numéro + **péremption différente** →
   **réception refusée**. Jamais deux lots de même numéro pour un article.
5. **D5 — Informations** : numéro **obligatoire** ; date de péremption **obligatoire si
   l'article est suivi en péremption** ; date de fabrication **facultative**. Aucun lot sans
   numéro. Suivi par lot et suivi de péremption sont **deux réglages distincts** de l'article (un
   article peut être suivi par lot sans péremption ; le suivi de péremption suppose le suivi
   par lot).
6. **D6 — Activation du suivi** : seulement si le stock de l'article est **nul sur tous les
   sites** (même logique que le passage `stock_managed`, port `catalog.stock_port`). Jamais de
   stock existant « sans lot ».
7. **D7 — Désactivation du suivi** : seulement si **tous les soldes par lot sont à zéro**.
   Invariant permanent : Σ lots = stock (site, article) ; aucune « quantité sans lot ».
8. **D8 — Saisie** : pour un article suivi, le lot est **obligatoire dès l'enregistrement du
   brouillon** ; ses données restent modifiables tant que la réception est en brouillon ; la
   validation **revalide tout** côté serveur.
9. **D9 — Stock initial** : `INITIAL_STOCK` suit exactement les règles des achats (lot
   obligatoire, péremption selon l'article, création / résolution du lot à la validation, solde
   par lot, mouvement associé).
10. **D10 — Annulation d'une réception** : refusée si elle rendait **le solde d'un lot**
    négatif (lot déjà consommé en tout ou partie) — ENT-08 étendu au niveau du lot ; refus
    total, comme aujourd'hui.
11. **D11 — Correction après validation** : numéro et dates d'un lot validé **figés** ; aucune
    modification ordinaire dans 3-G. Une éventuelle procédure de correction administrative
    sera définie plus tard (permission, motif obligatoire, audit avant / après).
12. **D12 — Valorisation (C1)** : CMUP **inchangé**, calculé par (tenant, site, article) ; le
    lot n'a **aucun coût de valorisation** ; valeur = quantité (site, article) × CMUP (site,
    article) ; sorties au CMUP du site ; le coût d'achat reçu dans un lot reste une information
    historique de la réception, soumise aux règles de visibilité des coûts (`cost_view`).
13. **D13 — Activation commerciale** : le suivi des lots est une capacité du module `stock` ;
    **aucune fonctionnalité de plan `stock.lots`**, plans STANDARD / ENTREPRISE inchangés.
14. **D14 — Permissions** : réutiliser l'existant — consultation `stock.level.view` (et
    `stock.entry.view` / `stock.movement.view` selon l'écran), saisie par les permissions des
    entrées (`stock.entry.create|update|validate|cancel`), réglage de l'article par
    `catalog.article.update`. `stock.lot.view` / `stock.lot.manage` seulement si
    l'implémentation démontre un besoin réel de séparation des privilèges.
15. **D15 — Visibilité** : référentiel des lots **au niveau du tenant**, mais un membre ne voit
    que les données des sites auxquels il a accès (`filter_site_ids`, `operation_site`, contrôles
    de site, RLS). La visibilité d'un lot est filtrée par les sites auxquels il est effectivement
    associé (solde ou réception sur un site visible) ; un membre limité à un site ne voit jamais
    le stock d'un autre site.
16. **D16 — Seuil « bientôt périmé »** : réglage **du tenant**, avec une valeur par défaut ;
    trois états calculés : **normal**, **bientôt périmé**, **périmé**. Aucun réglage par article
    dans 3-G.
17. **D17 — Dates** : péremption antérieure à aujourd'hui **acceptée** (lot immédiatement
    « périmé ») ; fabrication postérieure à la péremption **refusée** ; « aujourd'hui » =
    `tenant_today` (jamais la date du serveur ni du navigateur).
18. **D18 — Affichage dans 3-G** : page des lots, fiche lot, fiche article, réception,
    mouvements (lorsque le lot est présent). Alertes globales et tableau de bord de péremption :
    Lot 3-I.
19. **D19 — Conditionnements** : le lot est indépendant de la présentation ; un même lot peut être
    reçu dans plusieurs présentations (L001 : 2 Carton 24 puis 10 bouteilles) ; quantité du lot
    **toujours en unité de base** ; règles 3-C inchangées.
20. **D20 — Découpage** : voir « Séparation 3-G / 3-H / 3-I » et la décision P1-b. Images des
    articles reportées ; pas de Lot 3-J pour l'instant.

## Modèle de données conceptuel

Conceptuel seulement : noms, types et index définitifs fixés à l'implémentation (une migration,
RLS et droits ajoutés à la main).

| Élément | Contenu conceptuel |
|---|---|
| `catalog_articles` | deux réglages : **suivi par lot** (défaut non) et **suivi de péremption** (défaut non ; suppose le suivi par lot — contrainte en base) |
| `stock_lots` (nouvelle) | `tenant_id`, `article_id`, numéro (texte borné, sans espaces de bord), date de péremption (`date`, nullable), date de fabrication (`date`, nullable), horodatage et auteur de création ; unique `(tenant_id, article_id, lower(numéro))` ; unique `(tenant_id, article_id, id)` (cible des FK composites) ; `CHECK fabrication ≤ péremption` ; FK `(tenant_id, article_id)` → articles ; **immuable** (D11) |
| `stock_lot_levels` (nouvelle) | `tenant_id`, `site_id`, `article_id`, `lot_id`, `quantity` (`NUMERIC(18,3)`, `≥ 0`, unité de base) ; unique `(tenant_id, site_id, lot_id)` ; FK `(tenant_id, site_id)` → sites ; FK composite `(tenant_id, article_id, lot_id)` → `stock_lots` ; **aucun coût** (D12) |
| `stock_entry_lines` | saisie du lot du brouillon (numéro, péremption, fabrication) ; lot résolu ou créé **à la validation** (`lot_id`, FK composite `(tenant_id, article_id, lot_id)` : le lot d'un autre article est inaffectable) ; unicité des lignes revue pour admettre plusieurs lots d'un même article et d'une même présentation dans une réception |
| `stock_movements` | `lot_id` facultatif (FK composite `(tenant_id, article_id, lot_id)`) sur les mouvements d'entrée et d'annulation de réception ; table toujours append-only (`SELECT, INSERT`) ; numéro et dates figés (D11) : aucun instantané nécessaire |
| Réglage du tenant | seuil « bientôt périmé » en jours (D16) ; emplacement de stockage (colonne du tenant ou paramètre du module stock) fixé à l'implémentation |

État de péremption (calculé à la lecture, jamais stocké) : sans date → aucun état ; date
antérieure à `tenant_today` → **périmé** ; date ≤ `tenant_today` + seuil → **bientôt périmé** ;
sinon **normal**.

## Règles métier

- **RL-01** Réglages de l'article : suivi par lot activable seulement à stock nul sur tous les
  sites (D6) ; désactivable seulement si tous les soldes par lot sont nuls (D7) ; refusés pour
  un article non géré en stock (3-A) ; changements audités (avant / après), sous verrou de
  l'article comme `stock_managed`.
- **RL-02** Réception d'un article suivi : numéro obligatoire dès le brouillon (D8), péremption
  obligatoire si suivi de péremption (D5), fabrication ≤ péremption (D17) ; péremption passée
  acceptée (D17).
- **RL-03** Validation : revalidation complète ; résolution du lot par (article, numéro sans
  casse) ; lot existant de péremption différente → refus (D4) ; sinon création du lot ; solde
  (site, lot) augmenté de la quantité de base de la ligne ; mouvement `ENTRY` portant le lot ;
  CMUP recalculé comme aujourd'hui (D12). Tout ou rien.
- **RL-04** Annulation : mouvements inverses sur le même lot ; refus total si le stock (site,
  article) **ou** le solde d'un lot devenait négatif (ENT-08, D10).
- **RL-05** Invariant : pour un article suivi, Σ soldes des lots du site = stock (site, article)
  après chaque opération ; tenu dans la même transaction et sous les mêmes verrous que le niveau
  (ordre global site, article, puis lot).
- **RL-06** Présentations : lot indépendant de la présentation (D19) ; quantité du lot en unité
  de base (`base_quantity`) ; règles 3-C (conversion figée, quantités entières) inchangées.
- **RL-07** Article non suivi : comportement strictement inchangé (aucun lot accepté ni exigé).

## Séparation 3-G / 3-H / 3-I

| Lot | Contenu |
|---|---|
| **3-G** (cette ADR) | **suivi par lot non activable en exploitation (P1-b)** ; référentiel des lots ; réglages de suivi de l'article ; solde par lot et par site ; réceptions `PURCHASE` et `INITIAL_STOCK` ; annulation de réception ; traçabilité des mouvements d'entrée ; consultation (page des lots, fiche lot, fiche article, réception, mouvements) ; calcul de l'état de péremption |
| **3-H** | **ouverture de l'activation du suivi par lot, livrée avec** la consommation des lots par les ventes, le POS, les sorties, les transferts et les inventaires (comptage par lot) ; choix automatique ou manuel du lot ; annulations / retours avec le lot ; comportement face à un lot périmé ; lot éventuel sur le reçu — **précisé par la section « Lot 3-H » (H-D1 à H-D18, découpage 3-H-A / 3-H-B ; retours et remboursements finalement hors 3-H, H-D13)** |
| **3-I** | alertes et tableau de bord de péremption ; notifications ; traitement éventuel des lots périmés ; rapports de péremption |

### Décision P1 — activation du suivi fermée jusqu'à 3-H (P1-b, validée le 2026-10-02)

D1 impose Σ lots = stock et interdit des soldes non consommés ; D20 place la consommation dans
3-H. Livré seul, un article suivi verrait son stock (site, article) diminuer par une vente, une
sortie, un transfert ou un ajustement d'inventaire sans qu'aucun lot ne diminue. La
contradiction est levée ainsi (options P1-a « refuser les flux sortants des articles suivis » et
P1-c « consommation automatique dès 3-G » écartées) :

- **3-G implémente et teste** le modèle de suivi par lot, le référentiel des lots, les soldes par
  lot et par site, les réceptions `PURCHASE` et `INITIAL_STOCK` par lot, leur annulation, la
  traçabilité des mouvements d'entrée, la consultation et l'état de péremption.
- **Le suivi par lot n'est pas activable en exploitation** tant que la consommation des lots
  (3-H) n'est pas disponible : le **serveur** refuse le passage d'un article au suivi par lot
  (et donc au suivi de péremption) avec un code d'erreur stable (nom fixé à l'implémentation) ;
  le refus fait foi côté backend, jamais décidé par l'interface.
- **Cette fermeture est un état du produit**, levé uniquement par la livraison validée de 3-H
  (changement de code) : ni paramètre du client, ni réglage du tenant, ni fonctionnalité de plan
  (D13), ni action de la console TechNova, ni variable modifiable en exploitation. Les tests de
  3-G l'ouvrent explicitement par un dispositif réservé aux tests (mécanisme fixé à
  l'implémentation).
- **En exploitation**, aucun article n'est donc suivi par lot : aucun lot n'est demandé, créé ni
  consommé, et l'invariant Σ lots = stock (site, article) est respecté (aucun solde de lot).
- **Articles non suivis** (tous, en exploitation) : réceptions, ventes, POS, sorties, transferts
  et inventaires **strictement inchangés** ; aucun lot demandé ; aucune opération bloquée.
- **Aucune consommation automatique** des lots dans 3-G ; aucun élément de 3-H implémenté dans
  le cadre de 3-G.
- **Interface** : l'activation du suivi n'est pas proposée tant que la fermeture est en vigueur
  (état fourni par le serveur) ; les écrans de lots n'affichent que ce que le serveur renvoie.
- **3-H** ouvrira l'activation dans la même livraison que la consommation des lots (ventes, POS,
  sorties, transferts, inventaires), après validation.

## Sécurité / multi-tenant

- Nouvelles tables tenant-scoped (`TenantScopedMixin`), RLS `ENABLE` + `FORCE`, politique et
  droits minimaux dans la migration, jamais de `BYPASSRLS` ; tests d'isolation SQL **et** API.
- FK composites `(tenant_id, …)` partout ; `(tenant_id, article_id, lot_id)` empêche en base de
  rattacher le lot d'un autre article (même principe que 3-F pour les sites).
- Droits du rôle applicatif : `stock_lots` `SELECT, INSERT` (immuable, D11 ; aucune
  suppression) ; `stock_lot_levels` `SELECT, INSERT, UPDATE (quantity, updated_at)` ;
  `stock_movements` inchangé (`SELECT, INSERT`).
- Sites : lecture limitée aux sites visibles (`filter_site_ids`), écriture sur un site accessible
  et autorisé par son abonnement (`operation_site`, `ensure_site_allows`) ; un lot sans lien avec
  un site visible est introuvable pour le membre (D15).
- Permissions existantes (D14) ; aucune fonctionnalité de plan (D13).
- Audit : réglages de l'article (avant / après) ; lots créés ou alimentés dans l'audit de
  validation de la réception ; annulation (motif). Aucune donnée de lot hors du tenant.
- Coûts : tout champ de coût exposé avec un lot (coût reçu, historique) déclaré dans
  `STOCK_COST_FIELDS` (`cost_masking_route`).
- Dates : `tenant_today` (ADR-0028) pour tout état de péremption.

## CMUP / valorisation

Option **C1** (D12) : la formule STK-05, sa portée (site, article), sa précision (4 décimales)
et la règle STK-06 (aucun recalcul sur sorties, ajustements, annulations) restent **inchangées**.
Le lot ne porte qu'une quantité. Une réception d'article suivi produit exactement le même CMUP
que la même réception sans lot ; une annulation de réception ne modifie pas le CMUP. L'option
C2 (coût par lot, identification spécifique) est écartée : elle changerait la méthode de
valorisation, la valeur des sorties, transferts et ajustements, les marges et rendrait
l'historique non reconstructible (STK-06).

## Conséquences

- `StockService` reste le seul point d'écriture : il tiendra aussi les soldes par lot (verrou,
  ordre global, contrôle de non-négativité par lot) lors de l'implémentation de 3-G — sans
  changement du calcul du CMUP ni du contrôle par (site, article).
- Une migration (0034 prévue) : réglages de l'article, `stock_lots`, `stock_lot_levels`, lot sur
  les lignes d'entrée et les mouvements, unicité des lignes d'entrée revue (limitée aux
  entrées ; sorties et transferts inchangés en 3-G). Son retour arrière devra refuser ou
  documenter le cas de réceptions contenant plusieurs lignes d'un même article.
- API (à préciser) : consultation des lots (liste filtrable par article, site, état de
  péremption ; fiche lot avec réceptions, mouvements et soldes par site visible) ; lignes
  d'entrée et mouvements enrichis ; réglages de l'article ; réglage du seuil du tenant.
- Interface : réglages sur la fiche article, saisie du lot sur la réception (y compris sur
  mobile), page et fiche des lots, lots sur la fiche article (par site), colonne lot dans les
  mouvements ; textes i18n et codes d'erreur traduits.
- P1-b : le code de 3-G est livré sans être exploitable par les entreprises ; sa mise en
  service opérationnelle dépend de la livraison de 3-H (qui lève la fermeture de l'activation).
- L'ancrage « module `stock.lots` » d'ARCHITECTURE §10 est remplacé par cette ADR (capacité du
  module `stock`, sans fonctionnalité de plan).

## Risques

- Ouverture de l'activation avant la livraison de 3-H (dispositif de test actif hors des
  tests, contournement de la fermeture) : l'invariant Σ lots = stock serait rompu par les flux
  sortants ; la fermeture doit être contrôlée par le serveur et couverte par des tests (P1-b).
- Régression du cœur `StockService` (verrous, ordre, interblocages) qui sert aussi ventes et POS.
- Contraintes d'unicité des lignes : `check_articles` est partagé avec les sorties et les
  transferts ; modification à limiter aux entrées.
- Dates comparées hors du fuseau du tenant ; casse et espaces du numéro de lot.
- Formulaire de réception plus dense (débordement sur mobile, déjà rencontré au 3-F).
- Retour arrière de la migration avec des données multi-lots.

## Tests attendus

- **Fermeture P1-b** : en configuration d'exploitation, activation du suivi par lot (et de
  péremption) refusée par l'API avec le code dédié, pour tout rôle ; aucune autre voie
  (paramètre du client, réglage du tenant, plan) ne l'ouvre ; l'interface ne propose pas
  l'activation. Les scénarios suivants s'exécutent avec la fermeture levée par le dispositif de
  test.
- **Réglages** : activation refusée avec du stock sur un site, acceptée à stock nul ;
  désactivation refusée avec un solde de lot non nul ; refus pour un article non géré ; suivi de
  péremption sans suivi par lot refusé ; audit.
- **Réception** : lot obligatoire dès le brouillon ; péremption obligatoire selon l'article ;
  fabrication > péremption refusée ; péremption passée acceptée (état « périmé ») ; création du
  lot à la validation ; même lot reçu de nouveau (même péremption : cumul ; autre péremption :
  refus) ; numéro comparé sans casse et sans espaces de bord ; même numéro pour deux articles ;
  plusieurs lots d'un article et un lot en plusieurs présentations (quantité en unité de base) ;
  `INITIAL_STOCK` identique ; revalidation à la validation.
- **Annulation** : soldes et stock diminués ; refus total si un lot devenait négatif.
- **Invariant** : Σ lots = stock après chaque opération de 3-G, y compris en concurrence (deux
  réceptions simultanées du même lot, sans interblocage).
- **CMUP** : valeurs identiques avec et sans lot (réception, annulation).
- **Péremption** : états normal / bientôt périmé / périmé selon le seuil du tenant et
  `tenant_today` (fuseau différent de celui du serveur, bornes du jour).
- **Sécurité** : isolation RLS SQL et API des nouvelles tables ; FK composite (lot d'un autre
  article ou tenant) ; membre limité à un site (lots et soldes d'un autre site invisibles) ;
  permissions ; coûts absents sans `cost_view`.
- **Non-régression** : articles non suivis strictement inchangés — aucune opération bloquée ni
  modifiée, aucun lot demandé ; suites stock, ventes, POS, transferts, inventaires.
- **Migration** : montée / descente / montée, `alembic check`.
- **Interface et E2E** : saisie de réception avec lots (desktop et mobile), page et fiche des
  lots, états de péremption, fiche article ; suite E2E complète.

## Hors périmètre

Tout le contenu de 3-H et 3-I ; coût par lot ; numéros de série ; codes-barres, étiquettes ou
GS1 de lot ; quarantaine, blocage qualité, rappels de lots ; lots par emplacement (3-F reste par
article et site) ; FIFO / FEFO comptable ; correction administrative des lots (D11) ; exports ;
réglage du seuil par article ; fonctionnalité de plan ; images des articles ; modification du
Desktop.

## Implémentation (Lot 3-G)

Choix techniques faits pendant l'implémentation, dans le cadre des décisions ci-dessus :

- **Fermeture P1-b** : constante du code `LOT_TRACKING_AVAILABLE = False`
  (`app/modules/catalog/lot_tracking.py`), lue à chaque activation (création ou modification
  d'article : `422 lot_tracking_unavailable`) et exposée en lecture par
  `GET /catalog/lot-tracking` (l'interface ne propose alors pas le réglage). Aucun module de
  l'application ne la modifie (test statique). **Mécanismes réservés aux tests** : la fixture
  pytest `lot_tracking_open` remplace la constante dans le processus de test (`monkeypatch`) ;
  les tests de bout en bout marquent un article « suivi par lot » avec le rôle propriétaire de
  la base (`ownerSql`), comme les autres données que seule l'administration peut fixer. Le
  Lot 3-H lèvera la fermeture en passant la constante à `True`.
- **Seuil par défaut** : 30 jours (`DEFAULT_EXPIRY_WARNING_DAYS`), réglable de 0 à 365 par le
  tenant (`stock_settings`, ligne absente = défaut). Modification : `stock.threshold.manage`
  (permission existante des seuils de stock) **et** accès à tous les sites (réglage commun à
  l'entreprise : `403 tenant_wide_access_required` pour un rôle limité à un site) ; auditée.
- **États** : périmé si la date est antérieure à `tenant_today` ; bientôt périmé d'aujourd'hui
  (inclus) à aujourd'hui + seuil ; normal au-delà ; « sans péremption » sans date.
- **Lot connu** : péremption identique exigée (D4) ; une date de fabrication renseignée doit
  aussi être identique à celle du lot (`lot_manufacturing_mismatch`) — absente, elle est
  acceptée ; le lot reste figé. Un même lot saisi sur plusieurs lignes porte les mêmes dates.
- **Péremption sur un article suivi sans péremption** : facultative (obligatoire seulement si
  l'article est suivi en péremption, D5).
- **Réglages** : suivi par lot et suivi de péremption modifiables seulement à stock nul sur tous
  les sites et soldes de lots nuls (D6, D7) ; verrou exclusif de l'article (une validation de
  réception lit les réglages sous verrou partagé).
- **Visibilité** (D15) : un lot est visible par les sites où il a un solde (même nul après une
  annulation) parmi les sites visibles du membre ; ailleurs `404 stock_lot_not_found`.
- **Fiche lot** : réceptions et mouvements servis par les listes existantes filtrées par
  `lot_id` (`GET /stock/entries`, `GET /stock/movements`), avec leurs propres permissions.
- **Audit** : `stock_lot.created` (lot créé à la validation), détail des lots reçus dans
  `stock_entry.validated`, réglages de l'article dans `article.updated`,
  `stock_settings.updated`.
- **Migration 0034** : retour arrière refusé si des lots existent (traçabilité).

## Lot 3-H — consommation des lots (décisions validées par TechNova le 2026-10-02)

Les décisions de cette section **complètent** celles du 3-G (D1 à D20, P1-b), qui restent
inchangées. Pour éviter toute confusion de numérotation, elles sont notées **H-D1 à H-D18**.
Elles font suite à l'audit préalable du 3-H (2026-10-02, état `4fd303f`, aucun fichier
modifié). **Aucune implémentation n'est commencée ; P1-b reste active.**

### Décisions

1. **H-D1 — Mode de consommation par flux (variante D)** : ventes et POS = consommation
   **automatique FEFO** (le vendeur ne choisit pas de lot lors d'une vente normale ; le FEFO est
   une règle interne de gestion du stock) ; sorties de stock = **choix manuel** du lot ;
   transferts = **choix manuel** du lot ; inventaires = **comptage par lot**.
2. **H-D2 — Plusieurs lots par ligne** : oui. La ligne commerciale reste unique (Eau, 30) ; le
   stock produit un mouvement par lot (`SALE` lot A −20, `SALE` lot B −10) ; l'historique
   restitue exactement cette répartition.
3. **H-D3 — Ordre FEFO** (article suivi en péremption) : lots **non périmés** par date de
   péremption croissante, puis date de création du lot, puis numéro ; lots sans date de
   péremption après les lots datés ; un lot périmé n'est **jamais** choisi automatiquement.
   Ordre déterministe et reproductible.
4. **H-D4 — Article suivi par lot sans suivi de péremption** : FIFO — date de création du lot,
   puis numéro.
5. **H-D5 — Lots périmés** : vente / POS **bloqué** (jamais consommé par le FEFO) ; sortie de
   stock **autorisée** (destruction, mise au rebut) ; transfert **bloqué** par défaut ;
   inventaire : le lot périmé reste visible et comptable.
6. **H-D6 — Dérogation à la vente d'un lot périmé** : impossible avec les permissions normales ;
   **permission spécifique**, **motif obligatoire**, utilisateur et motif audités, aucune
   dérogation silencieuse. Le nom exact est proposé au plan technique après vérification des
   conventions RBAC (voir « Vérifications techniques », point 1).
7. **H-D7 — Modèle de traçabilité M1** : le mouvement de stock porte `lot_id` ; **un mouvement
   = une allocation de quantité sur un lot** ; une ligne répartie sur plusieurs lots produit
   plusieurs mouvements ; l'annulation inverse **chaque mouvement d'origine**. Le journal des
   mouvements est la **source de vérité historique** de la consommation des lots. **Aucune**
   table générique `stock_lot_allocations`.
8. **H-D8 — Choix saisis sur les brouillons** : conservés dans le brouillon pour les flux à
   choix manuel (sorties, transferts, et tout autre flux qui l'exigerait) ; **revalidés
   intégralement** par le serveur à la validation ; jamais source historique (le journal fait
   foi). Structures exactes fixées au plan technique.
9. **H-D9 — POS et reçu** : FEFO automatique au POS, sans sélection imposée au vendeur ; le lot
   reste consultable dans le détail et l'historique de la vente ; le **reçu commercial affiche
   le lot et, si elle existe, la date de péremption** (format fixé à l'implémentation — voir
   point ouvert O-2).
10. **H-D10 — Annulation de vente** : restauration **exacte** sur les lots consommés (A +20,
    B +10), jamais sur un autre lot, même si le lot est devenu périmé entre-temps ; protégée
    contre le double crédit.
11. **H-D11 — Transferts** : l'identité du lot est conservée (même lot sur le site destination) ;
    répartition possible sur plusieurs lots (site A : X −20 / Y −20 → site B : X +20 / Y +20) ;
    lots périmés selon H-D5 ; annulation = restauration sur les lots d'origine, **refusée** si
    les soldes du site destination ne le permettent plus.
12. **H-D12 — Inventaires** : comptage **par lot** pour un article suivi ; un lot existant peut
    être compté à 0 ; un lot **découvert** peut être créé si les règles de création du 3-G sont
    respectées ; un lot **absent** du comptage doit pouvoir être identifié ; écart et ajustement
    **par lot** ; jamais d'écart global transformé en consommation FEFO ; UX desktop et mobile.
13. **H-D13 — Retours et remboursements** : **hors périmètre du 3-H** (aucun workflow de retour
    ni de remboursement dans le Web actuel).
14. **H-D14 — Garde-fou serveur** : une fois P1-b levée, **tout mouvement d'un article suivi par
    lot porte un lot** ; un mouvement sortant sans `lot_id` pour un article suivi est refusé
    par le serveur (jamais un contrôle du seul frontend) ; articles non suivis inchangés.
15. **H-D15 — Levée de P1-b** : P1-b reste active pendant 3-H-A **et** 3-H-B ; levée en
    **dernière étape** du 3-H complet, après ventes, POS, sorties, annulations, transferts,
    annulations de transferts, inventaires, tests de concurrence, multi-site, multi-tenant,
    desktop et mobile, et vérification du garde-fou H-D14. Aucun flux incomplet ne doit pouvoir
    fonctionner sur un article réellement suivi par lot.
16. **H-D16 — Présentation sur un mouvement réparti** : quantité de stock toujours en unité de
    base ; présentation cohérente avec le 3-C ; si la quantité en conditionnement d'un
    mouvement individuel n'est pas représentable proprement, elle n'est pas affichée sur ce
    mouvement ; **jamais de faux arrondi**.
17. **H-D17 — CMUP** : C1 inchangé (aucun coût par lot, valorisation au CMUP du site, aucune
    méthode comptable par lot). Pour un transfert réparti sur plusieurs lots, le CMUP est
    déterminé **une fois au niveau de la ligne / de l'opération, avant répartition**, sans
    écart d'arrondi créé par la seule répartition (implémentation exacte proposée au plan
    technique, voir point 3).
18. **H-D18 — Lots disponibles pour le POS** : point d'accès **dédié** (pas la page générale
    `/stock/lots`) : tenant, site de la vente et permissions respectés ; seulement les lots
    pertinents, avec solde disponible et péremption ; mêmes règles que le moteur FEFO du
    serveur.

### Découpage validé

| Étape | Contenu | P1-b à la fin |
|---|---|---|
| **3-H-A** — moteur, ventes, POS, sorties | modèle M1 ; adaptation de `StockService` ; consommation par lot ; FEFO / FIFO ; choix manuel pour les sorties ; ventes, POS, sorties et leurs annulations ; garde-fou H-D14 ; concurrence ; historique ; affichage des lots ; reçu ; tests desktop / mobile | **active** (aucun article réellement suivi activable en exploitation) |
| **3-H-B** — transferts, inventaires | transferts multi-lots, conservation du lot entre sites, annulation ; inventaires par lot (lots découverts, lots absents, ajustements) ; concurrence ; tests desktop / mobile ; **puis** suite complète, vérification qu'aucun flux ne produit de mouvement sans lot pour un article suivi, **levée de P1-b** | levée en **dernière étape** |

### Vérifications techniques (lecture du code à `4fd303f`)

1. **RBAC (H-D6)** : convention `module.ressource.action` avec nature déclarée ; précédent
   direct `sales.sale.credit_override` (nature écriture), accordé à aucun rôle de base hors
   Administrateur (`*`). Le POS exige `pos.terminal.use` en plus des permissions de vente.
   **Proposition** (à confirmer au plan technique) : `sales.sale.expired_lot_override`
   (écriture), Administrateur seulement par défaut, sur le modèle de `credit_override`.
2. **Brouillons (H-D8)** : les lignes de sortie (`stock_exit_lines`) et de transfert
   (`stock_transfer_lines`) sont **entièrement remplacées** à chaque enregistrement du
   brouillon (`document.lines = []` puis reconstruction). Les choix de lots devront donc être
   des **lignes enfants de la ligne** (suppression en cascade avec elle, reconstruites à partir
   de la saisie), tenant-scoped, RLS `ENABLE` + `FORCE`, FK composites `(tenant, article, lot)`.
   Les ventes ne stockent aucun choix (FEFO à la validation).
3. **CMUP (H-D17)** : `compute_average_cost` est appliqué **mouvement par mouvement** dans
   `StockService._write`, arrondi à 4 décimales à chaque fois, seulement pour `ENTRY` et
   `TRANSFER_IN`. Les sorties (`SALE`, `EXIT`, `TRANSFER_OUT`, `ADJUSTMENT`) prennent le CMUP du
   site sans le modifier : les répartir ne crée aucun écart. Seul le **`TRANSFER_IN` réparti**
   enchaînerait plusieurs recalculs arrondis. **Proposition** : calculer le CMUP destination une
   fois sur la quantité totale de la ligne, puis l'appliquer à tous les mouvements de la ligne
   (premier mouvement : CMUP avant → CMUP final ; suivants : CMUP final inchangé).
4. **Contraintes de `stock_movements` (H-D7)** : unicité
   `uq_stock_movements_line_type_site (tenant_id, source_line_id, movement_type, site_id)` —
   un seul mouvement par ligne, type et site. À étendre à `lot_id` avec `NULLS NOT DISTINCT`
   (PostgreSQL 16) : comportement identique pour les articles non suivis, un mouvement par lot
   pour les autres, double application toujours impossible. Contrainte
   `packaging_snapshot_complete` : l'instantané de présentation d'un mouvement est **tout ou
   rien** (`packaging_id`, nom, conversion, quantité) ; `packaging_quantity_consistent` exige
   `|quantité| = quantité en conditionnement × conversion`.
5. **Hypothèses « un mouvement par ligne »** à reprendre : `StockService.movements_of` et
   `_origin_movements` (dictionnaires indexés par ligne : annulation des ventes, des sorties,
   des réceptions, des transferts) ; `ExitService.validate` (`zip(lignes, mouvements)` pour figer
   le coût de la ligne) ; `TransferService.validate` (`zip(lignes, paires)`) ;
   `StockService.transfer` (paires sortie / entrée par position) ; audit de l'inventaire
   (`len(movements)` = nombre de mouvements, plus nombre d'articles).
6. **Inventaires (H-D12)** : `inventory_lines` unique `(inventory_id, article_id)` ; comptage
   par ligne en unité de base ou en conditionnement + vrac (3-C) ; théorique initial capturé au
   démarrage, écart = physique − stock courant relu à la validation ; un article dans un seul
   inventaire en cours par site (verrou consultatif). Le comptage par lot demande un **détail
   par lot sous la ligne** (théorique, physique, écart, ajustement par lot), la ligne restant
   la somme.

### Points ouverts avant le code (aucune décision prise ici)

- **O-1 — Exercice de la dérogation H-D6** : les ventes et le POS consomment en FEFO sans choix
  du vendeur, et le FEFO exclut les lots périmés. Il faut préciser **comment** une vente
  dérogatoire désigne un lot périmé (choix explicite du lot sur la ligne, ou option « inclure
  les lots périmés » avec motif) et ce qui se passe si le stock non périmé est insuffisant
  alors qu'un stock périmé existe (refus avec un code d'erreur dédié par défaut ?).
- **O-2 — Reçu (H-D9)** : le Web **n'a pas de reçu imprimé 80 mm** ; le « reçu » du POS est
  aujourd'hui un **dialogue de confirmation à l'écran** (`PosReceipt`, point d'extension
  déclaré d'un futur ticket / PDF). Le 3-H affichera le lot et la péremption dans ce reçu à
  l'écran ; un ticket imprimé 80 mm serait un périmètre nouveau.
- **O-3 — Présentation d'un mouvement réparti (H-D16)** : avec la contrainte « tout ou rien »,
  ne pas afficher la quantité en conditionnement d'un mouvement revient à **omettre tout son
  instantané de présentation** (la ligne du document le conserve) — sauf modification de la
  contrainte. Proposition : conserver l'instantané quand quantité ÷ conversion est exacte à
  3 décimales, l'omettre sinon.
- **O-4 — Choix manuel incomplet (H-D8)** : la somme des allocations doit-elle égaler la
  quantité de la ligne dès l'enregistrement du brouillon, ou seulement à la validation ?
- **O-5 — Lot absent de l'inventaire (H-D12)** : un lot ayant un solde sur le site mais non
  compté bloque-t-il la validation (comptage explicite obligatoire, y compris à 0) ou est-il
  traité comme compté à 0 ? Et comment le comptage par lot se combine-t-il avec le comptage en
  conditionnement + vrac du 3-C (par lot) ?
- **O-6 — Garde-fou H-D14 pendant 3-H-A** : proposition d'activer le garde-fou dès 3-H-A (sans
  effet en exploitation tant que P1-b est active) ; les transferts et inventaires d'articles
  suivis seraient alors refusés dans les tests jusqu'au 3-H-B.

## Références

- Audit préalable du Lot 3-G (2026-10-02, rapport de session, aucun fichier modifié) : état du
  modèle (`stock_levels`, `stock_movements`, `stock_entries`, `stock_entry_lines`, sorties,
  transferts, inventaires, conditionnements), contraintes d'unicité bloquantes, absence de lots
  dans le Web et le Desktop, analyse du CMUP (options C1 / C2), impacts sur 3-H et 3-I.
- Audit préalable du Lot 3-H (2026-10-02, état `4fd303f`, rapport de session, aucun fichier
  modifié) : flux sortants (ventes, POS, sorties, transferts, inventaires, annulations), options
  de consommation A à D, modèles M1 / M2 / M3, concurrence, retours et remboursements absents.
- [`CATALOGUE_STOCK.md`](../architecture/CATALOGUE_STOCK.md) (STK-*, ENT-*),
  [`DATA_MODEL.md`](../architecture/DATA_MODEL.md),
  [`INVENTORY.md`](../architecture/INVENTORY.md), [`SALES.md`](../architecture/SALES.md),
  [`ARCHITECTURE.md`](../architecture/ARCHITECTURE.md) §10.
