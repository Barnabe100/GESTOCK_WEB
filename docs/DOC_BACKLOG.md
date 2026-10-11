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
