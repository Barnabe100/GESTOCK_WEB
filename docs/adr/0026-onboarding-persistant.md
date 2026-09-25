# ADR-0026 — Onboarding persistant : étapes déclarées par les modules, validation automatique, onboarding ≠ activation

- **Statut** : Acceptée (décisions TechNova du 2026-09-25, Phase 3.2-B)
- **Date** : 2026-09-25

## Contexte

Après l'inscription publique (ADR-0025), une entreprise existe sans site, avec un abonnement
souvent `pending_activation`. Le client doit voir à tout moment ce qui est fait, ce qui reste,
sa progression et sa prochaine action, et reprendre après déconnexion. La progression ne peut
pas vivre dans le navigateur seulement ; le client ne doit pas pouvoir se déclarer « terminé ».

## Décision

1. **Table `onboarding_steps`** (tenant-scoped, RLS `ENABLE` + `FORCE`, `UNIQUE(tenant_id,
   step_code)`) : `status`, `completed_at`, `completed_by`, `metadata` (JSONB), horodatages.
   Droits du rôle applicatif : `SELECT, INSERT, UPDATE` (jamais de suppression).
2. **Statuts V1** : `NOT_STARTED` → `IN_PROGRESS` → `COMPLETED`, dans cet ordre uniquement.
   **Pas de `SKIPPED`** : une étape recommandée non faite reste simplement `NOT_STARTED` (ou
   `IN_PROGRESS`), sans bouton « Ignorer ».
3. **`COMPLETED` est définitif** : fait historique du parcours d'installation (date, auteur,
   audit). Une modification ultérieure (site désactivé, utilisateur désactivé, information
   recommandée effacée) ne fait jamais régresser une étape. Garanti par le service **et** par
   un déclencheur en base (`onboarding_steps_forward_only`).
4. **Registre** : chaque étape est déclarée par le **manifeste du module** qui la porte
   (`ModuleManifest.onboarding`) : code, ordre, obligatoire, titre et description (clés i18n),
   action (écran de l'interface, permission), règle de validation automatique, applicabilité.
   Le registre valide les codes (uniques) et les permissions des actions. Une étape n'est
   proposée que si son module est effectif (et si elle est applicable). Étapes V1 :

   | Étape | Module | Obligatoire | Règle (constatée sur les données réelles) |
   |---|---|---|---|
   | `account` | users | oui | propriétaire actif |
   | `company` | organization | oui | nom, pays, devise renseignés (pays NULL : en cours — G4) |
   | `business_profile` | organization | oui | profil associé et actif (choisi à l'inscription) |
   | `subscription` | subscription | oui | abonnement `pending_activation`, `trial`, `active` ou `past_due` |
   | `first_site` | organization | oui | au moins un site actif |
   | `catalogue` | catalog | non | un article actif (catégories seules : en cours) |
   | `users` | users | non | un utilisateur actif en plus du propriétaire ; sans objet si le plan limite à 1 |
   | `configuration` | organization | non | informations recommandées de l'entreprise (nom commercial, logo, téléphone, e-mail, adresse, ville, région, IFU, RCCM) : toutes → terminée, une partie → en cours |

5. **Validation automatique** : l'évaluation fait seulement avancer le statut persisté. Elle a
   lieu à l'inscription (auteur : le propriétaire), après les actions qui peuvent satisfaire
   une étape (entreprise modifiée, site créé ou modifié, membre créé, profil changé — auteur :
   l'utilisateur agissant) et à chaque consultation (`GET /onboarding` ; auteur inconnu :
   `completed_by` nul, `metadata.trigger = "evaluation"`). Complétion auditée
   (`onboarding.step.completed`).
6. **Le client ne déclare jamais une étape terminée** : seule transition manuelle,
   `NOT_STARTED` → `IN_PROGRESS` (« je commence », auditée `onboarding.step.started`) ; toute
   autre valeur → `422 onboarding_transition_not_allowed`.
7. **Progression** (calculée par le serveur, jamais par le frontend) :
   - pourcentage = étapes `COMPLETED` / étapes proposées, arrondi à l'entier inférieur ;
   - onboarding **terminé** = toutes les étapes obligatoires `COMPLETED` ; les recommandées non
     faites ne bloquent pas mais ne comptent pas comme progression (terminé à 62 % possible) ;
   - étape actuelle = première obligatoire non terminée, sinon première recommandée non terminée.
8. **Tenants existants** : aucune donnée écrite par la migration ; étapes créées au premier
   accès (insertion idempotente, sans conflit) et évaluées sur les données réelles ; jamais de
   `COMPLETED` artificiel (pays NULL → `company` reste incomplète).
9. **Onboarding ≠ activation** : terminer l'onboarding ne modifie jamais l'abonnement.
   `Onboarding 100 % + abonnement pending_activation + licence non activée` est un état valide ;
   les opérations métier restent refusées (`subscription_restricted`). L'action d'une étape
   bloquée par l'abonnement est présentée comme telle (`blocked_reason`).
10. **Permissions** : `organization.onboarding.view` (`read`) et
    `organization.onboarding.manage` (`admin`) — accessibles en `pending_activation`.
11. **Champs obligatoires** (décision d'interface liée) : seuls les champs réellement exigés
    par le backend portent l'étoile `*`, et inversement ; en conséquence, la devise devient
    obligatoire dans `POST /public/signup` (l'interface la propose selon le pays ; la CLI
    TechNova garde la devise du pays par défaut).

## Conséquences

- Ajouter une étape = la déclarer dans un manifeste (et ses textes i18n) ; aucune modification
  du service. Les écrans ciblés gardent leur propre permission côté serveur.
- La page Entreprise complète (pays, informations recommandées) arrive en 3.2-C : d'ici là,
  les étapes `company` (tenant historique sans pays) et `configuration` se complètent par
  `PATCH /tenant`.
- Compatible avec la chaîne `PLAN → SUBSCRIPTION → PAYMENT → LICENCE → ACTIVATION` : aucune
  donnée de paiement ni de licence dans l'onboarding ; l'étape `subscription` constate une
  souscription enregistrée, pas une activation.

## Alternatives écartées

- **Progression calculée à la volée sans table** : pas d'historique (date, auteur), et une
  étape « régresserait » dès qu'une condition cesse d'être vraie.
- **Statut `SKIPPED`** : retiré de la V1 (décision TechNova) — une recommandation non faite
  reste visible sans statut artificiel.
- **Complétion déclarée par le client** : contraire à la validation métier côté serveur.
- **Initialisation des tenants existants par la migration** : écrirait des états sans lien avec
  les données ni l'historique.
