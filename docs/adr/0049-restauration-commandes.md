# ADR-0049 — Restauration : menu, moteur de commandes unique, préparation, règlement, QR

- **Statut** : Acceptée (conception, palier R0) ; **palier R1 livré** (`restaurant.menu`,
  migration 0041) ; **palier R2 en cours** (commandes et règlement, décisions D14 ; R2-A livré :
  socle de plateforme ; R2-B livré : migration 0042 et moteur T1 ; R2-C livré : prise en charge, réattribution, modèles Serveur et Préparateur ; R2-D livré : migration 0043 et règlement ; module encore planifié) — les autres modules restent planifiés
- **Date** : 2026-10-09
- **Prolonge** : [ADR-0020](0020-paiements-des-ventes.md) (paiements),
  [ADR-0022](0022-caisse.md) (caisse), [ADR-0023](0023-point-de-vente.md) (POS),
  [ADR-0030](0030-delegation-rbac.md) (délégation), [ADR-0037](0037-encaissement.md)
  (encaissement), [ADR-0045](0045-lots-et-peremption-stock-reception.md) (lots),
  [ADR-0046](0046-assortiment-par-site.md) (assortiment), [ADR-0047](0047-recu-de-vente-pos.md)
  (reçu), [ADR-0048](0048-changement-profil-site.md) (profil et modules par site, dont la
  décision ouverte d'E.1)
- **Détail** : [`RESTAURANT.md`](../architecture/RESTAURANT.md) (modèle, verrous, permissions,
  migrations, tests, critères d'acceptation R1 à R9)

## Contexte

Les modules `restaurant.*` sont déclarés `planned` (`backend/app/modules/planned.py`) : visibles
au catalogue, jamais activables (palier E.1, `422 module_not_implemented`). Le socle par site
(profil, `site_modules`, capacités, empreintes, changement de profil) est consolidé (palier G).

Le domaine Restauration / Maquis doit couvrir le petit maquis (sans table, sans cuisine, une
seule personne) jusqu'au restaurant structuré (salles, postes de préparation, QR), en
réutilisant catalogue, assortiment, ventes, paiements, caisse, stock, audit, RBAC et RLS, sans
système parallèle. Les arbitrages métier A1 à A7, B1 à B6, Z1 à Z3 et W1 sont intégrés
ci-dessous.

## Décision

### D1 — Modules (codes conservés)

| Code | Rôle | Dépendances obligatoires |
|---|---|---|
| `restaurant.menu` | sections, éléments, disponibilité manuelle | `catalog` |
| `restaurant.orders` | moteur unique, préparation générique, attribution, règlement | `restaurant.menu`, `sales` |
| `restaurant.tables` | zones et tables (facultatif) | `restaurant.orders` |
| `restaurant.kitchen` | affiché « Postes de préparation » (facultatif ; postes = données du site) | `restaurant.orders` |
| `restaurant.qr` | commandes publiques par QR (STANDARD et ENTREPRISE) | `restaurant.orders` |
| `restaurant.recipes` | recettes (ultérieur) | `catalog`, `stock`, `sales` |

- La dépendance actuelle `restaurant.orders → restaurant.tables` est supprimée à la livraison
  de R1. Aucun module n'est rendu `available` avant sa livraison réelle.
- Les dépendances facultatives (table, poste, caisse, client) sont testées par capacité côté
  serveur, jamais par un test de profil.
- `restaurant.kitchen` n'est **jamais requis** par les commandes (sans postes : flux de
  préparation générique). Il est activé ou désactivé site par site par
  `organization.module.manage`, dans les limites de l'offre, de la disponibilité et des
  dépendances ; désactivation refusée tant que des lignes routées vers un poste ne sont ni
  servies ni annulées (`409 module_has_open_operations`) ; les postes configurés sont conservés.

### D2 — Un moteur, trois canaux

Personnel, POS et QR créent la même Commande ; le canal (`STAFF` / `POS` / `QR`) n'est qu'une
donnée d'origine, posée par le serveur selon la route appelée. Table, responsable et client sont
facultatifs ; le mode de service (sur place / comptoir / à emporter) est obligatoire. Numéro court
par site et par jour (fuseau de l'entreprise, ADR-0028), recherche par numéro. Le mode de
paiement du site est recopié sur la commande à sa création : changer le réglage ne touche aucune
commande existante.

### D3 — Deux axes et un cycle de vie

- **Préparation** (lignes) : reçue → en préparation → prête → servie/remise (un seul état
  technique, libellé « Servie » sur place, « Remise » au comptoir ou à emporter) ;
  prête → en préparation permis (correction). **Servie/remise est définitif** : ni retour
  arrière, ni annulation, ni modification silencieuse (contrôle du service ET déclencheur en
  base).
- **Règlement** : non réglée → réglée. **Réglée = vente issue de la commande validée** (payée ou
  à crédit selon les règles existantes). L'état financier (payé, partiel, dû) est **toujours lu
  sur la vente** ; la commande n'en garde aucune copie.
- **Cycle de vie** : à confirmer (QR) → ouverte → close | annulée | refusée. Close = réglée ET
  toutes les lignes non annulées servies/remises. **Close est définitif** : aucune réouverture.
- **Commande réglée : ni ajout ni annulation de lignes** (`409 order_settled`) ; seules les
  transitions de préparation et de service continuent ; une commande supplémentaire est une
  nouvelle commande.
- Lignes immuables sauf leur état ; correction = annulation d'une ligne + nouvelle ligne
  (commande non réglée).

### D4 — Prix figés

Prix du catalogue global (article, ou conditionnement ; conditionnement sans prix configuré
exclu) lu et figé par le serveur sur la ligne à sa création. La vente issue d'une commande porte
une origine posée par le serveur, **immuable** ; seule cette origine dispense du contrôle
`sale_prices_changed`. Ventes ordinaires et POS inchangés. Au plus **une vente active** par
commande ; un nouveau règlement reprend les prix figés.

### D5 — Stock

Déduction à la validation de la vente seulement, par le moteur existant (FEFO, lots, CMUP).
Aucune réservation ; disponibilité indicative + épuisé manuel. Refus au règlement : articles
nommés, commande conservée, nouvelle tentative après correction. La disponibilité indicative
est **reportée** après R2 (D14, Q6) : en R2, seul l'épuisé manuel du menu s'applique.

### D6 — Paiement avant ou après (A1, B2, B6)

Mode du site (défaut = donnée du profil, recopié à l'activation, jamais écrasé par un changement
de profil, modifiable par le gestionnaire, aucune dérogation par commande en V1) :

- « à la commande » : le règlement précède la préparation (`409 order_not_settled`) ;
- « à la fin » : règlement à tout moment (avant, pendant ou après la préparation et le service).

Deux transactions distinctes :

- **T1 Création** : numéro, prix figés ; ni vente, ni paiement, ni stock, aucun contrôle de stock.
- **T2 Règlement** (tout ou rien) : création de la vente (origine = commande, canal
  `RESTAURANT`) → **validation** de la vente = déduction du stock → **paiements** (espèces :
  session ouverte de l'encaisseur ; reste dû : crédit — client, `sales.sale.credit_create`,
  limite et dérogation existantes). La dérogation existante `expired_lot_override` (permission
  `sales.sale.expired_lot_override`, motif, audit) est acceptée comme pour `checkout`.

Échec de T2 (stock, lot, article inactif ou hors assortiment, session de caisse, moyen de
paiement, limite de crédit) : seule T2 est annulée ; la commande reste intacte, « à régler »,
retrouvable par son numéro ; nouvelle tentative après correction. La commande précède toujours
le paiement : « avant » signifie avant la préparation ; aucun acompte ni paiement sans vente.
Aucun paiement en ligne en V1. `POST /pos/checkout` et `SaleService.checkout` sont inchangés.

### D7 — Attribution

- Protection d'une commande prise : 5 min par défaut, réglable par site (0 = désactivée) ;
  réattribution immédiate par `restaurant.orders.order.reassign`, motif obligatoire, auditée ; le
  nouveau responsable doit détenir `restaurant.orders.order.claim` effectif sur le site
  (`422 assignee_not_eligible`).
- Délai entre deux prises d'un même employé : désactivé par défaut, réglable par site,
  indépendant ; seule l'action explicite « Prendre » compte (ni la création, ni une
  réattribution) ; dernière prise lue dans l'historique de la commande (aucune table) ; prises
  d'un même employé sur un site sérialisées sous verrou.
- Contrôles serveur sous verrou transactionnel : verrou de la commande → relecture du
  responsable et de l'heure de prise → relecture du réglage du site → contrôle des permissions →
  action. Conflit : `409`. Deux prises simultanées : exactement une réussit. Appels directs à
  l'API : mêmes contrôles.

### D8 — Notifications internes (A4)

Mécanisme de plateforme générique (`member_notifications`), distinct des rappels d'abonnement.

- Transition « prête » : le responsable ET les membres du site disposant **effectivement** de
  `restaurant.orders.order.serve` (calcul par `CapabilityService.resolve` : appartenance active, accès
  et rôles du site, abonnement du site), **moins l'auteur de l'action, toujours** ; tout
  destinataire doit pouvoir lire la commande (`restaurant.orders.order.view` effectif sur ce site).
- Sans postes : un avis quand toute la commande est prête ; avec postes : un avis par partie
  prête et par poste (`Commande #125 — Table 2 — Bar : prêt (2 articles)`).
- Clé anti-doublon = (destinataire, évènement de transition) ; une transition répétée légitime
  (lignes ajoutées, correction) crée un nouvel évènement. Une seule notification par
  destinataire et par évènement.
- Résolution **calculée à la lecture** (lignes concernées servies, remises ou annulées) : aucune
  écriture sur les notifications des autres, historique intact.
- Lu / non lu individuel, compteur, « tout marquer lu », actualisation par interrogation toutes
  les 10 à 15 s (aucun WebSocket / SSE en V1, évolutif), purge à 30 jours (rôle et mécanisme
  définis en R4). Lisibles par leurs seuls destinataires ; jamais un autre site ni une autre
  entreprise.

### D9 — QR (B1)

Jeton aléatoire opaque par point d'accès (table ou comptoir), stocké haché, révocable par
rotation ; entreprise et site déduits du jeton seulement ; menu public sans stock, coût ni donnée
interne ; navigateur seul, aucune installation. Confirmation explicite par défaut
(`restaurant.orders.order.confirm`), acceptation automatique facultative par site ; en mode « à la
commande », le comptoir **confirme puis règle** (deux évènements, deux contrôles de permission,
enchaînables sur un même écran). Limitation de fréquence persistante, idempotence, plafonds,
RLS. Suivi public « à régler » sans aucune donnée de stock. Notifications push sur le téléphone
du client : volet mobile ultérieur, avec consentement, jamais obligatoires ; le suivi Web
fonctionne toujours.

### D10 — Activation à la livraison

Sur les sites existants, un module livré reste désactivé jusqu'à activation explicite : sa
migration de livraison remet à `false` les activations héritées de la période `planned`
(descente sans restauration : ces lignes étaient inertes). C'est le traitement, module par
module, de la décision ouverte d'E.1 (ADR-0048). Nouveaux sites : défauts du profil dans les
limites de l'offre. Un module non livré n'est jamais utilisable.

Précision du palier R1 : une activation inerte d'un module planifié ne bloque jamais la
désactivation d'un module livré — seuls les dépendants **disponibles** comptent dans
`module_has_dependents` (ex. : le menu se désactive même si les commandes, planifiées, sont
« activées » sur le site). Les lignes inertes ne sont ni lues ni modifiées ; la migration de
livraison du module concerné les remet à `false`. Lecture du menu : site sélectionné sans menu
effectif → `403 module_unavailable` ; sans site sélectionné, seuls les sites où le menu est
effectif pour le membre sont lus.

La migration de livraison d'un module refuse de descendre si des données saisies existent
(0041 : sections ou éléments de menu) ; détail dans
[`RESTAURANT.md`](../architecture/RESTAURANT.md) §11.

### D11 — Profils (données)

Définitions : *proposé* = `modules` ∪ `optional_modules` du profil ; *activé par défaut* =
`modules` (écrit activé à la création d'un site, s'il est dans l'offre) ; *facultatif* =
`optional_modules` (écrit désactivé, activable par le gestionnaire) ; *effectif* = proposé ∩
offre ∩ activé ∩ disponible ∩ dépendances effectives — seul état qui ouvre l'accès.

| Profil | Activés par défaut | Facultatifs | Paiement par défaut |
|---|---|---|---|
| Maquis, Bar, Café | menu, orders | tables, kitchen, qr, recipes | à la fin |
| Boulangerie | menu, orders | tables, kitchen, qr, recipes | à la commande |
| Restaurant, Pizzeria | menu, orders, tables, kitchen | qr, recipes | à la fin |
| Traiteur, Restauration rapide | menu, orders, kitchen | tables, qr, recipes | à la commande |

`restaurant.recipes` reste « à venir » jusqu'à R8 ; `restaurant.qr` entre dans l'offre STANDARD
à la livraison de R7. Le paiement par défaut est porté par une section générique
`module_settings` des profils (aucun test de code de profil).

### D12 — RBAC (A6, B5, Z2)

- Union des rôles de l'utilisateur pour le site ; chaque action teste SA permission, jamais un
  nom de rôle. Fonction dans la commande (responsable, préparateur d'une ligne, personne qui
  sert / remet) ≠ rôle RBAC ; une même personne peut cumuler les trois.
- Encaisser = `sales.sale.create` + `sales.sale.validate` (+ `sales.payment.create`,
  `sales.sale.credit_create` selon le cas) sur le site, et règles de session de caisse
  existantes.
- Modèles facultatifs **Serveur** (aucune permission d'encaissement) et **Préparateur** :
  **créés à la demande** (`POST /roles/from-template`), jamais automatiquement dans les
  entreprises ; clé générique `auto_provision = false` dans `role_templates.toml` ; gestion des
  conflits de noms précisée en R2.
- Permissions nouvelles déclarées avec leur nature ; délégation calculée par le serveur
  (ADR-0030) ; l'annulation d'une vente reste `sales.sale.cancel` / `sales.payment.cancel`
  (Administrateur par défaut, attribuables par rôle personnalisé).

### D13 — Annulations (A2, A3, B3, B4, Z1, Z3, W1)

- Aucune expiration automatique en V1.
- Commande **non réglée** : ligne reçue → `restaurant.orders.order.cancel` ; ligne en préparation ou
  prête → `restaurant.orders.order.cancel_prepared` ; motif obligatoire ; sans effet sur vente,
  paiement, caisse ou stock (rien n'a été vendu ni sorti).
- Commande : annulation de toutes ses lignes non finales (permission selon leur état), motif
  obligatoire ; refusée si réglée (`409 order_settled`) ou si une ligne est servie
  (`409 order_has_served_lines`).
- **Commande réglée non close (Z3)** : aucune annulation de ligne. Procédure : annulation de
  chaque paiement (`sales.payment.cancel` ; espèces : sortie inverse dans la session d'origine
  ouverte), puis de la vente (`sales.sale.cancel` ; un `CANCELLATION` par mouvement d'origine,
  même quantité, même coût, même lot, CMUP inchangé) par une personne habilitée ; la commande
  redevient « à régler » (évènement `SALE_CANCELLED`) ; annulation de la ligne avec motif ;
  nouveau règlement aux prix figés. Si la nouvelle vente consomme d'autres lots (FEFO, ex. lot
  d'origine devenu périmé), le stock total reste exact et le coût reste le CMUP du site (aucun
  coût par lot) ; la répartition par lot peut différer du physique et se corrige par
  l'inventaire par lot existant.
- **Commande close** : annulation de sa vente refusée (`409 order_closed`). Une erreur
  d'encaissement se corrige par l'annulation du paiement puis un nouveau paiement sur la même
  vente ; le reste dû éventuel est une créance calculée selon les règles existantes ; la
  commande reste close.
- **Servie non réglée** (client parti, B3, Z1) : règlement à crédit selon les règles existantes
  (client nommé, `sales.sale.credit_create`, limite et dérogation). À défaut de client à
  créditer, la commande **reste ouverte** — limite connue de V1 —, identifiable et avec son
  ancienneté visible ; la perte n'existe pas en V1 et sera conçue séparément.
- **Espèces après clôture de la session d'origine (W1)** : la règle de caisse existante est
  conservée (`409 cash_session_closed`) ; aucun remboursement n'est introduit en V1 ; la
  commande concernée reste identifiable (limite connue de V1).
- Historique en ajout seul et audit conservés ; contrôle du site et de l'entreprise à chaque
  action.

### D14 — Palier R2 : commandes et règlement (décisions validées avant R2-A)

**Périmètre et découpage**

- **Q1** : un seul palier fonctionnel R2 qui **absorbe l'ancien palier R3** (règlement) : un
  module de commandes sans règlement ne serait pas utilisable (aucune commande close ; « à la
  commande » : préparation impossible). Migrations distinctes : **0042** (commandes) et
  **0043** (origine des ventes) ; numéros des paliers suivants inchangés (R4 notifications…).
  R2 n'est livré qu'à la fin de R2-F.
- **Sous-étapes**, un commit chacune après ses vérifications (Q8) : R2-A socle de plateforme ;
  R2-B migration 0042 et moteur T1 ; R2-C prise en charge et modèles de rôles ; R2-D migration
  0043 et règlement ; R2-E interface et bascule du module ; R2-F E2E, documentation, rapport.
- **N1** : `restaurant.orders` reste `planned` dans le registre de production jusqu'au commit
  R2-E ; les tests intermédiaires utilisent un registre de test (copie où le module est
  disponible), sans drapeau ni chemin d'exception dans le code de production. Aucun commit
  intermédiaire ne rend le module utilisable sans règlement ni interface ; les commits
  intermédiaires ne se déploient pas en production.
- **Q2** : intégration à l'écran du POS reportée ; **un seul moteur** de commandes, le canal
  (`STAFF` en R2, `POS`, `QR`) étant fourni par la route, jamais par le client ; le POS
  ordinaire et `SaleService.checkout` ne sont pas modifiés.
- **Q6** : indicateur de stock reporté (D5) ; aucune réservation ; le contrôle décisif reste
  celui du règlement.

**Commande**

- **Q3** : numéro affiché clairement après la création ; **ticket de retrait** 80 mm imprimé
  par le navigateur, sans prix, coût ni stock (numéro, nom d'appel, mode de service, lignes),
  construit par le serveur ; composant d'impression **propre au module** (N2) :
  `ReceiptPrinter`, `ReceiptDialog` et le POS ne sont pas modifiés. Réimpression permise à qui
  peut consulter la commande (`restaurant.orders.order.view`) ; impression **non journalisée** en V1.
- **Q4** : nom d'appel facultatif (40 caractères au plus), secondaire : le numéro reste la
  référence.
- **P-2** : actions de préparation et de service par ligne **en plus** des actions sur toute la
  commande ; un évènement par requête (lignes concernées) ; sans postes, l'avis « prête » (R4)
  reste émis quand toute la commande est prête.
- **P-7** : `version` informatif, exposé à l'interface, jamais exigé en entrée.
- **P-8** : ajout de lignes idempotent (clé d'idempotence facultative sur l'évènement).
- **P-13** : écran en trois colonnes « Reçues », « En préparation », « Prêtes ».
- **N4** : une commande dont toutes les lignes ont été annulées n'est pas réglable
  (`409 order_empty`) ; elle s'annule avec `restaurant.orders.order.cancel` et un motif obligatoire.

**Règlement et droits**

- **P-11** : état financier (numéro de vente, payé / partiel / dû) visible avec
  `restaurant.orders.order.view` ; détail des paiements réservé à `sales.payment.view`.
- **P-12** : `restaurant.orders.order.confirm` déclarée au palier R7 (QR), à son premier usage.
- **Q5** : modèles Serveur et Préparateur facultatifs (`auto_provision = false`) ; si un rôle
  personnalisé porte déjà ce nom, la création du modèle est refusée (`409 role_name_taken`),
  sans renommage automatique.

**Données et migrations**

- **Q7 / N5** : retour arrière de 0042 refusé dès qu'une commande **ou** une ligne de réglages
  existe, y compris quand seuls des réglages subsistent après désactivation du module ; la
  désactivation ne supprime aucune donnée et la réactivation retrouve les réglages (le
  `site_setup` crée seulement ce qui manque). Retour arrière de 0043 refusé s'il existe des
  ventes `RESTAURANT`.
- **Q9** : la règle de retour arrière de 0041 est documentée à part
  ([`RESTAURANT.md`](../architecture/RESTAURANT.md) §11).

**Socle de plateforme (R2-A, livré)**

- `site_setup` du manifeste : initialisation d'un module **disponible** sur un site quand il y
  devient activé (création du premier site ou d'un nouveau site, activation manuelle,
  changement de profil du site), dans la transaction de l'activation ; idempotente, sans
  écrasement ; jamais pour un module planifié ni à la désactivation ; un échec annule
  l'activation. Un site dont l'activation est antérieure à la livraison d'un module (inerte)
  est initialisé par le module lui-même à son premier usage (R2-B).
- `module_settings` des profils : réglages par défaut d'un module proposé par le profil
  (valeurs scalaires, domaine contrôlé par le module) ; paiement par défaut des commandes
  porté par chaque profil de restauration (D11). Format et validation livrés en R2-A ;
  colonne `business_profiles.module_settings` et synchronisation en base dans 0042 (R2-B),
  comme prévu en R0.
- `auto_provision` des modèles de rôles (défaut `true`) : un modèle facultatif n'est jamais
  créé par le provisionnement ; un modèle protégé l'est toujours.
- `next_value` : compteur brut par entreprise et par clé (numéro court des commandes).
- Profils D11 appliqués aux données : Maquis, Bar, Café, Boulangerie portés par le profil UX
  `restaurant.default` ; Restaurant, Pizzeria, Traiteur, Restauration rapide le surchargent.
  Seuls les **nouveaux** sites sont concernés ; les activations des sites existants ne
  changent pas.

**Décisions du palier R2-B (validées)**

- **Clé du compteur des commandes** : `ro:{site_id}:{AAAAMMJJ}` — 3 + 36 + 1 + 8 =
  **48 caractères**, sous la limite de 50 de `document_sequences.sequence_key`, **sans
  élargissement de la colonne** (la clé de R0, `restaurant.order:{site_id}:{AAAA-MM-JJ}`, en
  comptait 64). Compteur par entreprise, par site et par jour de l'entreprise ; fondé sur
  l'**identifiant** du site, jamais sur son code ni son nom (clé stable après un renommage) ;
  jamais préfixé par `{site_id}:` (ne fige pas le code du site). Testé : longueur, isolation
  entre sites, entre jours, stabilité après changement du code et du nom du site.
- **Permissions préfixées par le code EXACT du module propriétaire** (règle du registre,
  inchangée) selon « module.ressource.action ». Correspondance avec R0 (seul le préfixe
  change ; natures, rôles et règles inchangés) :

  | R0 | Retenu |
  |---|---|
  | `restaurant.order.view`, `.create`, `.prepare`, `.serve`, `.cancel` (read / write) | `restaurant.orders.order.view`, `.create`, `.prepare`, `.serve`, `.cancel` |
  | `restaurant.order.cancel_prepared` (admin) | `restaurant.orders.order.cancel_prepared` |
  | `restaurant.order.claim` (write), `.reassign` (admin) — R2-C | `restaurant.orders.order.claim`, `.reassign` |
  | `restaurant.order.confirm` (write) — R7 | `restaurant.orders.order.confirm` |
  | `restaurant.settings.manage` (admin) | `restaurant.orders.settings.manage` |

  Convention pour les modules suivants, **sans figer leurs permissions avant leur palier** :
  `restaurant.tables.<ressource>.<action>` (R5), `restaurant.kitchen.<ressource>.<action>`
  (R6), `restaurant.qr.<ressource>.<action>` (R7), `restaurant.recipes.<ressource>.<action>`
  (R8). Le Gestionnaire (`restaurant.*`) couvre ces codes ; le Vendeur reçoit une liste
  explicite (R2-B : `restaurant.orders.order.view`, `.create`, `.prepare`, `.serve`,
  `.cancel`).
- **Module planifié ⇒ aucune permission accordée** : un module non livré qui déclare déjà ses
  permissions (ici `restaurant.orders`, planifié jusqu'à R2-E) n'en accorde aucune, même si
  une activation inerte le compte parmi les modules du site (`available_permissions` du
  registre ne retient que les modules disponibles). Routes non montées, activation refusée
  (`module_not_implemented`), aucune permission dans les capacités ni dans la délégation.

**R2-B livré** : migration 0042 (tables, déclencheur d'états finaux, RLS, droits par colonne,
`business_profiles.module_settings` synchronisée, remise à `false`, descente protégée) ;
moteur T1 (création idempotente, prix figés, numéro court), ajout de lignes idempotent,
préparation et service par commande ou par ligne, annulations, réglages par site
(`site_setup`, création au premier usage), ticket de retrait (API), ports du catalogue,
empreinte ; `restaurant.orders` toujours planifié en production.

**Décisions du palier R2-C (prise en charge, D7 ; modèles de rôles, D12 et Q5)**

- **Aucune migration** : 0042 porte déjà `assigned_user_id` / `assigned_at` / `assigned_by`,
  les évènements `CLAIMED` / `REASSIGNED` et l'index de la dernière prise par employé.
- **« Prendre »** (`POST /restaurant/orders/{id}/claim`, `restaurant.orders.order.claim`) :
  commande **ouverte**, réglée ou non (close, annulée ou refusée : refus existants). Commande
  déjà prise par l'employé : rien ne change (aucun évènement, même version).
  - **Protection** : mesurée depuis `assigned_at` (dernière prise ou réattribution) ; pendant
    `claim_protection_minutes`, un autre employé est refusé `409 order_claim_protected`
    (responsable nommé, fin de la protection) ; 0 = désactivée (reprise immédiate).
  - **Délai entre prises** : dernière action « Prendre » de l'employé **sur ce site**, lue dans
    l'historique ; pendant `claim_cooldown_minutes`, refus `409 claim_cooldown_active` (heure
    de disponibilité) ; ni la création ni une réattribution ne comptent ni ne remettent le délai
    à zéro ; 0 = désactivé.
  - **Verrous** (ordre global inchangé) : verrou consultatif de l'employé (site + employé) →
    réglages du site en partage → commande ; responsable, heure de prise et réglages relus sous
    ces verrous. Le verrou de l'employé est pris pour **toute** action « Prendre » (et non
    seulement quand le délai est positif) : le réglage n'est lu qu'après lui, l'ordre global
    reste ainsi respecté ; coût négligeable. Deux prises simultanées d'une commande protégée :
    exactement une réussit ; prises simultanées d'un même employé avec délai : une seule.
- **Réattribution** (`POST /restaurant/orders/{id}/reassign`,
  `restaurant.orders.order.reassign`, nature `admin`) : immédiate (la protection ne s'applique
  pas), motif obligatoire (évènement `REASSIGNED` et audit), contrôlée sous le verrou de la
  commande. Nouveau responsable **calculé par le serveur** : appartenance active, compte actif,
  site accessible, `restaurant.orders.order.claim` **effective sur le site** (rôles de
  l'entreprise et du site, modules effectifs, abonnement du site et son statut — le calcul de
  ses propres capacités ; jamais un nom de rôle), sinon `422 assignee_not_eligible`. Même
  responsable : rien ne change. Aucun retrait de responsable en V1 (pas de route).
- Audit `restaurant_order.claimed` (responsable précédent) et `restaurant_order.reassigned`
  (motif, ancien et nouveau responsables) ; `OrderOut.assigned_at` exposé (début de la
  protection) ; responsable ≠ rôle (D12).
- **Modèles de rôles** : Vendeur + `restaurant.orders.order.claim` ; Gestionnaire par
  `restaurant.*` (prise et réattribution) ; modèles facultatifs `waiter` (**Serveur** : menu
  consulté et « épuisé », commandes consultées, saisie, prise, service ; aucune permission
  d'encaissement ni d'annulation) et `preparer` (**Préparateur** : menu consulté et « épuisé »,
  commandes consultées, préparation), `auto_provision = false`, créés par
  `POST /roles/from-template`. Un rôle personnalisé du même nom (casse ignorée, créé avant
  l'arrivée du modèle) fait refuser la création : `409 role_name_taken` nommant le rôle
  (`role_id`, `role_name`), sans renommage automatique ; un nouveau rôle personnalisé ne peut
  plus prendre ce nom (`role_name_reserved`, règle existante des modèles).
- **Filtre des modules planifiés** (R2-B) vérifié sans régression : seul `restaurant.orders`
  déclare des permissions parmi les modules planifiés ; les permissions des modules livrés
  restent toutes accordées (registre et capacités du propriétaire, commerce et restauration).

**R2-C livré** : prise en charge, protection, délai entre prises, réattribution motivée,
modèles Serveur et Préparateur ; `restaurant.orders` toujours planifié en production.

**Décisions du palier R2-D (règlement T2, Z3, commande close)**

- **Migration 0043** : `sales.origin_type` / `origin_id` (complets ou absents ; posés par le
  serveur à la création, jamais acceptés de l'API — un champ envoyé par le client est ignoré ;
  **immuables** : déclencheur `sales_origin_immutable`, même contre une écriture SQL directe) ;
  index unique partiel `uq_sales_active_origin` (une vente active par origine) ; canal
  `RESTAURANT` (contrainte `ck_sales_sale_channel` ; une vente de ce canal a toujours une
  origine) ; FK composite `restaurant_orders (tenant_id, sale_id, site_id)` →
  `sales (tenant_id, id, site_id)` ; descente refusée dès qu'une vente issue d'une commande
  existe.
- **Règlement** `POST /restaurant/orders/{id}/settle` (paiements, `credit_override`,
  `expired_lot_override`, `idempotency_key` ; ni prix, ni lignes, ni origine) : permissions
  EXISTANTES sur le site de la commande (`sales.sale.create` + `sales.sale.validate`,
  `sales.payment.create` avec des paiements, `sales.sale.credit_create` pour un reste dû —
  contrôlée par les ventes) ; aucune permission nouvelle. Une seule transaction : verrou
  consultatif de la clé → verrou de la commande → lignes → `sales.api` `create_from_order`
  (vente `RESTAURANT` aux prix FIGÉS, puis **le même `validate`** que toute vente : articles
  actifs, assortiment, conditionnements et conversion, quantités entières, client et limite de
  crédit, `StockService.consume` FEFO / lots / CMUP, dérogation `expired_lot_override`,
  paiements et caisse) → commande `SETTLED`, `sale_id`, évènement `SETTLED`, audit
  `restaurant_order.settled` → clôture si toutes les lignes non annulées sont servies. Toute
  erreur annule tout : ni vente (même brouillon), ni ligne, ni paiement, ni mouvement de stock
  ou de caisse ; la commande reste « à régler » et la même clé peut être rejouée.
- **Seule dispense** : une vente ayant une origine n'est pas soumise à `sale_prices_changed`
  (prix figés) ; les ventes ordinaires et le POS gardent ce contrôle ; `SaleService.checkout`
  et `POST /pos/checkout` sont inchangés.
- **Lignes** : une vente porte chaque présentation une seule fois (règle des ventes) ; les
  lignes non annulées de même présentation sont regroupées, quantités et totaux FIGÉS
  additionnés (total de la vente = total de la commande, au centime) ; prix unitaire = prix figé
  commun, ou **moyen pondéré** (arrondi au centime, affichage seulement) si la même présentation
  a été commandée à deux prix différents.
- **Idempotence et concurrence** : même clé → `200`, vente existante (aucune écriture) ; clé
  différente sur une commande réglée → `409 order_settled` ; clé déjà utilisée pour un autre
  document → `409 idempotency_key_reused` ; deux règlements simultanés : un seul réussit (verrou
  de la commande), défense en base `uq_sales_active_origin` (`409 sale_origin_active`). Toutes
  les lignes annulées : `409 order_empty` (N4).
- **Port d'origine** `sales/origin_port.py` (modèle du port caisse ; aucune dépendance
  `sales → restaurant`) : l'annulation d'une vente ayant une origine appelle d'abord le
  gestionnaire enregistré, qui verrouille la commande AVANT la vente ; commande close →
  `409 order_closed` (vente inchangée) ; vente active de la commande → commande « à régler »
  (`sale_id` nul, évènement `SALE_CANCELLED`, audit `restaurant_order.sale_cancelled`) dans la
  même transaction (un refus ultérieur, ex. `sale_has_payments`, annule tout) ; ancienne vente
  déjà remplacée → commande intacte ; aucun gestionnaire → `409 sale_origin_unavailable`.
  Permissions : celles des ventes (`sales.payment.cancel`, `sales.sale.cancel`).
- **Commande close** : définitive (déclencheur 0042) ; un paiement annulé laisse la vente
  validée et la commande close (reste dû = créance), un nouveau paiement va sur la même vente.
  Clôture et annulation de la vente sérialisées par le verrou de la commande : soit la commande
  se clôt et l'annulation est refusée, soit la vente est annulée et la commande reste ouverte.
- **État financier** (P-11) lu sur la vente active : `sale_id`, `sale_number`,
  `payment_status`, `amount_due` dans les commandes, visibles avec `restaurant.orders.order.view` ;
  paiements : `sales.payment.view`. Client de la vente = client de la commande (aucun client
  saisi au règlement en R2-D).
- **Historique** : évènements d'une même requête à identifiants croissants (le tri heure puis
  identifiant garde l'ordre d'écriture : `SERVED` puis `CLOSED`).

**R2-D livré** : migration 0043, règlement T2, port d'origine, Z3, commande close, état
financier ; `restaurant.orders` toujours planifié en production (bascule en R2-E).

**Décisions du palier R2-E (interface)**

- **Écrans** (module frontend `restaurant_orders`, affiché seulement si le backend déclare
  `restaurant.orders` effectif) : « Commandes » — suivi en trois colonnes (Reçues, En
  préparation, Prêtes ; actualisé toutes les 15 s), onglet « À régler » (commandes en cours non
  réglées, filtre « servies non réglées ») et « Historique » (closes, annulées ou refusées,
  toutes) ; « Nouvelle commande » (mode de service, nom d'appel, client facultatif, éléments
  COMMANDABLES du menu du site, une clé d'idempotence par saisie) ; fiche (numéro mis en
  évidence après la création, lignes aux prix figés et leurs actions, prise en charge,
  réattribution motivée, annulations motivées, historique, état financier lu sur la vente) ;
  « Réglages » d'un site (moment du paiement, protection, délai entre prises ;
  `qr_auto_accept` affiché, non modifiable). Chaque bouton n'apparaît qu'avec la permission et
  dans l'état qui le permettent ; le serveur refait tous les contrôles.
- **Ticket de retrait 80 mm sans prix** : composant PROPRE au module (N2), même principe
  d'impression que le reçu (`#sm-print-root`, règle `@page` injectée le temps de l'impression) ;
  `ReceiptPrinter`, `ReceiptDialog` et le POS ne sont pas modifiés.
- **Règlement** : le dialogue de validation des ventes (`ValidateSaleDialog`) est réutilisé tel
  quel (libellé « Valider la vente » conservé), soumis à `POST /restaurant/orders/{id}/settle`
  avec une clé par ouverture du dialogue, rejouée en cas de nouvel essai après un refus ;
  messages d'erreur des ventes (dont dépassement de limite de crédit et lots périmés) ; reçu de
  la vente ouvert après un règlement réussi (avec `sales.sale.view`). Aucun nouveau mécanisme de
  crédit ni contournement : le dialogue applique les règles existantes (encaissement proposé
  d'emblée sans client ou sans `sales.sale.credit_create`).
- **Ajouts serveur nécessaires à l'interface** (sans migration) : filtre
  `settlement_status` (`UNSETTLED` \| `SETTLED`) de `GET /restaurant/orders` ;
  `GET /restaurant/orders/{id}/assignees` (`restaurant.orders.order.reassign` sur le site) :
  membres actifs détenant `restaurant.orders.order.claim` EFFECTIVE sur le site, calculés par le
  serveur avec la règle de la réattribution (l'interface n'en décide jamais) ; indicateur
  `weighted_unit_price` (booléen calculé, aucune colonne) sur les lignes des ventes et du reçu :
  `vrai` lorsque `line_total ≠ arrondi(quantité × prix unitaire)`, c.-à-d. ligne regroupée à des
  prix figés différents. Le reçu affiche « × prix (prix moyen) » suivi du total EXACT de la
  ligne ; la fiche de la vente signale « Prix moyen pondéré … le total fait foi ». Le POS et les
  ventes ordinaires ne sont jamais concernés (indicateur toujours faux).
- **Libellés** : canal `RESTAURANT` = « Restauration » (filtres et fiches des ventes) ; codes
  d'erreur des commandes traduits (`errors.json`) ; libellés des permissions
  `restaurant.orders.*`.
- **Commande sans client** (limite de V1, Z1) : comportement inchangé — le règlement d'une
  commande sans client n'accepte pas de reste dû (`credit_customer_required` des ventes) ;
  l'interface l'annonce sur une commande servie non réglée sans client. Aucune route n'associe
  un client APRÈS la création ; ce point est soumis à décision (non tranché en R2-E).
- **N1 maintenu sur décision de l'utilisateur** : `restaurant.orders` reste `planned` en
  production à la fin de R2-E (le plan prévoyait la bascule dans ce commit) ; la bascule suit
  la validation explicite de R2-E. Les écrans ne s'affichent donc pas encore en production.

**R2-E livré** : interface des commandes, ticket de retrait sans prix, règlement depuis la
fiche, réglages ; `restaurant.orders` toujours planifié en production (bascule après
validation). Ne pas commencer R2-F sans validation explicite.

## Conséquences

- **Ventes (R2-D, ancien R3)** : `origin_type` / `origin_id` immuables (déclencheur), index
  unique partiel des ventes actives par origine, canal `RESTAURANT` (contrainte CHECK),
  méthode interne `create_from_order`, port `sales/origin_port.py` appelé **avant** le verrou
  de la vente lors d'une annulation (refus si aucun port enregistré : `409 sale_origin_unavailable`).
- **Catalogue** : les commandes non finales s'enregistrent dans les ports assortiment, usage des
  conditionnements et suivi par lot.
- **Plateforme** : hook `site_setup` à l'activation d'un module sur un site, section
  `module_settings` des profils, notifications de membres, compteur
  `ro:{site_id}:{AAAAMMJJ}` (D14 ; jamais préfixé par `{site_id}:`, qui figerait le code du
  site), clé `auto_provision` des modèles de rôles.
- **Base** : déclencheurs d'états finaux (lignes, commandes) et d'origine des ventes.
- **Licences** : un site STANDARD déjà licencié n'obtient le QR qu'à sa prochaine licence ; la
  règle de licence n'est pas modifiée.
- **Changement de profil** : commandes non finales = travail en cours (BLOCKED si
  `restaurant.orders` disparaît, commandes nommées dans le détail) ; closes = historique
  (STRONG) ; menu, tables, postes = configuration.
- **Limites connues de V1** : commandes servies non réglées sans client à créditer (Z1) ;
  remboursement d'espèces après clôture de la session d'origine (W1).

## Alternatives écartées

- Utiliser le brouillon de vente comme commande : prix relus, pas d'états de préparation, risque
  de régression du POS.
- Un moteur QR séparé : deux règles de prix, de stock et de notification.
- Tables obligatoires ; un module par poste (Cuisine, Bar…).
- Relire le prix au règlement : le client paierait un autre prix que celui annoncé.
- Réserver le stock à la commande.
- Réutiliser la table `notifications` des rappels d'abonnement.
- Jeton QR signé (irrévocable sans liste noire) ; WebSocket / SSE en V1.
- Un compte par fonction.
- Commande et paiement dans une seule transaction (la commande serait perdue à l'échec).
- Expiration automatique des commandes.
- Réouverture d'une commande close ; annulation d'une ligne réglée sans annuler la vente.
- Procédure de perte ou de remboursement improvisée.
- Création automatique des modèles Serveur et Préparateur dans toutes les entreprises.

## Suivi

Paliers R1 (Menu) à R9 (Consolidation) : [`RESTAURANT.md`](../architecture/RESTAURANT.md).
Notifications push des clients : volet mobile ultérieur. Aucun palier ne commence sans
validation explicite.
