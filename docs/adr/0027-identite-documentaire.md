# ADR-0027 — Identité documentaire : le tenant, source unique ; en-tête construit par le serveur

- **Statut** : Acceptée (Phase 3.2-C)
- **Date** : 2026-09-25

## Contexte

Les futurs reçus, factures et documents afficheront l'identité de l'entreprise (nom, nom
commercial, logo, coordonnées, localité, IFU, RCCM). La Phase 3.2 a ajouté ces informations au
`Tenant` (ADR-0025) ; la page Entreprise doit les rendre toutes saisissables et montrer un
aperçu de l'en-tête. Il faut éviter les copies (une table `company_identity`, des colonnes
recopiées dans chaque document) et une logique de mise en page dupliquée entre l'interface et
le futur moteur de rendu.

## Décision

1. **Source unique** : le modèle `Tenant` porte l'identité de l'entreprise. Aucune autre table
   ni copie ; lecture et modification par `GET /tenant` et `PATCH /tenant` (permission
   `organization.tenant.update`, audit `tenant.updated`, RLS).
2. **Classement des informations** (`app/platform/tenancy/identity.py`, seule définition,
   réutilisée par l'onboarding) :
   - obligatoires (« * », refusées vides par l'API) : nom, pays, devise — la devise, fixée à la
     création, est affichée sans être modifiable ; le fuseau horaire, déjà non effaçable, porte
     aussi « * » ;
   - recommandées (jamais bloquantes) : nom commercial, logo, téléphone, e-mail, adresse,
     ville, région, IFU, RCCM ;
   - facultatives : site web, description (hors en-tête).
3. **Projection `DocumentIdentity`** construite **par le serveur** à partir du tenant et du
   référentiel des pays : nom, nom commercial, logo, lignes de coordonnées (`phone`, `email`,
   `address`, `locality` = ville, région, pays) et identifiants (`tax_id`, `trade_register`),
   chacune avec un `kind` stable (libellés — « Tél. », « IFU », « RCCM » — traduits au rendu).
   **Une information absente est omise, jamais remplacée par « N/A ».** Exposée en lecture
   par `GET /tenant/document-identity` (`organization.tenant.view`) ; la page Entreprise
   l'affiche en aperçu, le futur moteur de reçus / PDF la consommera telle quelle.
4. **Logo** : `logo_url` (https, sans identifiants, validé par le serveur). Un futur stockage
   de fichiers alimentera ce même champ (ou son équivalent dans la projection) sans changer le
   contrat de `DocumentIdentity`.
5. **Onboarding** : les étapes `company` et `configuration` s'appuient sur ce classement ; leur
   complétion reste définitive (ADR-0026). Configurer l'entreprise n'active jamais
   l'abonnement.

## Conséquences

- Aucun changement de schéma (pas de migration en 3.2-C).
- Un document généré plus tard n'aura pas à « savoir » quelles informations existent : il
  rendra la projection. Si un document doit refléter l'identité **au moment de son émission**
  (factures), il devra en garder une trace figée au moment de l'émission ; ce sera décidé
  avec le moteur documentaire.
- L'aperçu montre les informations **enregistrées** (après « Enregistrer »), pas la saisie en
  cours : aucune logique de mise en page dans React.

## Alternatives écartées

- **Table `company_identity`** : doublon du tenant, synchronisation à maintenir.
- **En-tête composé par le frontend** : logique documentaire hors du backend, à réécrire pour
  les reçus et PDF.
