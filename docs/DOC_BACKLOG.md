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

### R5-B / B-1 — verrou du site pour toute création, table facultative à la création

- **Ordre des verrous de la création** (à reporter dans `RESTAURANT.md` §7) : verrou consultatif
  d'idempotence → **site en partage de clé** (`lock_site_key_share`, `FOR KEY SHARE`) → réglages
  du site (partage) → table (partage, port des tables, si une table est fournie) → compteur du
  jour → insertion. Attention : en SQLAlchemy, `with_for_update(key_share=True)` produit
  `FOR NO KEY UPDATE` ; le partage de clé exige `read=True, key_share=True` (erreur détectée par
  les tests pas à pas avant le commit).
- **R-2, résultats avec le vrai code** (`tests/test_restaurant_orders_locking.py`) : D1 (création
  sans table / désactivation des commandes) et D2 (création sans table, puis avec table /
  changement de profil) — la reconfiguration attend au verrou du site (constaté dans
  `pg_stat_activity`), aucune erreur ni `40P01`, résultats `201` puis `409`. Ordre inverse : la
  création attend dès son premier verrou (site), sans interblocage. Ces scénarios ne prouvent
  pas l'absence générale d'interblocage.
- **Constat hors périmètre B-1 (préexistant, non corrigé)** : une création peut valider APRÈS
  une désactivation de `restaurant.orders` validée pendant qu'elle attendait (capacités lues au
  début de la requête, jamais recalculées pour le module des commandes) : commande ouverte sur un
  site où le module vient d'être désactivé (observé : désactivation `204`, création `201`, une
  commande ouverte, module désactivé). Même nature pour les permissions lues en début de
  requête. Pas un interblocage. Le module des TABLES, lui, est recalculé après les verrous
  (`tables_unavailable`). Correction proposée (recalcul de l'effectivité des commandes après le
  verrou du site) soumise à autorisation — **autorisée et livrée** (correction complémentaire
  ci-dessous).
- **Codes d'erreur nouveaux** (traductions en R5-C) : `table_not_allowed_for_mode`,
  `tables_unavailable`, `table_inactive` (`table_not_found` existe depuis R5-A).

### R5-B / B-1 — correction complémentaire : effectivité des commandes revérifiée à la création

- **Correction** (création des commandes seulement) : après le verrou du site (partage de clé)
  et AVANT les réglages, les modules effectifs du site sont RECALCULÉS depuis la base
  (`OrderService._fresh_site_modules`, un seul calcul par requête, module planifié jamais
  effectif) ; si `restaurant.orders` n'est plus effectif, la création est refusée
  (`403 module_unavailable`, code de `require_module`) et rien n'est écrit. Le même calcul sert
  à l'effectivité des tables. Mécanisme général des capacités, permissions communes et autres
  modules : inchangés.
- **Tests pas à pas** (`tests/test_restaurant_orders_locking.py`) : désactivation d'abord (tenue
  sur sa ligne `site_modules`) → la création attend au verrou du site (constaté), puis
  `403 module_unavailable` ; état final vérifié EN BASE (module désactivé, aucune commande en
  cours ; avec table : aucune commande rattachée), sans table puis avec table. Création d'abord
  → `201` puis `409 module_has_open_operations`, module toujours actif, une commande. D1 / D2
  rejoués : aucune erreur ni `40P01`.
- **Risque résiduel** : les PERMISSIONS (et les autres capacités, ex. abonnement) restent lues au
  début de la requête ; seule l'effectivité des modules des commandes et des tables est
  revérifiée après le verrou. Les changements d'abonnement ou de licence par la console ne
  prennent pas le verrou du site (AR-2, non investigué).
- **CI de `e187111` (hors périmètre, non corrigé)** : première exécution du job Frontend en échec
  dans `frontend/src/pages/SignupPage.test.tsx:254` (`findByRole('radiogroup', « Secteur
  d'activité »)` au-delà de son délai de 3 s), alors qu'aucun fichier frontend n'a changé depuis
  `63acc53` (CI verte). Local : ce fichier 5/5, Vitest complet 463/463 ; job relancé une fois :
  vert (run `38603202130`, tentative 2). Test intermittent sous charge du runner, à examiner à
  part.

### R5-B / B-2 — changement et retrait de la table d'une commande

- **Route** `PUT /restaurant/orders/{id}/table`, corps `{table_id: UUID | null, reason?}` :
  `table_id` obligatoire (`null` explicite = retrait), motif facultatif (A5). Permission
  `restaurant.orders.order.create` vérifiée sur le site de la commande (A8) ; portée commune
  des écritures (`404 order_not_found` si le site n'est pas lisible, `403 site_mismatch` si un
  autre site est sélectionné).
- **Ordre des verrous** (à reporter dans `RESTAURANT.md` §7) : site en partage de clé
  (`FOR KEY SHARE`) → table cible en partage (port des tables, association seulement) →
  commande en exclusif → contrôles → écriture. Le mode de service (fixé à la création) est lu
  avant le verrou de la commande pour respecter l'ordre table → commande ; la contrainte
  `table_only_on_site` le garantit aussi en base. Le retrait ne verrouille aucune table.
- **Association ou changement** : mêmes contrôles qu'à la création — `422
  table_not_allowed_for_mode` (comptoir, à emporter), `422 tables_unavailable`
  (`restaurant.tables` non effectif, recalculé après le verrou du site ; module planifié :
  jamais), `404 table_not_found` (autre site, autre entreprise, inconnue), `422 table_inactive`.
  **Retrait** permis quand `restaurant.tables` n'est plus effectif (B4).
- **Commandes finales refusées** : `409 order_closed`, `409 order_cancelled` (contrôle commun
  `_require_open` : une commande `PENDING_CONFIRMATION`, inexistante avant R7, serait refusée
  `409 order_not_open`). Une commande réglée encore ouverte (non servie) peut changer de table.
- **Même table (ou retrait sans table)** : aucune écriture (ni version, ni évènement, ni audit).
  La table demandée est validée AVANT le verrou de la commande (ordre des verrous) : ses refus
  (mode, module, table) précèdent donc ceux de l'état de la commande (même table devenue
  indisponible, commande finale avec une table invalide), toujours sans écriture.
- **Historique** : évènement `TABLE_CHANGED` (`table_id`, `table_label`, `zone_name` et
  `previous_*`, libellés au moment du changement, conservés après renommage) et audit
  `restaurant_order.table_changed` (mêmes données + motif) ; `version` incrémentée. Aucune
  copie du libellé dans la commande (A7).
- **Concurrence** (`tests/test_restaurant_orders_locking.py`, attentes constatées dans
  `pg_locks` / `pg_stat_activity`, aucune erreur ni `40P01`) :
  - changement d'abord / désactivation de la table cible : la désactivation attend la table,
    puis `409 table_has_open_orders` ; désactivation d'abord : le changement attend la table,
    puis `422 table_inactive`, commande inchangée ;
  - retrait / désactivation de l'ancienne table : aucune attente croisée ; tant que le retrait
    n'est pas validé, la désactivation voit la commande et est refusée ; ensuite elle aboutit ;
  - changement d'abord / changement de profil : le profil attend au verrou du site, puis
    `409` (aperçu périmé ou bloqué) ; profil d'abord (vers un autre profil de restauration) :
    association et retrait attendent au verrou du site, puis s'appliquent ;
  - règlement d'abord (commande tenue, attente au stock) : le changement attend la commande,
    puis s'applique à la commande réglée ouverte ; changement d'abord : le règlement suit ;
  - clôture d'abord (règlement d'une commande servie) : `409 order_closed`, table inchangée ;
    changement d'abord puis service qui clôt : la commande close garde la nouvelle table.
- **Constat** : un changement de profil arrêtant les commandes est refusé
  (`profile_change_blocked`) tant qu'une commande est ouverte ; tous les profils de restauration
  proposent les tables. Le retrait « hors module » n'est donc atteignable que par un module
  planifié (production, E.1) ou une activation retirée par une autre voie que la désactivation
  (refusée par A6) : testé en simulant l'état en base.
- **Risque résiduel** : la permission et le module des commandes sont lus au début de la
  requête (aucun recalcul de `restaurant.orders` pour le changement de table, comme les autres
  opérations sur une commande existante) ; seuls les modules des tables sont recalculés pour une
  association. Mécanisme général des capacités inchangé. Aucune migration ; interface en R5-C ;
  champ `table` de `OrderOut` en B-3.

### R5-B / B-2 — validation (`2de09f0`)

- Validé sur la base du compte rendu et de la CI verte. Décisions : protections existantes du
  changement de profil conservées (B-2 ne les contourne pas) ; **ordre des refus conservé** — la
  validation de la table peut précéder celle de l'état final de la commande (une table invalide
  ou inactive peut donc être refusée avant `order_closed`), aucun état métier modifié en cas de
  refus ; règle « même table : aucune écriture » conservée ; `PENDING_CONFIRMATION` non anticipé
  (examiné en R7). Risques résiduels (permissions et effectivité de `restaurant.orders` lues en
  début de requête ; AR-2) toujours ouverts.

### R5-B / B-3 — lecture enrichie, filtre par table, plan de salle

- **`OrderOut`** : `table_id` et `table` (`{id, label, zone_name, is_active}`), libellé et zone
  COURANTS lus par le port des tables (`labels`, un appel par page) — aucune copie dans la
  commande (A7), l'affichage d'une commande close suit les renommages (B6), l'historique garde
  les libellés d'alors. Lu même quand `restaurant.tables` n'est plus effectif (B4). Visible pour
  qui lit la commande (`order.view`), sans exiger `restaurant.tables.table.view`.
- **Filtre `table_id`** de `GET /restaurant/orders` (B7) : égalité, combiné aux autres filtres
  (état « en cours » par défaut) ; table inconnue, d'un autre site ou d'une autre entreprise :
  liste vide. Recherche par libellé toujours reportée.
- **Plan de salle** `GET /restaurant/tables/board` (module des tables, `table.view`, déclaré avant
  `/{table_id}`), pour UN site : site sélectionné, sinon `site_id` ; `422 site_required`,
  `403 site_mismatch`, `403 site_access_denied` (site non lisible : module non effectif ou
  permission absente sur ce site, autre entreprise) ; site sélectionné sans le module :
  `403 module_unavailable`. Non paginé (tables d'un site). Tables actives, plus toute table
  inactive encore occupée (défense : aucune commande ne disparaît du plan), dans l'ordre des
  zones puis des tables (sans zone en dernier) ; compteurs par état.
- **États (B1 / C2)**, calculés à la lecture par l'interface publique des commandes
  (`occupying_orders`, `OccupyingOrder.to_settle`) : une commande `PENDING_CONFIRMATION` ou `OPEN`
  occupe sa table ; « en attente de règlement » = `OPEN`, `UNSETTLED`, `prep_status = SERVED` ;
  table **Occupée** si au moins une commande occupante n'est pas en attente de règlement (à
  préparer ou à servir, réglée non servie, à confirmer, sans ligne active), **À régler** si
  toutes le sont, **Libre** sinon ; annulée, refusée ou close : table libérée. Le filtre « À
  régler » de la liste (`unsettled_served` : au moins une ligne servie) reste inchangé (B1).
- **Droits** : détail des commandes (numéro, nom d'appel, statuts, création) seulement avec
  `restaurant.orders.order.view` résolue pour le site du plan (`orders_visible`, sinon
  `orders: null`). **Choix à confirmer** : le nombre de commandes et l'ancienneté de la plus
  ancienne (`order_count`, `occupied_since`) restent visibles avec `table.view` seule (A1 : âge et
  nombre affichés) ; aucun montant n'est exposé par le plan.
- **Tests** (`tests/test_restaurant_tables_board.py`, 12) : libellé courant (renommage, commande
  close), non-régression des commandes sans table (trois modes), lecture hors module (état
  simulé en base), filtre, ordre et contenu du plan, états et priorité (plusieurs commandes,
  réglée non servie, close, annulée), à confirmer et refusée (états simulés en base avant R7),
  commande sans ligne active, table inactive occupée (simulée), droits (`order.view` par site,
  sans `table.view`), portée des sites, isolation entre entreprises, route absente en production.
- Aucune migration ; aucune écriture ; interface en R5-C ; codes d'erreur sans nouveauté.

### R5-B / B-3 — validation (`532a5a2`) et arbitrages

- Validé sur la base du compte rendu et de la CI verte ; `532a5a2` reste la référence de la
  livraison B-3. Arbitrages confirmés (comportement livré conforme, aucun code modifié) :
  1. `order_count` et `occupied_since` visibles avec `restaurant.tables.table.view` seule, sans
     `restaurant.orders.order.view`, le détail individuel des commandes restant masqué
     (`orders: null`) ;
  2. `table_id` reste exposé dans `OrderOut`, en complément de l'objet `table` ;
  3. libellé et informations de la table d'une commande consultables avec `order.view`, sans
     exiger `table.view` ; le plan de salle et la configuration restent protégés par les
     permissions des tables ;
  4. une table inactive encore occupée reste visible sur le plan par précaution — **situation
     anormale** que les routes normales ne créent pas (désactivation refusée avec des commandes
     en cours, A6 ; rattachement refusé à une table inactive) ;
  5. plan de salle non paginé et limité à un site dans cette version ; pagination à réévaluer
     selon les besoins et la volumétrie.

### R5-B / B-4 — vérification globale et clôture technique

- **Ordres de verrouillage réels** consignés dans `RESTAURANT.md` §7 (tableau par opération :
  création, ajout de lignes, prise en charge, transitions, annulations, règlement, changement de
  table, désactivation de module, changement de profil, tables et zones). Affirmations inexactes
  corrigées : l'insertion des avis n'est pas « sans verrou » (clé étrangère vers le site :
  partage de clé) ; les commandes prennent le site en partage de clé (jamais en exclusif).
- **Interblocage confirmé (antérieur à R5-B)** — diagnostic pas à pas hors dépôt, puis tests :
  (1) règlement ou « prête » tient la commande puis prend le site en partage de clé (insertion de
  la vente ou de l'avis) ; (2) ajout de lignes ou prise en charge tient les réglages en partage et
  attend la commande ; (3) désactivation de `restaurant.orders` tient le site en exclusif et
  attend les réglages en exclusif → `40P01` (victime observée : (2), erreur 500). Reproduit à
  l'identique sur `4d7ffbe` (R5-A, sans code de R5-B) ; non déclenchable en production tant que le
  module est planifié.
- **Correctif autorisé (option A), commit `6b25162`** : `add_lines` et `claim` prennent le site en
  partage de clé (`lock_site_key_share`) AVANT les réglages ; aucune autre opération modifiée
  (transitions et règlement inchangés ; option B — généralisation — non autorisée).
- **Tests de non-régression** (`tests/test_restaurant_orders_locking.py`, attentes constatées
  dans `pg_locks` / `pg_stat_activity`, absence de `40P01`, état final vérifié en base) :
  - ordre site → réglages : site tenu en exclusif, l'ajout de lignes et la prise en charge
    attendent au verrou du site sans aucun verrou de ligne sur les réglages ni la commande ;
  - S1 (règlement + ajout de lignes + désactivation) : la désactivation attend au verrou du
    site ; `201`, `409 order_settled`, `409 module_has_open_operations` ; une vente, une ligne ;
  - S1b (règlement + prise en charge + désactivation) : `201`, `200`, `409` ; commande prise par
    le membre attendu ;
  - S2 (« prête » + ajout de lignes + désactivation) : `200`, `200`, `409` ; un avis, deux lignes ;
  - sans le correctif, ces quatre tests échouent (vérifié) ; S3 / S4 du diagnostic hors dépôt
    (règlement + changement de table + changement de profil ou désactivation des tables) : aucun
    interblocage, exécutés sur `532a5a2` seulement (avant le correctif, non rejoués après).
- **Résultats** : suite backend complète locale `1408 passed` (39 min 47 s, sur `6b25162`) ;
  `1404 passed` sur `532a5a2` avant le correctif ; fichier de concurrence rejoué deux fois
  (`23 passed` ×2) ; Ruff, `ruff format --check`, Mypy, `alembic check` propres ; Vitest local
  `463 passed` (sur `532a5a2`, frontend inchangé depuis) ; CI verte sur `532a5a2` et `6b25162`
  (4/4 jobs). Exécutés seulement en CI : Signing Service, configuration Docker Compose, build du
  frontend.
- **Limites et risques résiduels** : les scénarios vérifiés ne démontrent pas l'absence générale
  d'interblocage ; transitions et règlement tiennent toujours la commande avant le site (cycles
  connus rompus, autres combinaisons non explorées) ; **AR-2 non investigué** (règlement en
  espèces contre reconfiguration de la caisse ; vente, POS et stock contre un changement de
  profil ; console hors verrou du site) ; **courses sur les permissions, abonnements et licences**
  (capacités et permissions lues en début de requête ; seuls les modules des commandes, à la
  création, et des tables, à l'association, sont recalculés après le verrou du site) : non
  investiguées ; **E2E impossibles** tant que `restaurant.orders` et `restaurant.tables` sont
  `planned` ; test frontend intermittent `SignupPage.test.tsx` (voir B-1) non revu.
- **État** : aucune migration en R5-B ; politique E.1 d'ADR-0048 inchangée ; modules
  `restaurant.orders` et `restaurant.tables` toujours `planned` en production ; interface en
  R5-C (non commencée).

## R5-C — interface des tables et du plan de salle

### R5-C / C-1 — configuration des zones et des tables (frontend seul)

- **Module frontend `restaurant_tables`** (`frontend/src/modules/restaurant_tables/`), inscrit au
  registre (`app/modules.ts`) : entrée « Tables » (`nav.restaurantTables`, rubrique
  « restaurant », déjà placée par le profil UX Restaurant), route `/restaurant/tables`
  (`restaurant.tables.table.view`). Affiché seulement si le backend déclare le module
  **disponible** : il reste invisible en production tant que `restaurant.tables` est `planned`
  (test de registre ajouté).
- **Page en deux onglets** (sur le modèle du menu) : « Tables » (recherche, filtre d'état, filtre
  de zone avec « Sans zone » → `without_zone=true`, tri serveur par libellé, zone, places, ordre,
  site) et « Zones » (recherche, filtre d'état, tables actives / total). Création, modification,
  activation et désactivation (confirmation pour désactiver) avec
  `restaurant.tables.table.manage` ; lecture seule sinon (le backend reste la frontière de
  sécurité). Site d'une nouvelle saisie : site sélectionné, sinon seul site, sinon choisi.
- **Contrats** : `PUT /restaurant/tables/{id}` avec le corps COMPLET (`zone_id`, `label`,
  `seats`, `sort_order`) ; `PUT /restaurant/tables/zones/{id}` (`name`, `sort_order`). La zone
  actuelle d'une table reste proposée même désactivée (le serveur accepte de la conserver et
  refuse seulement d'en choisir une autre inactive) ; changer de site à la création efface la
  zone choisie. Bornes reprises du serveur : libellé 40, nom de zone 100, places 1 à 999, ordre 0
  à 100 000 ; unicités et protections laissées au serveur, refus traduits.
- **Traductions** : les dix codes d'erreur des tables et des zones (`zone_not_found`,
  `table_not_found`, `zone_name_taken`, `table_label_taken`, `zone_has_active_tables`,
  `table_has_open_orders`, `zone_inactive`, `table_not_allowed_for_mode`, `tables_unavailable`,
  `table_inactive`) ; les trois derniers serviront en C-3. Libellés des permissions
  `restaurant.tables.table.view` / `.manage` ajoutés au catalogue `permissions` (exigés par le
  test du registre `app/modules.test.ts`, qui a détecté leur absence ; utilisés aussi par
  l'éditeur de rôles).
- **Non couvert** (sous-paliers suivants) : plan de salle (C-2), table dans les commandes (C-3),
  widget du tableau de bord (C-4). E2E impossibles tant que les modules sont `planned`.

### R5-C / C-2 — plan de salle (frontend seul)

- **Onglet « Plan de salle »**, par défaut, dans la page Tables (`FloorPlan.tsx`) :
  `GET /restaurant/tables/board?site_id=` pour UN site (site sélectionné, sinon seul site, sinon
  choisi : aucun appel avant le choix) ; actualisé toutes les 15 s (`BOARD_REFRESH_MS`).
- Cartes groupées par zone **dans l'ordre du serveur** (sans zone en dernier) ; état Libre /
  Occupée / À régler et compteurs **tels que calculés par le serveur** (aucun recalcul dans
  React) ; places, nombre de commandes et début d'occupation (fuseau du tenant) ; avertissement
  informatif si plusieurs commandes (A1) ; table inactive encore occupée signalée (situation
  anormale, arbitrage 4 de B-3).
- Détail des commandes (numéro, nom d'appel, statut si non ouverte, préparation ou « tout servi,
  à régler ») seulement si le serveur le fournit (`orders_visible`) ; liens vers la fiche et vers
  la liste filtrée (`/restaurant/orders?table_id=`) seulement avec `restaurant.orders.order.view`
  et le module des commandes ; sinon nombre et ancienneté seuls (arbitrage 1 de B-3).
- États chargement, erreur traduite (`site_access_denied`…), plan vide. Styles `sm-floor-*`
  (jetons existants, grille adaptative). Aucun changement backend.

### R5-C / C-3 — la table dans les commandes (frontend seul)

- **Contrat** : type `Order` complété de `table_id` et `table` (`{id, label, zone_name,
  is_active}`, libellé et zone COURANTS) ; `OrderCreateInput.table_id` ; mutation
  `PUT /restaurant/orders/{id}/table` (`{table_id | null, reason}`) ; création et changement de
  table rafraîchissent aussi le plan de salle.
- **Choisir une table** exige le module des tables et `restaurant.tables.table.view` (un module
  planifié n'accorde aucune permission ; `hasModule` seul compte aussi les activations inertes).
  Sans eux, aucune table n'est proposée, mais **le retrait reste possible** (B4).
- **Saisie** : table facultative, tables ACTIVES du site, seulement « sur place » ; effacée au
  changement de mode ou de site, jamais envoyée hors « sur place » (`table_id: null`) ;
  avertissement non bloquant si la table a déjà des commandes en cours (A1, d'après le plan de
  salle).
- **Fiche** : table (libellé · zone) et « Table désactivée » ; « Associer / Changer de table »
  pour une commande OUVERTE « sur place » avec `order.create` ; dialogue : nouvelle table (la
  table actuelle n'est pas proposée), motif facultatif (A5), « Retirer la table » ; commande
  finale, autre mode ou sans `order.create` : aucune action ; refus du serveur traduit dans le
  dialogue (qui reste ouvert).
- **Historique** : `TABLE_CHANGED` « T1 (Salle) → T2 » / « → sans table » et table initiale de
  `CREATED`, avec les libellés ENREGISTRÉS dans l'évènement (jamais le libellé courant, A7 / B6).
- **Listes** : colonne « Table » (À régler, Historique), table sur les cartes du suivi ; filtre
  par table (B7) partagé par les trois onglets et porté par l'URL (`?table_id=`, lien « Voir les
  commandes de la table » du plan de salle), visible avec la consultation des tables.
- Test existant de la saisie mis à jour : le corps de la création porte désormais
  `table_id: null` hors « sur place » (accepté par le serveur, valeur par défaut) ; fixture de
  commande complétée des champs de `OrderOut` depuis B-3.

### R5-C — bilan et limites

- Livré : C-1 (`c5151b4`), C-2 (`e12e768`), C-3. Aucun changement backend, aucune migration,
  aucune dépendance ; `restaurant.orders` et `restaurant.tables` restent `planned` (écrans
  invisibles en production), politique E.1 inchangée.
- **E2E toujours reportés** : impossibles tant que les modules sont `planned` (scénario à écrire
  à leur passage à `available` : configuration → plan de salle → commande sur une table →
  changement → règlement → table libérée). Vérification par Vitest (API simulée) seulement.
- Limites connues : listes de choix (zones, tables, filtre) limitées à 100 éléments ; ticket de
  retrait sans table (le `TicketOut` du serveur ne la porte pas) ; widget du tableau de bord
  `restaurant.tables:occupied` non réalisé (C-4, non autorisé).

## Refonte UX de la barre latérale (frontend seul)

À reporter dans `DESIGN_SYSTEM.md` (navigation) lors d'une prochaine mise à jour autorisée.

- **Présentation seulement** : rubriques, ordre et entrées viennent toujours de
  `buildNavigationSections` (modules effectifs et implémentés, permissions, fonctionnalités,
  profil UX du site actif). Registre des modules, règles de disponibilité, routes, URL, site
  choisi : inchangés. Aucune rubrique ajoutée (pas de « Tiers » : la répartition reste celle des
  rubriques `navGroups` existantes), aucun module `planned` affiché.
- **Rubriques** (`layouts/SidebarNav.tsx`) : « Tableau de bord » = lien direct ; chaque autre
  rubrique = bouton natif (icône, libellé, flèche indicatrice de sous-menu, `aria-expanded`,
  `aria-controls`) ; la rubrique de la page courante est mise en évidence et la page courante
  porte `aria-current="page"`. Icônes : **PrimeIcons** (système du projet, aucune dépendance
  Lucide) — Tableau de bord `pi-objects-column`, Catalogue `pi-box`, Stock `pi-warehouse`,
  Ventes `pi-shopping-cart`, Caisse `pi-wallet`, Restaurant `pi-shop` (pas d'icône
  « couverts »), Rapports `pi-chart-bar`, Atelier `pi-wrench`, Administration `pi-cog` ;
  repli `pi-folder`.
- **Ordinateur** (pointeur précis, `(hover: hover) and (pointer: fine)`) : panneau contextuel
  adjacent (position fixe, au-dessus du contenu : `.sm-sidebar` `z-index: 950`) ouvert au
  survol ; fermeture et bascule vers une autre rubrique **différées de 250 ms** (le panneau reste
  ouvert pendant le trajet du curseur, même en diagonale à travers d'autres rubriques). Clic :
  ouvre et **épingle** (ne se ferme plus au départ du curseur), second clic, `Échap` (focus rendu
  au bouton), clic extérieur ou navigation referment. Écran tactile sans survol : clic seulement.
- **Mode compact** : bascule en haut de la barre (« Réduire / Développer le menu »), largeur
  4,5 rem (le contenu reprend la place), icônes seules, libellés conservés pour les lecteurs
  d'écran ; infobulle (PrimeReact `Tooltip`) au survol d'un lien direct et au focus **clavier**
  d'une rubrique ; au survol d'une rubrique, son panneau s'ouvre, titré par son nom (aucune
  infobulle superposée).
- **Tablette / mobile** (≤ 1024 px, tiroir existant) : toujours développé (bascule masquée),
  rubriques dépliées sur place au toucher, celle de la page courante dépliée d'office, tiroir
  refermé après le choix d'une destination ; largeur bornée à 85 vw, aucun débordement.
- **Persistance** : mécanisme existant réutilisé (`tabStorage`, `sessionStorage`, clé
  `sm.sidebar`) — préférence conservée dans l'onglet comme le site choisi, perdue à la fermeture
  de l'onglet (aucun stockage nouveau).
- Tests : `layouts/SidebarNav.test.tsx` (14 tests : points 1 à 13 et accessibilité) ;
  `Sidebar.test.tsx` et `SiteExperience.test.tsx` adaptés (entrées présentes mais masquées dans
  les sous-menus refermés). E2E : aide `followMenu` (ouvre le tiroir et la rubrique, puis suit
  l'entrée) et `sideMenu` dans `e2e/support.ts` ; les assertions d'absence ou de présence d'une
  entrée portent sur le menu, sous-menus refermés compris (`includeHidden`).

## Livraison de `restaurant.orders` et `restaurant.tables` (étape 1 de la finalisation)

Décisions validées par l'utilisateur (audit des modules métier) : tables et commandes passent à
`available` après E2E ; libération explicite ; délai entre deux prises 5 min par défaut, 5 ou 10 ;
photos du menu, paiements électroniques et automobile reportés. À reporter dans `RESTAURANT.md`
(§2, §5, §8, §11, §12), `API.md`, `DATA_MODEL.md`, ADR-0049 (décisions de livraison) et
`CLAUDE.md` (phase en cours) :

- **Libération** : `POST /restaurant/orders/{id}/release` (`{reason?}`, motif facultatif, 500
  caractères), permission EXISTANTE `restaurant.orders.order.claim` sur le site de la commande ;
  seul le RESPONSABLE libère (`409 order_not_claimed_by_you`, aussi pour une commande libre ;
  `assigned_name` joint) ; commande finale : `order_closed` / `order_cancelled`. Effets :
  responsable, heure et auteur de la prise remis à `NULL`, `version + 1`, évènement `RELEASED`
  (auteur, heure, motif, `previous_user_id`), audit `restaurant_order.released` (motif, nom,
  `held_since`). Verrou de la commande seul (aucun cycle avec la prise, §7). La libération NE
  remet PAS à zéro le délai entre prises de l'employé (lu dans ses `CLAIMED`). Le gérant
  continue de réattribuer avec `order.reassign` (motif obligatoire) ; aucune libération d'autrui.
- **Délai entre deux prises** (≠ protection d'une commande prise, inchangée : 0 à 1440, défaut
  5) : `claim_cooldown_minutes` ∈ {5, 10}, défaut 5 (réglages créés à l'activation, API
  `Literal[5, 10]`, contrainte `claim_cooldown_choice`). Toujours appliqué sous le verrou
  consultatif de l'employé (prises simultanées : une seule réussit).
- **Migration 0047** : type d'évènement `RELEASED` ; contrainte du délai remplacée ; valeurs
  existantes ramenées sans assouplir (0 à 5 → 5, au-delà → 10) ; descente refusée si une
  libération existe.
- **Migration 0048** (aucun schéma) : activations héritées de `restaurant.orders` et
  `restaurant.tables` remises à `false` sur les sites EXISTANTS (activation explicite par
  `organization.module.manage` sur le site) ; nouveaux sites : défauts de LEUR profil dans LEUR
  abonnement (Restaurant : commandes et tables ; Maquis, Bar… : commandes, tables facultatives).
  Descente sans effet. Manifestes sans `status=PLANNED` ; plans STANDARD et ENTREPRISE
  inchangés (les deux incluent déjà les deux modules).
- **Interface** : bouton « Libérer » sur la fiche (responsable seulement, dialogue au motif
  facultatif, refus traduit), réglage du délai par choix 5 / 10 minutes, évènement « Libérée ».
- **Historique** : trié par heure de la REQUÊTE (`occurred_at`, puis identifiant) ; deux actions
  concurrentes peuvent y apparaître dans l'ordre de leurs requêtes, l'ordre d'écriture suivant le
  verrou de la commande (comportement existant, constaté par le test de concurrence
  libération / prises).
- **Tests** : registre de test sans effet (`orders_available`, `tables_available`) ;
  `orders_planned` / `tables_planned` / `planned_client` / `pclient` SIMULENT la période
  « Bientôt disponible » pour garder la couverture des règles des modules planifiés ; E2E
  `restaurant-tables.e2e.ts`, `restaurant-orders.e2e.ts`, appui `restaurant-support.ts` ;
  `profiles.e2e.ts` et `restaurant-menu.e2e.ts` alignés (assertion obsolète de
  `profiles.e2e.ts:188` remplacée : `/restaurant/tables` s'ouvre, `/restaurant/kitchen`
  « Page introuvable »).
- **Correctif barre latérale** (refonte `318c9b1`) : transition de largeur de `.sm-sidebar`
  retirée (barre écrasée pendant un redimensionnement, constatée en capture pleine page).

## Palier R6 — module Cuisine (`restaurant.kitchen`, ADR-0050)

Décisions détaillées dans [ADR-0050](adr/0050-cuisine-postes-de-preparation.md). À reporter
dans `RESTAURANT.md` (§2, §5, §8, §11, §12, §13), `API.md`, `DATA_MODEL.md` et `CLAUDE.md`
(phase en cours) :

- **Données (migration 0049)** : `restaurant_prep_stations` (poste d'UN site, nom 100 unique
  par site sans casse, ordre d'affichage, désactivation, jamais supprimé) ;
  `restaurant_station_routes` (affectation d'une section OU d'un élément du menu à un poste,
  une par cible, tenue par le module Cuisine : aucune écriture dans les tables du menu) ;
  `restaurant_order_lines.station_id` (FK composite site, `RESTRICT`) ;
  `restaurant_orders.priority` (`NORMAL` / `HIGH` / `URGENT`) ; types d'évènement
  `PRIORITY_CHANGED` et `LINES_ROUTED`. RLS `ENABLE` + `FORCE`, droits minimaux (colonnes
  modifiables listées), descente refusée dès qu'une donnée de cuisine serait perdue.
- **Routage** : par le port `restaurant_orders/station_port.py` (les commandes ne dépendent
  pas de la cuisine), seulement si la cuisine est EFFECTIVE sur le site ; à la création et à
  l'ajout de lignes, UNE fois par ligne : affectation de l'élément, sinon de sa section, sinon
  « Sans poste » ; poste désactivé ignoré ; postes verrouillés en partage. Correction manuelle
  `PUT /restaurant/kitchen/orders/{id}/route` (lignes non finales, poste actif du MÊME site ou
  aucun, motif facultatif, évènement `LINES_ROUTED`, audit `restaurant_order.lines_routed`).
- **Priorité** : `PUT /restaurant/kitchen/orders/{id}/priority` (commande ouverte, motif
  facultatif, évènement `PRIORITY_CHANGED`, audit) ; écran trié par priorité décroissante PUIS
  ancienneté (réception), l'ancienneté restant affichée : priorité ≠ ancienneté.
- **Écran cuisine** `GET /restaurant/kitchen/board?site_id=…[&station_id=…|&unrouted=true]` :
  tickets = commande ouverte × poste ; colonne = état de la ligne la MOINS avancée (Reçue, En
  préparation, Prête) ; table ou mode, nom d'appel, lignes et quantités, note, heure de
  réception, temps écoulé (calculé par l'écran), poste, responsable ; actualisation toutes les
  10 s ; exige aussi `restaurant.orders.order.view` sur le site. Préparer reste l'action des
  commandes (`order.prepare`, lignes du ticket) ; SERVIR reste au service (écran des
  commandes), cohérent avec le fonctionnement des serveurs.
- **Multi-postes** : la commande n'est prête que lorsque toutes ses lignes non finales le sont
  (règle existante) ; un évènement de préparation par poste et une notification « prête » PAR
  POSTE (`part = true`, `station_name`) quand toutes les lignes de CE poste le sont.
- **Désactivation** : poste refusé tant que des lignes en cours y sont routées
  (`409 station_has_open_lines`, verrou exclusif) ; module refusé avec des lignes routées en
  cours (`409 module_has_open_operations`, empreinte `site_footprint`).
- **Permissions** : `restaurant.kitchen.station.view` (read), `.station.manage` (admin),
  `.line.route` (write), `.priority.set` (write) ; Administrateur et Gestionnaire : toutes ;
  Vendeur : écran, priorité, poste d'une ligne ; Serveur : écran, priorité ; Préparateur :
  écran, poste d'une ligne ; Consultant : écran (`*.view`).
- **Interface** : pages « Cuisine » (`/restaurant/kitchen`, trois colonnes, une sous l'autre
  sous 800 px, filtre par poste mémorisé dans le navigateur) et « Postes de préparation »
  (`/restaurant/kitchen/stations` : onglets Postes et Affectations du menu) ; fiche d'une
  commande : priorité, colonne « Poste », action « Changer de poste », historique.
- **Livraison (migration 0050)** : aucun schéma ; activations héritées de `restaurant.kitchen`
  remises à `false` sur les sites EXISTANTS (activation explicite) ; nouveaux sites : défauts
  de leur profil (Restaurant, Pizzeria, Traiteur, Restauration rapide : activée ; Maquis, Bar,
  Café… : facultative). Plans STANDARD et ENTREPRISE inchangés (incluent déjà le module).
- **Non livré dans R6** : indicateur `restaurant.kitchen:ready` du tableau de bord (déclaré par
  le profil UX, aucun widget implémenté — comme ceux des tables et des commandes), affectation
  de membres à un poste (reportée, ADR-0050 K1), impression de bons par poste.

## Lot R6.5 — moyens de réception internationaux (ADR-0051)

Décisions et implémentation dans [ADR-0051](adr/0051-moyens-de-reception-internationaux.md)
(section « Implémentation R6.5 »). À reporter dans `PAYMENTS.md`, `API.md`, `DATA_MODEL.md` et
`CLAUDE.md` (phase en cours) :

- **Migrations** : 0051 (`ref_payment_providers`), 0052 (opérateur, pays, devise des moyens),
  0053 (`payment_method_accounts`), 0054 (confirmation et instantané des paiements). Descentes
  refusées dès qu'une donnée serait perdue.
- **API** : `GET /payment-providers?country=&currency=&kind=` ; `payment-methods` : champs
  `provider_code`, `provider_name`, `provider_label`, `country_code`, `currency`,
  `unavailable_reason`, `accounts` (avec `site_id`) ; `GET/POST
  /payment-methods/{id}/accounts`, `PATCH /payment-methods/accounts/{id}`,
  `POST /payment-methods/accounts/{id}/activate|deactivate` ; paiements (`PaymentCreate`) :
  `payment_method_account_id`, `received_confirmed` ; `PaymentOut` et reçu : `currency`,
  `confirmed_at`, `confirmed_by_name`, `provider_label`, `account_*`.
- **Codes d'erreur** : `payment_method_currency_mismatch`, `payment_confirmation_required`,
  `payment_account_unavailable`, `payment_account_not_allowed`, `payment_account_invalid`,
  `payment_account_type_not_accepted`, `payment_account_duplicate`,
  `payment_account_label_required`, `payment_account_not_found`, `payment_provider_unknown`,
  `payment_provider_mismatch`, `payment_provider_not_allowed`, `country_unknown`,
  `currency_unknown`.
- **Changement de comportement** : un paiement hors espèces exige désormais la confirmation
  explicite de la réception des fonds (API et interface) ; les intégrations qui créaient des
  paiements hors espèces par l'API doivent envoyer `received_confirmed: true`.
- **Catalogue** : vide au départ ; une entrée n'est ajoutée qu'après vérification de ses types
  d'identifiants et formats (`payment_providers.toml`, format commenté dans le fichier).
- **Non livré** : paiement dans le parcours QR (R7, décision complémentaire requise), passerelles
  et API d'opérateurs, webhooks, encaissement centralisé par TechNova, conversion de devises,
  traductions au-delà du français.
