# Documentation — points à reprendre lors de la révision globale finale

Consigne : la documentation complète est mise à jour à la fin de l'implémentation de
l'application. Ce fichier garde la trace des changements de comportement, permissions,
migrations et décisions qui ne sont PAS encore reportés dans les documents de référence. Les
décisions elles-mêmes restent tracées dans les ADR au moment où elles sont prises.

## R2-E — association tardive du client (Z1)

Décision tracée : [ADR-0049](adr/0049-restauration-commandes.md), section « Décisions du palier
R2-E ». À reporter :

- `docs/architecture/API.md` (commandes de restauration) : route
  `PUT /restaurant/orders/{id}/customer` (permission `restaurant.orders.order.create`, refus
  `order_settled` / `order_closed` / `order_cancelled`, `customer_unavailable`,
  `customer_not_found`, `customer_inactive`, `customer_change_reason_required`).
- `docs/architecture/DATA_MODEL.md` : migration 0044 (`GRANT UPDATE (customer_id)` sur
  `restaurant_orders`, type d'évènement `CUSTOMER_SET`, descente refusée si une association
  existe).
- `docs/architecture/RESTAURANT.md` : parcours Z1 (servie non réglée → client associé → crédit
  selon les règles des ventes), tableau des refus, liste des évènements, migrations.
- Guide utilisateur (à créer) : « Associer un client » / « Changer de client » sur la fiche
  d'une commande.

## R2-E — désignation d'un client sans consultation des clients (vérification du risque résiduel)

Comportement confirmé (aucune règle modifiée) et durci, à reporter dans `RESTAURANT.md`,
`CLIENTS.md` et la section sécurité / RBAC :

- Désigner un client par son identifiant dans une opération (création de commande, association
  tardive, comme une vente avec `sales.sale.create`) relève de la permission de l'opération ;
  `customers.customer.view` régit la CONSULTATION du référentiel (`/customers`) et la recherche
  dans l'interface. Une commande n'expose du client que `customer_id` et `customer_name`
  (réponses et évènement `CUSTOMER_SET`) ; l'audit exige `audit.log.view`.
- Durcissement : le refus `customer_inactive` des commandes de restauration ne joint le code du
  client (`customer_code`) qu'à un membre détenant `customers.customer.view` sur le site.
- ~~Point ouvert~~ RÉSOLU (correction ciblée après R2-F) : les ventes (`sales` : brouillon,
  modification, validation, point de vente, vente issue d'une commande) appliquent la même
  règle ; voir la section « Ventes — confidentialité du client désactivé » ci-dessous.

## R2-F — validation finale de R2-E

À reporter (aucun comportement modifié) :

- Couverture E2E des commandes : tant que `restaurant.orders` est `planned`, ses routes ne sont
  pas montées en production (inventaire OpenAPI vérifié par
  `test_production_mounts_no_order_route`) ; les parcours d'interface des commandes sont couverts
  par les tests API (registre de test) et Vitest, les E2E vérifiant seulement l'absence du
  module (`profiles.e2e.ts`, `restaurant-menu.e2e.ts`). Un E2E des écrans de commande sera à
  écrire lors de l'activation du module (sur autorisation).
- E2E : premier test d'une exécution sensible au démarrage à froid du serveur de développement
  Vite (compilation à la demande) — à mentionner dans la procédure E2E (préchauffage ou
  serveur construit).
- Commentaire de `backend/app/modules/planned.py` (« toujours planifié jusqu'à R2-E ») à
  aligner sur la décision « planifié jusqu'à autorisation explicite d'activation ».

## Ventes — confidentialité du client désactivé (correction ciblée après R2-F)

À reporter dans `SALES.md`, `CLIENTS.md`, `API.md` (refus `customer_inactive`) et la section
sécurité / RBAC :

- Règle commune portée par le module Clients : `customers.api.require_active_customer` (client
  du tenant : `422 customer_not_found` ; actif : `422 customer_inactive`) ; le code du client
  désactivé (`customer_code`) n'est joint au refus qu'à un membre détenant
  `customers.customer.view` SUR LE SITE de l'opération (`CUSTOMER_VIEW` exporté par
  `customers.api`). Utilisée par les ventes (`SaleService._customer`) et les commandes de
  restauration (comportement inchangé).
- Code d'erreur, message, permissions d'opération et règles de crédit inchangés ; aucune
  permission nouvelle ; aucun changement frontend (le message traduit n'utilise pas le code).
- **Risque résiduel à réexaminer lors de la révision finale** (validé, non corrigé) : la
  distinction entre `422 customer_not_found` et `422 customer_inactive` révèle encore à
  l'appelant si un identifiant de client existe dans l'entreprise (même sans
  `customers.customer.view`). Portée pratique faible (identifiants non devinables, accès limité
  à l'entreprise), mais à arbitrer : conserver deux codes distincts, ou un refus unique pour qui
  ne peut pas consulter les clients.

## Plan de migrations de la restauration (correction à reporter)

`RESTAURANT.md` §11 (et toute mention « 0044 = R4 ») est décalé par la livraison de R2-E :

| Migration | Palier | Contenu réel |
|---|---|---|
| 0044 | R2-E (livrée) | association tardive du client (Z1) : `GRANT UPDATE (customer_id)`, évènement `CUSTOMER_SET` |
| 0045 | R4 (livrée) | `member_notifications`, politiques RLS (dont restrictives), droits du rôle de purge |
| 0046 | R5 (prévue) | zones, tables, FK `restaurant_orders.table_id` (ancien « 0045 ») |
| 0047 | R6 (prévue) | postes, affectations, `station_id` (ancien « 0046 ») |
| 0048 | R7 (prévue) | points QR (ancien « 0047 ») |
| 0049 | R8 (prévue) | recettes (ancien « 0048 ») |

Les migrations livrées ne sont ni modifiées ni renumérotées ; les numéros des paliers suivants
restent indicatifs jusqu'à leur livraison.

## R4 — notifications des membres (ADR-0049 D8)

À reporter dans `RESTAURANT.md` (§4, §7, §8, §9, §12, §14), `DATA_MODEL.md`, `API.md`,
la section sécurité / RBAC, l'exploitation (rôles SQL, cron) et `DESIGN_SYSTEM.md` :

- **Plateforme** : paquet `app/platform/member_notifications` (modèle, service d'émission
  `notify_members`, port de résolution `register_notification_resolver`, routeur, purge) ;
  table `member_notifications` (site, destinataire, `kind`, `source_type` / `source_id`,
  `entity_type` / `entity_id`, `payload`, `created_at`, `read_at`) ; clé anti-doublon
  `uq_member_notifications_source (tenant_id, recipient_user_id, source_type, source_id)`.
- **RLS** (migration 0045) : `tenant_isolation` (rôle applicatif, permissive) ; `recipient_read`
  et `recipient_update` RESTRICTIVES (`recipient_user_id = app_current_user_id()`) ; droits du
  rôle applicatif : `SELECT`, `INSERT`, `UPDATE (read_at)`, jamais `DELETE`.
- **Émission** : une insertion par destinataire dans un point de sauvegarde (la clé anti-doublon
  est appliquée par l'index unique) — `INSERT … ON CONFLICT` est inutilisable : PostgreSQL y
  applique la politique de LECTURE, réservée au destinataire. Insertion en dernier, sans verrou
  (§7 point 8), dans la transaction de la transition.
- **Commandes** : `restaurant_orders/notifications.py` — type `restaurant.order.ready`, source
  `restaurant_order_event` (l'évènement `READY`), entité `restaurant_order` ; émis quand une
  transition « prête » laisse TOUTE la commande prête (sans postes) ; destinataires = responsable
  + titulaires effectifs de `restaurant.orders.order.serve` sur le site (calcul
  `member_permissions`, partagé avec `member_holds`), tous lecteurs de la commande
  (`order.view`), moins l'auteur ; candidats = appartenances actives ayant accès au site ;
  `payload` = numéro du jour, date, nombre et identifiants des lignes prêtes (aucun prix, client,
  coût). Résolution à la lecture : lignes concernées toutes servies ou annulées.
- **API** (`organization.notification.view`, nature `read`) : `GET /member-notifications`
  (filtres `unread`, `site_id` ; plus récentes d'abord ; `resolved`), `GET
  /member-notifications/unread-count`, `POST /member-notifications/{id}/read` (idempotent ;
  `404 notification_not_found` pour celle d'un autre), `POST /member-notifications/read-all`.
  Lisibles seulement sur les sites accessibles où le membre détient la permission (revérifié à
  chaque lecture).
- **Permission** `organization.notification.view` (déclarée dans le module `organization`) :
  Administrateur (`*`), Consultant (`*.view`), et ajoutée aux modèles Gestionnaire, Vendeur,
  Serveur, Préparateur (`role_templates.toml`) — conforme à `RESTAURANT.md` §8. Descriptions des
  modèles à compléter (« notifications »).
- **Purge** : rôle SQL dédié `stockmanager_purge` (sans `BYPASSRLS` ; script
  `docker/postgres/init/03-purge-role.sh`, à créer AVANT la migration 0045 sur une base
  existante — la migration le vérifie) ; droits `USAGE` du schéma, `SELECT (id, created_at)`,
  `DELETE` ; politiques `purge_read` / `purge_delete` limitées aux lignes de plus de 30 jours
  (durée figée dans la migration). Commande `stockmanager notifications purge` (quotidienne,
  idempotente ; `SM_PURGE_DATABASE_URL`, `SM_DB_PURGE_ROLE`). CI : création du rôle et
  `SM_TEST_PURGE_DATABASE_URL`. `docker/README.md` complété (indispensable à l'exécution de la
  migration sur un volume existant).
- **Interface** : centre de notifications UNIQUE — une seule cloche (somme des sources
  autorisées) et une seule page `/notifications` (module Organisation), une section par source
  déclarée par les modules (`FrontendModule.notifications`) : « Activité » (notifications des
  membres, interrogation toutes les 15 s) et « Rappels d'échéance des licences » (anciens
  `NotificationsPage` et cloche du module Abonnement, devenus `ExpiryNotificationsPanel` ;
  comportement inchangé). Lien « Ouvrir » vers la commande avec `restaurant.orders.order.view`
  (marque lu).
- **E2E différés tant que `restaurant.orders` est planifié** (aucune route de commande en
  production) : avis créé par « Prête » dans l'interface, compteur de la cloche mis à jour en
  15 s, « Ouvrir » vers la fiche de la commande, « traitée » après le service. Couverts par les
  tests API (registre de test) et Vitest.
- **Points à arbitrer / risques résiduels** :
  - commande rendue entièrement prête par l'ANNULATION de sa dernière ligne non prête : aucun
    avis (D8 ne vise que la transition « prête ») ;
  - destinataire sans `organization.notification.view` (analyse complémentaire, tests
    `test_every_base_role_able_to_receive_can_read`,
    `test_custom_role_without_notification_view_receives_but_cannot_read`) : jamais par un
    rôle STANDARD (Administrateur, Gestionnaire, Vendeur, Serveur ont tous la lecture ; rôles
    standard résolus depuis leur modèle, entreprises existantes comprises) ; seulement par un
    rôle PERSONNALISÉ donnant `order.view` + `order.serve` (ou `claim`, responsable) sans la
    lecture des notifications SUR CE SITE (y compris lecture détenue sur un autre site
    seulement). Notification alors enregistrée mais illisible (403 / invisible) jusqu'à la
    purge — conforme à la lettre de D8 (règle des destinataires), en tension avec « lisibles
    par leurs destinataires ». Arbitrage présenté, en attente ;
  - **décision A + C retenue (correctif)** : règle D8 conservée intégralement ; avertissement
    NON bloquant à la création, modification, duplication et consultation d'un rôle
    personnalisé (`RoleOut.warnings`, code `notification_read_missing`, champs
    `receiver_permissions`, `read_permission`, `affected` = attributions (membre, site) où la
    lecture manque sur CE site — rôles du tenant et de ce site, jamais d'un autre site ; vide
    pour un rôle inactif ; absent des listes). Permissions pouvant rendre destinataire déclarées
    par le module émetteur (`register_notification_receivers` ; `restaurant.orders` :
    `order.serve`, `order.claim`). Interface : message dans la fenêtre du rôle (conservée
    ouverte après un enregistrement avec avertissement). À reporter dans `API.md` (rôles),
    la section RBAC et le guide utilisateur (rôles personnalisés). Limite : une combinaison
    incomplète créée par la seule modification des ATTRIBUTIONS d'un membre (page Utilisateurs)
    n'est signalée qu'à la consultation du rôle, pas à l'enregistrement du membre ;
  - comportement EXISTANT constaté (antérieur à R4, non modifié) : en production, le
    propriétaire peut enregistrer des permissions `restaurant.orders.*` dans un rôle
    personnalisé (module planifié à activation inerte ; propriétaire exempté de
    l'anti-escalade) ; sans effet tant que le module est planifié, effectives dès son
    activation ;
  - postes (R6) : un avis par partie et par poste exigera un évènement source par poste (la clé
    anti-doublon reste (destinataire, évènement)) ;
  - coût : une résolution de capacités par membre de l'équipe du site à chaque commande prête ;
  - planifier le cron de purge en exploitation.
- **E2E (exécution R4, puis vérification complémentaire)** : premières exécutions à 161/162
  (échecs différents : `assortment.e2e.ts:166` au run 1, dépassement des 30 s de
  `receivables.e2e.ts:156` au run 2) ; après diagnostic, deux nouvelles exécutions complètes à
  **162/162** (32,1 min et 33,8 min). Diagnostic (mesures, aucune correction appliquée) :
  - `receivables.e2e.ts:156` : aucune requête anormalement lente (trace), mais une succession
    d'étapes d'~1 s (rafales de requêtes au chargement de chaque page, un seul processus API).
    A/B même machine, même base : code d'AVANT R4 (`3541126`) 25,5–28,7 s (moy. 27,4 s), code
    R4 26,7–32,4 s (moy. 28,75 s) → R4 ajoute ~1 s (requête `member-notifications/unread-count`
    de la cloche à chaque chargement), l'essentiel est antérieur à R4. Base neuve (même code
    R4) : 22,8–27,4 s (moy. 25,2 s) → volume accumulé de la base E2E ≈ 2–3 s. Le reste vient
    de l'environnement : suite pytest (base recréée) 1,54 s/test en R2-F, 2,01 s/test sur le
    code d'avant R4 le lendemain (+30 %). Test historiquement à 18 s, désormais 25–29 s pour un
    délai de 30 s : marge insuffisante, d'où l'intermittence.
  - Marge réduite généralisée : en R2-F aucun test ne dépassait 24 s ; aujourd'hui 7 à 8 tests
    entre 24 et 30 s, dont des scénarios sans lien avec R4 (`users.e2e.ts:90` à 29,2 s,
    `console-licenses.e2e.ts:102`, `transfers.e2e.ts:215`, `cash.e2e.ts:124`).
  - `assortment.e2e.ts:166` : non reproduit — 8/8 réussites, Vite et API redémarrés à froid
    avant chaque essai (titre affiché en 1,4–1,6 s pour 5 s permises), puis réussi dans les
    deux exécutions complètes (15,2 s, 11,7 s) ; aucun rechargement de dépendances Vite dans
    les journaux. La trace de l'échec initial avait été écrasée : cause non établie (les
    sorties de chaque exécution sont désormais conservées séparément).
  - À arbitrer (rien n'a été modifié) : base E2E réinitialisée ou entreprises jetables,
    découpage des scénarios longs ou `test.slow()` ciblé, frontend construit (`vite preview`)
    au lieu du serveur de développement pendant les E2E.

## R5 — tables et zones : conception validée (non implémentée)

Conception de `restaurant.tables` validée (implémentation R5-A non autorisée à ce stade). À
reporter, à la livraison, dans ADR-0049 (D1, D11, O8), `RESTAURANT.md` (§3, §4, §7, §8, §11,
§13, §14), `DATA_MODEL.md`, `API.md` et le guide utilisateur.

- **Écarts constatés dans le dépôt** :
  - E1 : `backend/app/modules/planned.py` déclare `restaurant.tables` sans dépendance, alors
    qu'ADR-0049 D1 prévoit `depends_on = restaurant.orders` ; le manifeste réel livré en R5
    déclarera la dépendance (entrée planifiée retirée) ;
  - E2 : conséquence de D1 — le module ne peut être effectif en production tant que
    `restaurant.orders` est `planned` ; il reste lui aussi `planned` (A2) ;
  - E3 : D11 active les tables par défaut pour Restaurant / Pizzeria ; les sites existants sont
    remis à `false` par la migration 0046 (A11), les nouveaux sites suivent D11 ;
  - E4 : `RESTAURANT.md` §11 attribue encore 0045 à R5 (voir « Plan de migrations » ci-dessus).
- **Arbitrages retenus (A1–A11, B1–B7)** : plusieurs commandes ouvertes par table, avec
  avertissement (A1) ; table seulement en `ON_SITE` (A3), aussi par contrainte en base
  `table_id IS NULL OR service_mode = 'ON_SITE'` (B5) ; zone facultative (A4) ; motif du
  changement de table facultatif (A5) ; désactivation du module refusée avec des commandes
  ouvertes rattachées à des tables (A6) ; aucune copie du libellé dans la commande, libellés
  conservés par l'historique (A7) — l'affichage courant d'une commande close suit les
  renommages (B6) ; association et changement de table par `restaurant.orders.order.create`
  vérifiée sur le site de la commande (A8) ; R4 inchangé (A9) ; permissions
  `restaurant.tables.table.view` (read) et `restaurant.tables.table.manage` (admin, zones et
  tables) ; retrait de la table permis quand le module n'est pas effectif (B4) ; filtre
  `table_id` de la liste des commandes (B7).
- **Indépendance des commandes** : port `restaurant_orders/table_port.py` implémenté et
  enregistré par `restaurant_tables` ; aucune importation de `restaurant_tables` par les
  commandes ; module « effectif » = capacités du site RECALCULÉES après les verrous (celles du
  contexte sont mises en cache par requête) ET module disponible dans le registre (une
  activation inerte d'un module planifié compte parmi les modules du site) ; sinon
  `422 tables_unavailable`.
- **Occupation (B1)**, calculée à la lecture : une commande `PENDING_CONFIRMATION` ou `OPEN`
  occupe sa table ; Occupée (au moins une commande « en cours ») > À régler (toutes ouvertes,
  non réglées, tout servi) > Libre. Annulée, refusée ou close : table libérée ; réglée non
  servie : table occupée. Le filtre « À régler » de la liste des commandes reste inchangé.
- **Historique** : évènement `TABLE_CHANGED` (table nouvelle et ancienne : identifiant, libellé,
  zone ; aucun évènement sans changement) et table initiale dans `CREATED` ; audits
  `restaurant_order.table_changed`, `restaurant_table.*`, `restaurant_zone.*`.
- **Licences** : `restaurant.tables` figure dans STANDARD et ENTREPRISE depuis la Phase 1 ; toute
  licence émise le contient (modules du plan figés sans filtre) ; aucun renouvellement ni
  aucune réémission requis.
- **Ordre des verrous à reporter dans `RESTAURANT.md` §7** (écritures portant une table) :
  verrou d'idempotence (création) → site en partage de clé (`FOR KEY SHARE`, en premier) →
  réglages du site (partage) → table cible (partage) → commande (exclusif, changement de table)
  → contrôles après verrous → écriture. Désactivation d'une table : table en exclusif puis
  comptage des commandes ouvertes ; zone : exclusif pour la désactiver, partage pour y
  rattacher une table.
- **Migration 0046 (prévue)** : `restaurant_zones`, `restaurant_tables` (RLS, aucun `DELETE`),
  FK composite `restaurant_orders (tenant_id, site_id, table_id)` en `RESTRICT`, contrainte B5,
  index partiel, `GRANT UPDATE (table_id)`, valeur `TABLE_CHANGED`, remise à `false` de
  `restaurant.tables` sur les sites existants, descente refusée dès qu'une zone ou une table
  existe.
- **Décision à réexaminer séparément (R-1)** : la politique E.1 d'ADR-0048 est CONSERVÉE (les
  activations automatiques créées pour les modules planifiés restent telles quelles). Un site
  Restaurant / Pizzeria créé après 0046 recevra `restaurant.tables` activé (inerte), qui
  deviendra effectif dès le passage du module à `available`, sans action de l'entreprise ;
  même cas pour `restaurant.orders` depuis 0042. Aucune remise à `false` future n'est
  programmée et la politique de `restaurant.orders` n'est pas modifiée ; arbitrage séparé
  requis.
- **Risque à établir (R-2, hypothèse non démontrée)** : interblocage possible entre une écriture
  de commande (réglages pris en partage, puis contrôle de la clé étrangère vers le site à
  l'insertion) et une opération qui verrouille d'abord le site en exclusif (désactivation d'un
  module, changement de profil). Tests de concurrence reproductibles obligatoires en R5-B
  (connexions pilotées pas à pas, attente vérifiée dans `pg_locks` / `pg_stat_activity`,
  `lock_timeout` borné, absence de `40P01` vérifiée, deux ordres), y compris sur les commandes
  SANS table ; si l'interblocage est confirmé dans le code existant : scénario reproductible
  rapporté, aucune correction hors R5 sans autorisation.
- **Autres risques** : E2E impossibles tant que les modules sont `planned` ; une commande oubliée
  garde la table « Occupée » (A1 : âge et nombre de commandes affichés) ; plans en base
  supposés conformes à `plans.toml` (`catalog sync`) ; interrogation du plan de salle toutes
  les 15 s (index partiel).
- **Fonctions reportées** : réservations, fusion de tables, statut « nettoyage », création en
  série des tables (A10), recherche des commandes par libellé de table (B7), transfert de
  lignes entre commandes / partage d'addition, plan de salle graphique.

### R5-A — socle livré (zones et tables, migration 0046)

À reporter avec le reste de R5 (mêmes documents). Le module reste `planned` en production (A2) ;
les tests utilisent un registre de test (fixture `tclient`).

- **Écart d'URL (contrainte du registre)** : deux modules ne peuvent pas partager un préfixe
  d'URL, et `restaurant.orders` occupe `/restaurant`. Les routes livrées sont donc
  `/restaurant/tables` (tables : liste, création, `/{id}`, `/{id}/activate|deactivate`) et
  `/restaurant/tables/zones` (zones), au lieu de `/restaurant/zones` envisagé dans la conception.
  Les routes des zones sont déclarées avant `/{table_id}` (de même pour le futur plan de salle,
  R5-B).
- **Codes d'erreur nouveaux** (traductions `errors.json` à ajouter en R5-C, l'interface étant hors
  R5-A) : `zone_not_found`, `table_not_found` (404), `zone_name_taken`, `table_label_taken`,
  `zone_has_active_tables`, `table_has_open_orders` (409), `zone_inactive` (422).
- **Interface publique nouvelle des commandes** : `restaurant_orders/api.py` (lecture seule :
  `open_orders_on_tables`, `open_orders_by_table`), utilisée par l'empreinte et la protection de
  la désactivation d'une table ; aucune opération des commandes modifiée.
- **Empreinte du module** : `active` = commandes non finales rattachées à une table (A6 :
  désactivation manuelle refusée, `module_has_open_operations`) ; `info` = zones et tables. Par
  le mécanisme commun du palier D, un changement de profil qui ARRÊTERAIT les tables avec de
  telles commandes serait BLOQUÉ (et non STRONG, comme envisagé dans la v1 de la conception). En
  pratique, tous les profils de restauration proposent les tables (par défaut ou en option) :
  elles ne s'arrêtent qu'en quittant le secteur, ce qui arrête aussi les commandes, déjà
  BLOQUÉES par leurs commandes en cours.
- **Modèles de rôles** : `restaurant.tables.table.view` ajoutée au Vendeur, au Serveur et au
  Préparateur (descriptions mises à jour) ; Gestionnaire par `restaurant.*` (consultation et
  configuration), Consultant par `*.view`, Administrateur par `*`.
- **Migration 0046** : nom de la clé étrangère `restaurant_orders → restaurant_tables` abrégé
  par SQLAlchemy (convention > 63 caractères : `…_restaur_9e1f`) ; montée refusée si une
  commande portait déjà un `table_id` ; descente refusée dès qu'une zone, une table ou un
  évènement `TABLE_CHANGED` existe ; `restaurant.tables` remis à `false` sur les sites
  existants seulement (politique E.1 conservée pour les sites créés ensuite).
- **Concurrence de la configuration** (tests pas à pas : transaction tenue sous le rôle
  applicatif, attente vérifiée dans `pg_stat_activity`, puis validation) : création d'une table
  contre désactivation de sa zone, désactivation d'une zone contre création d'une table,
  réactivation d'une table contre désactivation de sa zone, désactivation d'une table contre
  rattachement d'une commande (écriture de R5-B simulée) ; libellés concurrents (`409`). Les
  tests d'interblocage des écritures de commandes (R-2) restent à faire en R5-B.

### R5-A — clôture (validée sur `4d7ffbe`, CI `38168592174` verte : 4/4 jobs)

Validation limitée à R5-A ; `restaurant.orders` et `restaurant.tables` restent `planned` en
production ; politique E.1 d'ADR-0048 inchangée ; R5-B et R5-C non commencés (R5-B : autorisation
distincte, avec tests de concurrence, ordre des verrous et investigation de R-2).

Risques résiduels et limites de tests conservés :
- **R-1** (activations inertes des nouveaux sites Restaurant / Pizzeria, effectives au passage à
  `available`) : à arbitrer séparément ; aucune remise à `false` future programmée.
- **R-2** (interblocage possible des écritures de commandes contre désactivation d'un module ou
  changement de profil) : hypothèse non démontrée ; tests pas à pas obligatoires en R5-B, y
  compris sur les commandes sans table ; aucune correction hors périmètre sans autorisation.
- **E2E** : parcours des tables non jouables tant que le module est `planned` (comme les
  commandes) ; couverture par les tests d'API, de concurrence et de migration.
- **Suite backend locale** : exécutée une fois complète (1 354 passés, 3 échecs attendus des
  modèles de rôles et du registre, ajustés puis relancés sur leurs fichiers : 22 passés) ; non
  relancée intégralement en local ensuite, couverte par la CI sur `4d7ffbe`. Vitest et E2E non
  exécutés localement (aucun changement frontend) ; tests frontend joués par la CI.
- **Licences** : vérifié avec les plans synchronisés depuis `plans.toml` ; en production, suppose
  `catalog sync` exécuté (plans en base conformes).
- **À faire en R5-C** : traductions `errors.json` des sept codes d'erreur ; adresses réelles des
  zones sous `/restaurant/tables/zones`.

## R5-B — intégration des tables aux commandes : conception et arbitrages (non implémentée)

À reporter avec le reste de R5 (`RESTAURANT.md` §7 notamment). Implémentation non autorisée à ce
stade ; `restaurant.orders` et `restaurant.tables` restent `planned` ; politique E.1 inchangée.

- **R-2 confirmé dans le code existant (commandes SANS table)** — diagnostic pas à pas hors
  dépôt (transaction « portillon » sur le compteur du jour, attentes vérifiées dans `pg_locks`
  / `pg_stat_activity`), deux exécutions identiques :
  - D1 : création d'une commande contre désactivation de `restaurant.orders` → interblocage
    (`40P01`, victime : la reconfiguration, erreur 500 non traitée) ;
  - D2 : création contre changement de profil arrêtant les commandes → même résultat ;
  - cause : la création prend les réglages du site en partage PUIS le site en partage de clé
    (contrôle de la clé étrangère à l'insertion de la commande), la reconfiguration prend le
    site en exclusif PUIS les réglages en exclusif (empreinte des commandes) ;
  - écartés par test : ajout de lignes (D3) et prise en charge (D4) contre désactivation
    (attente puis `409 module_has_open_operations`) ; ordre proposé simulé (D6) : la
    reconfiguration attend dès le verrou du site, sans interblocage ;
  - en production, non déclenchable tant que les routes des commandes ne sont pas montées.
- **AR-1 (accepté)** : TOUTE création de commande, avec ou sans table, prend le site en partage
  de clé (`FOR KEY SHARE`) AVANT les réglages du site. D1 et D2 seront rejoués avec le vrai code
  (sans table explicitement ; attente réelle ; absence de `40P01`) ; l'ordre des verrous sera
  vérifié sur tous les chemins concernés. Ces tests ne prouvent pas l'absence générale
  d'interblocage.
- **AR-2 (hors périmètre, NON INVESTIGUÉ)** :
  - règlement avec paiement en espèces contre reconfiguration de la caisse (l'empreinte de la
    caisse prend ses réglages en exclusif sous le verrou du site ; ordre côté règlement non
    vérifié) ;
  - opérations de vente, de point de vente et de stock contre un changement de profil arrêtant
    leurs modules ;
  - changements d'abonnement ou de licence par la console, hors verrou du site (course possible
    sur les capacités, pas un interblocage).
  Aucune modification de ces opérations dans R5-B.
- **AR-3 (accepté)** : l'API du plan de salle (états Libre / Occupée / À régler, priorité,
  commandes multiples, commandes qui occupent encore une table : B1 et C2 de la conception) fait
  partie de R5-B ; l'interface graphique reste en R5-C.
