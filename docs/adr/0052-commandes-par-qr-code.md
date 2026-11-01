# ADR-0052 — Commandes par QR Code (palier R7)

- **Statut** : Proposée — plan R7-A à R7-F validé par l'utilisateur le 2026-11-01 (décisions
  ci-dessous) ; livraison complète soumise à une validation fonctionnelle globale sur Ubuntu
- **Date** : 2026-11-01
- **Prolonge** : [ADR-0049](0049-restauration-commandes.md) (D9 — QR), ADR-0050 (cuisine),
  ADR-0051 (moyens de réception : **non intégrés** au parcours QR)

## Contexte

ADR-0049 D9 a fixé le principe : jeton opaque par point (table ou comptoir), stocké haché,
révocable par rotation ; entreprise et site déduits du jeton seulement ; menu public sans stock
ni coût ; confirmation explicite par défaut ; même moteur de commandes. L'audit de R7 a montré
que la création d'une commande exige un utilisateur connecté et que toute lecture en base
suppose une entreprise connue : le canal public demande un chemin distinct, sans contourner la
RLS ni dupliquer le moteur.

## Décisions de l'utilisateur (2026-11-01)

| Id | Décision |
| --- | --- |
| Q1 | `restaurant.qr` disponible dans **STANDARD et ENTREPRISE** (ENTREPRISE inchangée) ; règles de licence existantes : un site STANDARD déjà licencié ne l'obtient qu'à sa licence suivante |
| Q2 | Option A : jeton aléatoire de 256 bits, **empreinte seule** en base, QR affiché seulement à la création ou à la régénération ; réimprimer = régénérer, après confirmation explicite (« les anciens QR imprimés deviennent immédiatement invalides ») ; révocation immédiate côté serveur |
| Q3 | Segno côté serveur, SVG, téléchargement et impression (support de table, affiche de comptoir) ; **adresse de base dynamique** : configuration serveur centralisée (`SM_PUBLIC_APP_URL`, `SM_PUBLIC_ALLOWED_HOSTS`), jamais `Host` / `X-Forwarded-Host` / adresse du navigateur |
| Q4 | Plafonds réglables par site (30 lignes, 20 unités par ligne, 3 commandes en attente par point au départ), plafonds de la plateforme, validation serveur, concurrence |
| Q5 | Lien de suivi : secret distinct du jeton, haché, expiré 12 h après la clôture ou le refus ; aucune donnée interne ; aucun paiement électronique |
| Q6 | Points de TABLE (table active du site, commande rattachée) et de COMPTOIR |
| Q7 | Aucun compte client ; nom et téléphone demandés selon le réglage du site (non demandé / facultatif / obligatoire), minimisation |
| Q8 | Commande « À confirmer » par défaut (sauf acceptation automatique), refus motivé ; menu public : désignation, description, prix, disponibilité |
| Q9 | Un seul moteur de commandes, chemin de création publique distinct ; même circuit après confirmation ; rien en cuisine avant confirmation |
| Q10 | Jeton dans le fragment `#`, effacé par `history.replaceState` ; CSP, `Referrer-Policy: no-referrer`, aucune ressource tierce |
| Q11 | Proxies de confiance explicites ; empreintes HMAC (clé serveur) des adresses IP |
| Q12 | Idempotence limitée au point et à la même requête ; une clé connue ne permet pas de récupérer le suivi |
| Q13 | Téléphone effacé 30 jours après la fin de la commande par la tâche quotidienne (planifiée et supervisée) |
| Q14 | Permissions `restaurant.qr.point.view`, `restaurant.qr.point.manage`, `restaurant.qr.settings.manage`, `restaurant.orders.order.confirm` ; prise en charge interdite avant confirmation ; limites `SM_QR_*` ; suivi maintenu après régénération du point |

## R7-A — points QR (migration 0056)

- Module **`restaurant_qr`** (paquet backend et frontend), dépend de `restaurant.orders` ; tables
  facultatives. Planifié pendant son développement (routes non montées en production,
  permissions non accordées) ; livré par la migration 0058.
- `restaurant_qr_points` : point d'UN site, `kind` = `TABLE` (table existante du MÊME site, FK
  composite `RESTRICT`, un seul point ACTIF par table) ou `COUNTER` (libellé unique par site,
  insensible à la casse) ; `token_hash` (SHA-256 hexadécimal, unique sur toute la plateforme,
  format contrôlé par CHECK) ; `token_generated_at` / `_by` ; jamais supprimé.
- **Jeton** : `secrets.token_urlsafe(32)` (256 bits, 43 caractères) ; jamais stocké, jamais
  journalisé, jamais dans l'audit ; un hachage lent est inutile pour une valeur aléatoire de
  256 bits. L'adresse et l'image ne figurent que dans la réponse d'émission (`201` de création,
  `200` de régénération, `Cache-Control: no-store`) ; aucune route ne les relit.
- **Régénération** : `POST /restaurant/qr/points/{id}/regenerate` exige
  `{"confirm_invalidation": true}` ; l'empreinte est remplacée dans la transaction (révocation
  immédiate). Désactivation : point inutilisable, jeton inchangé ; réactivation d'un point de
  table : table encore active et sans autre point actif.
- **Adresse publique** (`app/core/public_urls.py`, seul constructeur) : `{base}/q#<jeton>` et
  `{base}/q/track#<secret>` ; base = `SM_PUBLIC_APP_URL`, contrôlée au démarrage
  (`public_url_errors` : http(s), https obligatoire hors développement, domaine dans
  `SM_PUBLIC_ALLOWED_HOSTS`, ni chemin, ni paramètre, ni fragment, ni identifiants) ; en
  développement seulement, `http://localhost:5173` par défaut ; ailleurs, sans configuration :
  `503 public_url_not_configured`, **rien n'est écrit**.
- **Image** : Segno, SVG `viewBox` (mise à l'échelle par l'impression), correction d'erreur Q,
  marge de 4 modules, noir sur blanc.
- **RLS** : `ENABLE` + `FORCE`, `tenant_isolation`, et **`qr_token_lookup`** (`SELECT` seulement,
  rôle applicatif) : `token_hash = NULLIF(current_setting('app.qr_token_hash', true), '')`.
  `app.qr_token_hash` est fixée par le serveur pour chaque transaction (`set_db_context`, vide
  par défaut) : seule la ligne du jeton présenté devient visible ; aucune écriture possible par
  cette politique ; aucun `BYPASSRLS`, aucune fonction `SECURITY DEFINER`.
- **Droits** : `SELECT, INSERT`, `UPDATE (label, token_hash, token_generated_at,
  token_generated_by, is_active, updated_at)` ; aucune suppression ; aucun droit pour la console
  ni la purge. Descente refusée dès qu'un point existe.
- **Permissions** : `restaurant.qr.point.view` (read), `restaurant.qr.point.manage` (admin),
  `restaurant.qr.settings.manage` (admin, R7-C). Administrateur (`*`), Gestionnaire
  (`restaurant.*`) ; Consultant : `point.view` (`*.view`).
- **Audit** : `restaurant_qr_point.created|updated|activated|deactivated|regenerated` (type,
  libellé, table ; jamais le jeton, son empreinte ni l'adresse).
- **Configuration ajoutée** (`SM_*`) : `PUBLIC_APP_URL`, `PUBLIC_ALLOWED_HOSTS`,
  `TRUSTED_PROXIES`, `RATE_LIMIT_HMAC_KEY` (obligatoire en production, ≥ 32 caractères),
  `QR_ORDER_IP_LIMIT` (10), `QR_ORDER_POINT_LIMIT` (30), `QR_ORDER_WINDOW_MINUTES` (10),
  `QR_READ_IP_LIMIT` (120), `QR_READ_WINDOW_MINUTES` (1), `QR_TRACKING_HOURS_AFTER_END` (12),
  `QR_CONTACT_RETENTION_DAYS` (30).
- **Tests** : `tests/test_restaurant_qr_points.py` (jetons, image, règles d'adresse par
  environnement, émission unique, empreinte seule, comptoir / table, table d'un autre site,
  table inactive, un point actif par table, tables désactivées, régénération confirmée,
  en-têtes `Host` / `X-Forwarded-Host` ignorés, adresse absente → rien d'écrit, permissions et
  portée des sites, isolation API et SQL avec la politique de lecture par jeton, droits SQL,
  registre de production, descente de 0056 refusée).

## R7-B — accès public sécurisé et menu public (aucune migration)

- **Routeur public des modules** : champ `public_router` du manifeste, monté sous
  `/api/v1/public<préfixe du module>` pour un module DISPONIBLE seulement, sans
  `require_module` (aucun membre) ; chaque route contrôle son périmètre.
- **Garde unique** `restaurant_qr/access.py` (`resolve_access`), à chaque appel : format du
  jeton (refus sans requête) → point trouvé par l'empreinte, la politique `qr_token_lookup`
  ne laissant voir que lui (aucune entreprise dans le contexte) → contexte RLS de l'entreprise
  DU POINT, empreinte effacée → entreprise active, site actif, point actif → abonnement du SITE
  autorisant l'écriture (même calcul que pour un membre : licence en vigueur sinon plan,
  statut, politique d'accès ; `pending_activation`, expiré, suspendu : refus) → modules
  effectifs et disponibles du site (QR Code, commandes, menu) → pour une table : module des
  tables effectif, table ACTIVE du même site.
- **Refus identique** pour tout échec : `404 qr_unavailable`, message générique ; motif
  journalisé côté serveur avec l'identifiant du point (jamais le jeton ni son empreinte).
- **Menu public** `GET /api/v1/public/restaurant/qr/menu` (en-tête `X-QR-Token`) :
  établissement (nom commercial), site, point (table ou comptoir), devise, sections actives et
  éléments : nom, conditionnement, description, prix, disponibilité. Seul « épuisé » est montré
  (non commandable) ; tout autre motif interne (article désactivé, hors assortiment,
  conditionnement sans prix, section désactivée) retire l'élément. Jamais de stock, coût,
  référence, identifiant d'article ni motif d'indisponibilité saisi par le personnel. Lecture
  du menu factorisée dans le module Menu (`item_query`, `item_rows`, `site_menu`) : même règle
  « commandable » que pour le personnel.
- **Portée sans membre** : `TenantScope` (protocole : l'entreprise) et `PublicScope`
  (entreprise et site déduits du jeton) dans `app/platform/context.py` ; les ports des tables
  et la lecture du menu acceptent cette portée minimale.
- **En-têtes** (intergiciel, toutes les réponses `/api/v1/public/…`, erreurs comprises) :
  `Cache-Control: no-store` (sauf réponses publiques déjà mises en cache volontairement : pays,
  profils), `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`,
  `X-Robots-Tag: noindex, nofollow`.
- **Adresse du client** (`app/core/client_ip.py`) : adresse de la connexion ; `X-Forwarded-For`
  lu seulement si la connexion vient d'un proxy de confiance (`SM_TRUSTED_PROXIES`, adresses ou
  réseaux), parcouru de droite à gauche en sautant les proxies de confiance ; en-tête altéré :
  arrêt au dernier proxy de confiance. Aussi utilisée par l'inscription publique.
- **Limitation de fréquence** : clés en empreinte **HMAC-SHA256** avec `SM_RATE_LIMIT_HMAC_KEY`
  (inscription comprise) ; lecture publique comptée par adresse IP AVANT la vérification du
  jeton (`SM_QR_READ_IP_LIMIT` par `SM_QR_READ_WINDOW_MINUTES`) : un jeton inconnu est compté
  aussi (pas de recherche par force brute illimitée).
- **Tests** : `tests/test_restaurant_qr_public.py` (menu limité aux champs publics, aucune
  donnée interne, autre site absent ; épuisé visible, motifs internes masqués, section et
  assortiment ; refus identiques : jeton absent, malformé, inconnu, régénéré, point désactivé,
  table désactivée, tables ou QR désactivés ; site inactif, entreprise suspendue, abonnement
  suspendu ou en attente d'activation, site B indépendant ; un jeton n'ouvre que son site et son
  entreprise ; routes publiques non montées tant que le module est planifié ; limitation de
  fréquence et empreinte HMAC ; proxies de confiance).

## R7-C — commandes sans compte, anti-abus, réglages QR (migration 0057)

- **Moteur unique** : `OrderService` reçoit une portée et un acteur abstraits (`tenant`,
  `scope`, `actor_kind`, `actor_user_id`, `meta`) ; la création est factorisée dans
  `_insert` (verrous, effectivité recalculée, réglages, table, prix FIGÉS du menu, routage,
  numéro du jour, évènement, audit), appelée par le personnel (inchangé) et par
  **`PublicOrderService`** (`restaurant_orders/public.py`) : acteur `PUBLIC`, aucun
  utilisateur (`created_by` nul, audit sans utilisateur), modules du site calculés sans membre
  (`CapabilityService.public_site_modules` : abonnement du site autorisant l'écriture), état
  « à confirmer » ou « ouverte » si `qr_auto_accept` (lu sous le verrou partagé des réglages,
  recopié dans l'évènement de création). Le module QR passe par `restaurant_orders.api`
  (`create_public_order`, `awaiting_staff_count`, `order_ref`) : aucun accès à ses modèles.
- **Requête publique** `POST /api/v1/public/restaurant/qr/orders` (en-tête `X-QR-Token`) :
  `client_key` (UUID **v4** obligatoire), lignes (élément du menu, quantité **entière**
  1..99, instruction), nom, téléphone ; **tout autre champ est refusé** (`extra = forbid` :
  site, table, prix, canal, état…). Ordre : compteur par adresse IP (validé) → garde → compteur
  par point (validé) → garde rejouée → verrou consultatif du POINT → réglages QR du site en
  partage → plafonds du site → nom / téléphone → idempotence → plafond des commandes en attente
  → création → lien de suivi.
- **Idempotence (Q12)** : clé unique PAR POINT (`UNIQUE (tenant_id, point_id, client_key)`),
  clé du moteur dérivée (`uuid5` du point et de la clé : jamais égale à une clé du personnel) ;
  même clé + même requête (empreinte SHA-256 du contenu) dans les 15 minutes : aucune nouvelle
  commande (`200`, `replayed`), **nouveau secret de suivi** (l'ancien lien cesse de fonctionner :
  le secret n'étant jamais conservé, il ne peut être renvoyé) ; même clé + autre contenu :
  `409 qr_order_key_reused` ; au-delà de 15 minutes : `409 qr_order_already_sent`. Une clé
  connue ne suffit pas : il faut aussi le jeton du point et le contenu exact.
- **Plafonds (Q4)** : réglages QR par site (`restaurant_qr_site_settings`, créés à l'activation
  du module par `site_setup`, conservés par une désactivation) : 30 lignes, 20 unités par ligne,
  3 commandes en attente par point au départ ; plafonds de la plateforme 100 / 99 / 20 dans le
  schéma ET en base (`CHECK`). « En attente » = à confirmer, ou ouverte sans responsable et non
  commencée (acceptation automatique). Compté sous le verrou du point : soumissions simultanées
  sans dépassement. Réglages : `GET/PUT /restaurant/qr/settings` (`point.view` /
  `settings.manage` sur CE site), verrou exclusif, audit avant / après
  (`restaurant_qr_settings.updated`).
- **Données personnelles (Q7)** : nom (`call_name`, 40) et téléphone (`contact_phone`, 30)
  selon le réglage du site (non demandé / facultatif / obligatoire ; défaut : nom facultatif,
  téléphone non demandé) ; une information NON demandée n'est jamais conservée ; téléphone
  normalisé (séparateurs retirés, aucun indicatif ajouté, 6 à 15 chiffres) ; aucun compte.
- **Refus assainis** : élément non commandable ou inconnu → `422 qr_order_item_unavailable`
  avec les seuls identifiants (aucun motif interne) ; tout autre refus du moteur → `404
  qr_unavailable` ; plafonds : `qr_order_too_many_lines`, `qr_order_quantity_too_large`,
  `qr_order_pending_limit` ; informations : `qr_order_name_required`,
  `qr_order_phone_required`, `qr_order_phone_invalid` ; limitation : `429 rate_limited`
  (`SM_QR_ORDER_IP_LIMIT`, `SM_QR_ORDER_POINT_LIMIT` par `SM_QR_ORDER_WINDOW_MINUTES`).
- **Effacement des téléphones (Q13)** : 30 jours après la fin (close, annulée, refusée) par la
  tâche quotidienne EXISTANTE `stockmanager notifications purge` (rôle `stockmanager_purge`) :
  lecture limitée aux colonnes utiles des commandes finies depuis plus de 30 jours, mise à jour
  de `contact_phone` seule et vers `NULL` seulement (`WITH CHECK`) ; le déclencheur des états
  finaux n'admet que cet effacement sur une commande finie. Durée figée dans 0057 (comme 0045).
  Code de sortie non nul en cas d'échec : à superviser (minuterie systemd `OnFailure=` ou alerte
  du cron) — planification et supervision à mettre en place par l'exploitation (voir le guide
  de mise à jour).
- **Migration 0057** : `restaurant_qr_site_settings`, `restaurant_qr_orders` (FK composites,
  `request_hash` et `tracking_hash` au format SHA-256, `tracking_hash` unique, politique
  `qr_tracking_lookup`), `restaurant_orders.contact_phone`, droits par colonne, politiques du
  rôle de purge, déclencheur des états finaux remplacé (version de 0042 restaurée à la
  descente) ; descente refusée dès qu'un réglage, une commande QR ou un téléphone existe.
- **Tests** : `tests/test_restaurant_qr_orders.py` (16 : moteur unique et acteur public, table
  et acceptation automatique, champs de décision refusés, quantités et clés invalides, refus
  sans motif interne, plafonds du site et de la plateforme, permissions des réglages,
  minimisation nom / téléphone, idempotence et renouvellement du secret, clé limitée au point,
  délai de rejeu, 5 soumissions identiques simultanées → 1 commande, plafond en attente sous
  concurrence (6 soumissions, plafond 2 → 2 créées), limitation par IP et par point, refus
  identiques au menu, effacement à 30 jours seulement et idempotent, droits du rôle de purge,
  immutabilité des commandes finies, droits SQL, descente refusée puis restauration du
  déclencheur).

## R7-D — confirmation ou refus par le personnel (aucune migration)

- **Permission** `restaurant.orders.order.confirm` (nature `write`, module des commandes, P-12) :
  Administrateur (`*`), Gestionnaire (`restaurant.*`), Vendeur et Serveur (modèles de rôles ;
  rôles système résolus depuis leur modèle : les entreprises existantes la reçoivent aussi) ;
  jamais le Préparateur ni le Consultant.
- `POST /restaurant/orders/{id}/confirm` : « à confirmer » → ouverte (`confirmed_at` /
  `confirmed_by`, évènement `CONFIRMED`, audit `restaurant_order.confirmed`), sous le verrou de
  la commande, effectivité revérifiée ; deux confirmations simultanées : une seule réussit
  (`409 order_not_pending`).
- `POST /restaurant/orders/{id}/reject` : motif OBLIGATOIRE (500 caractères) ; définitif
  (`REJECTED`), lignes annulées avec le même motif, évènement `REJECTED` (motif), audit
  `restaurant_order.rejected` ; motif affiché au client sur son suivi.
- **Rien avant la confirmation** : toute action d'une commande ouverte (prise en charge,
  préparation, ajout de lignes, règlement, table, client…) refuse une commande à confirmer
  (`409 order_pending_confirmation`) ; une commande refusée : `409 order_rejected`. L'écran
  cuisine ne lit que les commandes ouvertes : rien n'y arrive avant la confirmation.
- **Listes** : filtres `state=open` (ouvertes seulement, colonnes de suivi et « À régler ») et
  `state=pending` (« À confirmer ») ; `state=active` inchangé (à confirmer + ouvertes).
- **Notification** des membres `restaurant.order.qr_received` à la création d'une commande « à
  confirmer » (aucune si acceptée automatiquement) : membres actifs du site détenant
  EFFECTIVEMENT `order.confirm` et `order.view` sur ce site (même calcul que pour leurs
  requêtes) ; aucun auteur (le client n'est pas un membre) ; « traitée » dès que la commande
  n'est plus à confirmer (calcul à la lecture). Les aides de notification acceptent la portée
  minimale (`TenantScope`) pour l'accès public.
- **Acceptation automatique** : `qr_auto_accept` modifiable dans les réglages des commandes
  (`restaurant.orders.settings.manage`), auditée avant / après ; omise : inchangée.
- **Tests** : `tests/test_restaurant_qr_confirm.py` (7 : permission déclarée, rien avant la
  confirmation, circuit habituel après, refus motivé et définitif, permissions et portée des
  sites, notifications et résolution, acceptation automatique sans avis, 4 confirmations
  simultanées → 1) ; listes exactes des modèles Vendeur et Serveur mises à jour.

## R7-E — suivi client temporaire (aucune migration)

- `GET /api/v1/public/restaurant/qr/track` (en-tête `X-QR-Tracking`) ; lien
  `{base}/q/track#<secret>` construit par `app/core/public_urls.py`.
- **Secret** distinct du jeton du point, 256 bits, empreinte seule
  (`restaurant_qr_orders.tracking_hash`, unique), vérifié à chaque appel par la politique
  `qr_tracking_lookup` ; renouvelé par une soumission rejouée (l'ancien lien cesse de
  fonctionner) ; jamais journalisé.
- **Garde** (`resolve_tracking`) : format, empreinte, entreprise active, site actif, abonnement
  du site autorisant l'écriture, module QR effectif. Le suivi **survit** à la régénération et à
  la désactivation du point (décision Q14). Lecture comptée par adresse IP (même limite que le
  menu). Tout échec : `404 qr_tracking_unavailable` (réponse identique).
- **Expiration** : tant que la commande est en cours, puis `SM_QR_TRACKING_HOURS_AFTER_END`
  (12) heures après sa fin (clôture, annulation, refus) ; `available_until` renvoyé.
- **Contenu** : établissement, site, point (table ou comptoir), devise, numéro du jour, état,
  état de préparation, lignes (nom, conditionnement, quantité, état), total des lignes non
  annulées, **reste à régler** (lu sur la vente si la commande est réglée, sinon le total ; 0
  pour une commande annulée ou refusée), motif d'un **refus** seulement (jamais le motif d'une
  annulation par le personnel). Jamais de membre, de coût, de stock, de prix d'achat, de
  référence ni d'autre client. **Aucun paiement électronique.**
- **Tests** : `tests/test_restaurant_qr_tracking.py` (9 : champs publics seulement, secrets
  inconnus / malformés / de mauvaise nature refusés, renouvellement par rejeu, survie à la
  régénération et à la désactivation du point, motif de refus affiché et motif d'annulation
  masqué, expiration 11 h / 13 h et commande en cours jamais expirée, reste à régler lu sur la
  vente, module et abonnement, politique de lecture par empreinte).

## R7-F — interfaces, activation, offres, livraison (migration 0058)

- **Livraison** : manifeste `restaurant.qr` disponible ; migration 0058 : activations héritées
  de la période « Bientôt disponible » remises à `false` sur les sites existants (activation
  explicite par `organization.module.manage` sur le site) ; descente sans effet.
- **Offres (Q1)** : `restaurant.qr` ajouté à STANDARD dans `plans.toml` (`stockmanager catalog
  sync`), ENTREPRISE inchangée (l'incluait déjà) ; aucune autre règle commerciale modifiée
  (modules, limites et fonctionnalités de STANDARD vérifiés par test). Licences : modules figés
  à l'émission ; un site STANDARD licencié avant R7 obtient le module à sa licence suivante, à sa
  date de début (renouvellement contigu) — vérifié par test sur les deux dates.
- **Interface des entreprises** : page « QR Codes » (`/restaurant/qr`, `point.view`) : points
  (création table / comptoir, renommage, activation, désactivation confirmée, régénération avec
  le texte de confirmation exigé) et réglages QR du site ; fenêtre d'émission : avertissement
  « ne pourra plus être affiché », image par `<img>` (SVG du serveur, aucun script), carte de
  table A6 ou affiche A4 (impression navigateur, `@page` injectée), téléchargement SVG, copie du
  lien (jamais affiché en clair). Commandes : onglet « À confirmer » (15 s), boutons Confirmer /
  Refuser (motif obligatoire) sur la file et la fiche, colonnes de suivi et « À régler » limitées
  aux commandes ouvertes, téléphone du client sur la fiche, acceptation automatique dans les
  réglages des commandes (si le QR est effectif).
- **Pages publiques** (`/q`, `/q/track`) : application distincte chargée à la demande (comme la
  console) — aucune session, aucun appel d'authentification ; secret lu dans le fragment, gardé
  pour l'onglet (`sessionStorage`) et effacé de l'adresse avant tout affichage ; CSP par balise
  (aucune ressource tierce) et `referrer = no-referrer` (aussi posé globalement dans
  `index.html`) ; requêtes sans cookie ni jeton de session (`credentials: 'omit'`) ; mobile
  d'abord ; menu actualisé toutes les 30 s, suivi toutes les 15 s ; même clé d'idempotence tant
  que le contenu n'a pas changé (renvoi après une coupure), nouvelle sinon ; total indicatif
  (centimes), le serveur fait foi ; aucun paiement.
- **Nouveau QR scanné dans le même onglet** : seul le fragment change (aucun rechargement) ;
  la page écoute `hashchange`, lit le nouveau secret, l'efface de l'adresse et repart d'un
  panier vide (page indexée par le secret) — défaut trouvé par l'E2E, couvert par Vitest et E2E.
- **Minimisation de l'audit public** : les entrées d'audit écrites par l'acteur `PUBLIC`
  (commande QR reçue) ne conservent ni l'adresse IP ni l'agent utilisateur du client
  (`public_meta`) ; l'adresse ne sert qu'aux compteurs de fréquence, sous empreinte HMAC.
