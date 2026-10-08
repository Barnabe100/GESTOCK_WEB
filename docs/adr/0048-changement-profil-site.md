# ADR-0048 — Changement du profil d'activité d'un site

- **Statut** : Acceptée (profils / modules par site, palier D)
- **Date** : 2026-10-08
- **Prolonge** : [ADR-0024](0024-profils-activite-et-profils-ux.md) (profils d'activité),
  [ADR-0033](0033-abonnement-par-site.md) (1 site = 1 abonnement), paliers A
  (`sites.business_profile_code`, migration 0039), B (capacités par site) et C
  (`site_modules`, migration 0040)

## Contexte

Depuis les paliers A à C, le profil d'activité et l'activation des modules sont portés par
chaque site. Il manquait le moyen de changer le profil d'UN site (ex. Alimentaire → Restaurant)
sans toucher aux autres sites, au profil d'origine du tenant, ni aux données déjà enregistrées,
et en protégeant les opérations en cours.

## Décision

### Reconfiguration, jamais réinitialisation

Seuls changent `sites.business_profile_code` du site ciblé et ses lignes `site_modules`. Aucune
donnée n'est supprimée ni transformée (ventes, paiements, stock, mouvements, documents, lots,
inventaires, caisse, assortiment, audit). `tenants.business_profile_code` reste le profil
d'origine. Aucune migration.

### Niveaux calculés par le serveur

Chaque module métier déclare dans son manifeste une fonction **en lecture seule**
`site_footprint(session, site_id, lock)` (`app/platform/footprint.py`) qui compte, dans ses
seules tables, ce que le site contient :

- `history` : données commerciales enregistrées (ventes validées / annulées, paiements,
  mouvements, documents clos, stock et soldes de lots non nuls, inventaires clos, sessions de
  caisse clôturées, mouvements de caisse) ;
- `open` : documents ouverts (brouillons de vente et de stock, inventaires en cours) ;
- `active` : travail en cours qui deviendrait **impossible** si le module cessait d'être
  effectif sur le site (session de caisse ouverte) ;
- `info` : configuration (assortiment, emplacements, postes de caisse, moyens de paiement).

| Niveau | Condition | Changement |
|---|---|---|
| **BLOCKED** | travail `active` d'un module effectif avant et plus après | refusé (`409 profile_change_blocked`), aucune confirmation ne le force |
| **STRONG** | sinon, `history` ou `open` non vide | confirmation exacte `CHANGER DE PROFIL` |
| **SIMPLE** | sinon (site vide ou seulement configuré) | confirmation normale |

**L'historique seul ne bloque jamais** (10 000 ventes clôturées → STRONG). Un travail en cours
qui reste possible (session de caisse ouverte avec un profil qui conserve la caisse) est
affiché sans élever le niveau à lui seul ; ses mouvements (fond initial, encaissements) sont de
l'historique. Avec le catalogue actuel, le seul cas BLOCKED est une session de caisse ouverte
vers `distribution.entrepot` (seul profil sans caisse ni point de vente).

### Activations (`site_modules`) : le plus petit changement

- module des deux profils : choix du site conservé ;
- retiré du profil : désactivé (ligne conservée, `enabled = false`) ;
- ajouté au profil et inclus dans l'abonnement du site : `default_enabled` du nouveau profil
  (facultatif → désactivé) ;
- ajouté hors abonnement : jamais activé (aucune ligne créée) ;
- un module activé dont une dépendance est désactivée **par ce changement** est désactivé.

Aucune activation implicite, aucune ligne supprimée, aucun autre site lu. Le plan n'est jamais
contourné : un profil partiellement hors abonnement est **signalé** (`plan.compatibility =
PARTIAL`, `modules_not_in_plan`), le changement reste possible, les modules hors plan ne sont
jamais effectifs.

### Aperçu, empreinte et concurrence

`GET /sites/{id}/business-profile/preview?profile_code=…` n'écrit rien, ne verrouille rien et
n'est pas audité. Il renvoie une **empreinte** (SHA-256 des éléments décisifs : profils,
niveau, modules de l'abonnement, modules ajoutés / retirés / conservés, actions sur
`site_modules`, natures de blocage — sans les compteurs d'historique). `PUT
/sites/{id}/business-profile` recalcule tout sous verrou et refuse une empreinte différente
(`409 profile_preview_outdated`). L'empreinte n'autorise rien.

Ordre des verrous : **site** (`FOR UPDATE` : attend et suspend les écritures qui référencent le
site par clé étrangère) → **`site_modules` du site** → verrous déclarés par les modules qui
cessent d'être effectifs (caisse : réglage `cash_site_settings` `FOR UPDATE`, que l'ouverture
d'une session prend en partage). L'activation manuelle d'un module (`PUT
/sites/{id}/modules/{code}`) prend le même ordre (site d'abord) : aucun interblocage.

### Désactivation manuelle d'un module (correctif du palier C)

Désactiver un module dont le site a un travail `active` est refusé
(`409 module_has_open_operations`, détail `operations`) : une session de caisse ouverte
interdit toujours de désactiver `cash_register` sur ce site.

### Permissions, portée, audit

`organization.profile.manage` (existante, nature `admin`) détenue **sur ce site**, site du
tenant (`404`), accessible et actif (`403 site_access_denied`), abonnement du site
(`403 subscription_restricted`, `422 subscription_missing`). La portée vient de l'URL, jamais de
`X-Site-Id`. Audit `site.profile_changed` (profils, secteurs, niveau, confirmation, empreinte,
modules, `site_modules` avant / après avec la raison, historique résumé) ; un refus BLOCKED au
changement réel est journalisé `site.profile_change_refused` (`changed: false`) dans sa propre
transaction.

### Création d'un site avec un profil

`POST /sites` accepte `business_profile_code` (facultatif ; absent → profil d'origine). Un
autre profil que celui d'origine exige aussi `organization.profile.manage`. Le site est
initialisé selon le palier C ; rien n'est copié d'un autre site. C'est l'issue proposée quand
un changement est BLOCKED (« Créer un nouveau site avec ce profil »), opération séparée.

## Conséquences

- L'historique d'un module retiré reste en base. Il n'est pas lisible avec ce site sélectionné
  (`403 module_unavailable`) ; en vue « Tous les sites », il l'est si le module est effectif sur
  un autre site accessible. La résolution des capacités n'est pas modifiée (décision Q2).
- Fenêtre résiduelle : une requête d'ouverture de caisse dont les capacités ont été résolues
  juste avant la validation du changement, et qui n'a pas encore pris son verrou, peut encore
  ouvrir une session (même famille que toute révocation de droit concurrente). Une ouverture
  qui a déjà pris son verrou est attendue puis vue (BLOCKED).
- Le profil du site est exposé par les capacités (`sites[].profile`, `profile`) : base du futur
  thème visuel, non implémenté ici.
- Inchangés : `StockService`, CMUP, ventes, POS, reçus, inventaires, lots, assortiment,
  licences, console, CLI, RBAC global.

## Suite : interface par site (palier E)

Sans changement backend : la coquille, le menu, le tableau de bord (`SiteProfileHero`) et le
thème visuel suivent le profil du **site actif** (`BusinessProfileTheme`, `frontend/src/core/theme/`,
dérivé des données du catalogue, repli neutre) ; la page Modules distingue activé / activé mais
inactif (dépendance) / désactivé / hors abonnement / à venir / non proposé par le profil du site,
sans interrupteur pour une activation impossible ; la page Sites montre site → profil → modules
actifs → statut ; un changement de site vide le cache des requêtes ; une page d'un module absent
du site actif l'explique. Voir [`DESIGN_SYSTEM.md`](../architecture/DESIGN_SYSTEM.md) §8 bis.

## Synthèse du modèle (recette, palier G)

```text
Tenant (profil d'origine = profil d'inscription, défaut d'un nouveau site ; jamais effectif)
  └── Sites
        ├── Profil propre        sites.business_profile_code (changement : aperçu + PUT, ce site seul)
        ├── Modules propres      site_modules (profil du site ∩ abonnement du site ∩ activé)
        ├── Capacités propres    /me/capabilities avec X-Site-Id (modules, permissions, profil UX)
        └── Expérience propre    menu, tableau de bord, thème (BusinessProfileTheme), cache vidé

Console TechNova  →  consultation (profil de chaque site)      ≠  forçage du profil métier
CLI               →  création du tenant et du profil initial   ≠  contournement du modèle par site
```

Le plan commercial reste porté par l'abonnement de chaque site ; un module « Bientôt disponible »
n'est jamais activé (E.1) ; les données d'un site ne sont jamais servies pour un autre site ni pour
une autre entreprise. Recette de bout en bout : `tests/test_site_model_acceptance.py` (A1
Alimentation / A2 Entrepôt / B1 Maquis), E2E `site-experience` (données du site actif).

## Palier F : console TechNova et CLI (D8, lecture seule)

- **Console** : `GET /tenants` expose `site_profiles` (profils distincts des sites actifs) à côté
  du profil d'origine ; `GET /tenants/{id}` expose `sites_detail` (chaque site, actif ou non,
  et son profil). Lecture seule : aucune route d'écriture de profil, droits SQL inchangés
  (`SELECT (business_profile_code) ON sites`, migration 0039) ; aucune migration.
- **CLI** : `create-tenant --business-profile` conservé (profil d'origine et site initial) ;
  `change-profile` ne vise que le profil d'origine d'une entreprise sans site. La règle D2
  (`profile_is_per_site`) quitte le routeur de l'API pour le service
  `change_business_profile` : API et CLI la partagent ; avant ce palier, la CLI pouvait encore
  changer le profil d'origine d'une entreprise ayant des sites (profil par défaut des sites
  créés ensuite). Aucune commande ne change le profil d'un site.

## Palier E.1 : modules « Bientôt disponible »

Un module marqué Bientôt disponible peut être exposé dans le catalogue mais ne peut jamais être activé tant que son implémentation n'est pas disponible. Le contrôle est effectué côté serveur.

- Source de vérité : `ModuleManifest.status` du registre (`planned`, déclaré dans
  `app/modules/planned.py`) ; aucun champ nouveau, aucune migration.
- Point central : `ModuleService.set_enabled_for_site` (seule activation explicite ; la route de
  l'entreprise reste retirée, la console n'active aucun module). Une activation d'un module non
  `available` est refusée `422 module_not_implemented`, après les contrôles de permission, de
  site et d'offre (hors plan / non proposé : `422 module_not_offered`, inchangé). La
  désactivation reste permise.
- Inchangés : consultation (`GET /sites/{id}/modules`, `GET /modules`, statut `planned`),
  capacités, initialisation d'un site et changement de profil (défauts du profil : lignes
  `site_modules` éventuellement activées pour un module planifié, inertes car `require_module`
  refuse tout module non `available`).
- **Décision ouverte (à trancher avant la livraison effective de chaque module concerné)** :
  ces activations planifiées créées automatiquement (initialisation d'un site, changement de
  profil, copie de la migration 0040) rendraient le module effectif dès son passage à
  `available`. Comportement conservé tel quel à la validation du palier E.1 (commit `34924cd`) ;
  la livraison d'un module planifié doit décider s'il naît activé ou désactivé sur les sites
  existants.
