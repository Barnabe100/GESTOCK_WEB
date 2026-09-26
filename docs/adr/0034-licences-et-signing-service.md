# ADR-0034 — Licences des sites et Signing Service (Ed25519)

- **Statut** : Acceptée (Phase 3.3-B2 ; arbitrages TechNova Q3, Q4, Q5 du 2026-09-26)
- **Date** : 2026-09-26

## Contexte

Chaîne commerciale (ADR-0025) : PLAN → SUBSCRIPTION → PAYMENT → LICENCE → ACTIVATION.
Depuis 3.3-B1, **1 site = 1 abonnement** (ADR-0033) ; depuis 3.3-A, TechNova confirme les
paiements sans rien activer (ADR-0032). Il manque la **licence** : l'acte qui, après un paiement
confirmé, ouvre une période d'utilisation d'un site, fige ses droits et son nombre de postes, et
peut être vérifié hors de StockManager Web (Desktop, contrôle hors ligne futur).

Contraintes TechNova, sans exception : la clé privée de signature n'est **jamais** dans
GESTOCK_WEB, Git/GitHub/GitHub Actions, l'API FastAPI, la console TechNova, React, PostgreSQL,
le Docker Compose ou les images de l'application, un fichier de configuration versionné ou un
test permanent ; elle appartient **exclusivement** au Signing Service, qui ne lit aucune donnée
de tenant, n'accède à aucune base, ne décide ni du plan, ni du prix, ni du quota, ne confirme
aucun paiement et ne modifie aucun abonnement.

## Décision

1. **Signing Service** (`signing-service/`) : paquet et processus distincts (FastAPI minimal,
   `cryptography`), image Docker propre, **hors** du Compose de l'application. Routes
   `GET /health` et `POST /v1/sign`. Clé privée Ed25519 lue au démarrage depuis un **fichier
   monté** (`SIGNING_PRIVATE_KEY_FILE`), refus de démarrer si elle est absente, illisible ou
   modifiable par d'autres. Demandes authentifiées par **HMAC-SHA256** (secret partagé avec la
   console, lu depuis un fichier monté), horodatage borné (± 300 s) et nonce à usage unique
   (anti-rejeu). Signe seulement un payload **bien formé** (structure stricte, clés inconnues et
   flottants refusés). Journal : `license_id`, `key_id`, empreinte SHA-256 du payload — jamais
   la clé, le secret, le payload ni la signature. `signing-service-keygen` génère une paire hors
   de tout dépôt Git (refus sinon), en `0600`, et n'affiche que l'entrée publique.
2. **Format `.lic` v1** : `{"format": "stockmanager-license", "version": 1, "key_id",
   "payload", "signature"}` ; signature Ed25519 (base64) de la **forme canonique** du document
   sans `signature` (clés triées, séparateurs `,` `:`, UTF-8, aucun flottant). Même fonction
   canonique dans le backend et le service (vecteur de référence commun aux deux suites).
   Payload : `license_id`, `license_number`, `license_version`, `supersedes_id`, `tenant_id`,
   `site_id`, `subscription_id`, `payment_id`, `plan`, `billing_period`, `issued_at` (UTC),
   `timezone`, `valid_from`, `valid_until` (jours inclus du fuseau de l'entreprise),
   `max_activations`, `modules`, `features`, `limits`, `compatibility`. Aucun prix.
3. **Clés publiques** : trousseau versionné `backend/app/platform/licensing/data/public_keys.toml`
   (`key_id`, `Ed25519`, clé publique, `active` / `retired`) ; `SM_LICENSE_PUBLIC_KEYS_FILE` le
   remplace (développement, tests : clés éphémères). Rotation : nouvelle clé `active`, ancienne
   `retired` (vérifie encore les licences émises, refusée pour une nouvelle signature).
4. **Génération (console TechNova)** depuis un paiement **`CONFIRMED`** seulement, sous verrou
   de l'abonnement du site : période calculée par le serveur — lendemain de la couverture en
   cours (renouvellement contigu : aucun jour perdu ni offert), sinon aujourd'hui — et durée =
   période de facturation (01/10/2026 annuel → 30/09/2027) ; plan, modules, fonctionnalités et
   limites **figés** ; postes = `requested_activations` proposé, confirmé ou ajusté par TechNova
   (Q3, défaut 1 : Q4), figé. La console fait signer, **vérifie** la signature avec le trousseau
   (clé active, payload identique à celui soumis), puis enregistre la licence et aligne
   l'abonnement du site, dans **une** transaction ; un échec de signature ne laisse aucune
   trace (`503 signing_service_unavailable`, `502 signing_failed` / `license_signature_invalid`).
   Une licence au plus (non révoquée) par paiement. Aucune licence n'est importée depuis un
   navigateur ; l'entreprise ne peut ni générer une licence ni modifier ses droits, son quota ou
   sa validité.
5. **Table `licenses`** (migration 0021) : FK composites vers l'abonnement **du même site**
   `(tenant_id, site_id, subscription_id)` et le paiement **de cet abonnement**
   `(tenant_id, subscription_id, payment_id)` ; numéro global `LIC-AAAA-NNNNN` (séquence
   PostgreSQL) ; `starts_at` / `ends_at` (instants UTC des bornes locales) ; statut **stocké**
   `ISSUED` / `REVOKED`, état affiché calculé (`NOT_YET_VALID`, `ACTIVE`, `EXPIRED`, `REVOKED`).
   Déclencheur `licenses_final` : rien ne change après l'émission, sauf la révocation. RLS
   `ENABLE` + `FORCE` ; rôle applicatif : `SELECT` ; rôle de la console : lecture, `INSERT`
   d'une licence `ISSUED`, `UPDATE` des seules colonnes de révocation d'une licence `ISSUED`,
   `USAGE` de la séquence ; aucune suppression.
6. **Capacités** : la licence **en vigueur** d'un abonnement fournit ses conditions (modules,
   fonctionnalités, limites) à la place du plan (`PlanTerms`) ; un changement de plan vaut à la
   licence suivante. Statut effectif, grâce et politiques d'accès inchangés : la licence règle
   la période de l'abonnement (`active`, début = licence en vigueur, fin = fin de la couverture).
   L'activation manuelle et la prolongation transitoires (3.2-G) sont refusées dès qu'une
   licence existe (`409 subscription_license_controlled`).
7. **Révocation (Q5)** : `ISSUED` → `REVOKED`, **définitive**, jamais restaurée. Sans autre
   licence couvrant ce jour, l'abonnement du site passe `suspended` (politique « suspendu » :
   régularisation seulement) ; les données sont conservées. Une licence révoquée n'est plus
   distribuée (`409 license_revoked`).
8. **Réémission (Q5)** : nouveau cycle explicite — l'ancienne licence est révoquée (si elle ne
   l'est pas) et une **nouvelle** est émise : nouveau numéro, version + 1, `supersedes_id`, même
   paiement, **même période**, postes conservés sauf changement explicite, conditions du plan
   actuel de l'abonnement. Une licence n'est réémise qu'une fois ; une licence expirée ne l'est
   pas.
9. **Audit** : `license.generated`, `license.revoked`, `license.reissued`, double audit
   (plateforme + miroir de l'entreprise) dans la transaction, avec numéro, période, postes,
   `key_id` et empreinte du payload.

## Conséquences

- La console exige `SM_SIGNING_SERVICE_URL` et `SM_SIGNING_CLIENT_SECRET` (≥ 32 caractères)
  pour générer ; sans eux, la génération répond `503` et rien n'est modifié.
- Le numéro de licence consomme la séquence même si la signature échoue ensuite (trous
  possibles, sans conséquence).
- Révoquer la licence en vigueur alors qu'une licence future existe suspend l'abonnement
  jusqu'à une réémission (cas limite documenté ; la licence future n'est pas « avancée »).
- Le cache anti-rejeu du Signing Service est en mémoire : une instance ; plusieurs instances
  exigeraient un stockage partagé des nonces.
- Contrôle hors ligne (Desktop) : non construit ; le `.lic` et le trousseau public le
  permettront (preuve locale, `last_seen_at` et délai de grâce configurables : 3.3-B3).

## Alternatives écartées

- **Signature dans la console ou l'API** : contraire à la règle absolue sur la clé privée.
- **Clé privée en variable d'environnement** : fuite facile (journaux, `docker inspect`) ;
  fichier monté seulement.
- **JWT / JWS génériques** : dépendance supplémentaire, ambiguïtés d'algorithme ; la forme
  canonique explicite suffit.
- **Restaurer une licence révoquée** : écarté par TechNova (Q5) ; la réémission est un
  nouveau cycle explicite.
