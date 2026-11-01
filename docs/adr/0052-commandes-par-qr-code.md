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
