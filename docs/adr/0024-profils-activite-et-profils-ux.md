# ADR-0024 — Profils d'activité : secteurs, Business Profiles, profils UX

- **Statut** : Proposée (implémentée et testée en Phase 3.1 ; validation TechNova en attente)
- **Date** : 2026-09-25
- **Complète** : [ADR-0003](0003-resolution-des-capacites.md) (résolution des capacités) et
  [ADR-0009](0009-i18n-et-terminologie.md) (terminologie par profil)

## Contexte

Jusqu'en 3.0, un profil d'activité était un fichier plat (`alimentation`, `quincaillerie`,
`commerce_general`, `restaurant`) : modules proposés, ordre des modules, terminologie. Les
rubriques du menu étaient fixées dans le frontend, le tableau de bord identique pour tous. Un
restaurant voyait une application de stock renommée. La Phase 3.1 doit permettre une
expérience réellement adaptée au métier (restauration, automobile, distribution…) sans
dupliquer l'application ni introduire de conditions par secteur, et sans développer les
modules spécialisés.

## Décision

1. **Modèle** : `Core + Secteur + Business Profile + Profil UX + Modules + Plan + Permissions`.
   - *Secteur* (`retail`, `restaurant`, `automobile`, `distribution`) : classification.
   - *Business Profile* `<secteur>.<activité>` : activité précise ; référence un secteur et un
     profil UX ; modules proposés ; surcharges de présentation.
   - *Profil UX* (`retail.default`…) : navigation (rubriques ordonnées de modules), tableau
     de bord (widgets et raccourcis `<module>:<id>`), terminologie, thème (accent d'une
     palette contrôlée, densité), modules proposés par défaut. Partagé par plusieurs profils.
2. **Données, pas code** : fichiers du catalogue (`sectors.toml`, `ux_profiles/`,
   `profiles/<secteur>/`), validés contre le registre des modules puis synchronisés en base
   (`business_sectors`, `ux_profiles`, `business_profiles` étendue ; migration `0013`, qui
   bascule les tenants des anciens codes). Le mécanisme existant (`business_profiles`,
   `CapabilityService`, `/me/capabilities`, provisioning) est **étendu**, pas doublé.
3. **Registre central** `BusinessProfileRegistry` (lecture, liste, existence, configuration
   par défaut, expérience effective).
4. **Défaut ≠ effectif** : l'expérience exposée par `/me/capabilities` (`ux`) ne contient que
   les modules **effectifs et implémentés** ; les modules planifiés du profil sont listés à
   part (`ux.upcoming`) et présentés « à venir », jamais comme des écrans. Aucune route n'est
   créée pour eux.
5. **Le profil n'est jamais une permission** : il propose des modules ; le plan, les
   activations, le RBAC, la portée des sites, l'abonnement et la RLS restent seuls juges. Le
   frontend filtre encore chaque entrée par permission et fonctionnalité (ergonomie).
6. **Un seul frontend** : registres (navigation des modules, widgets, raccourcis) + données
   du profil UX. Aucune condition sur un code de profil ou de secteur (tests statiques).
7. **Profil du tenant** : changement contrôlé (`PUT /tenant/business-profile`,
   `organization.profile.manage` ; CLI `change-profile`), sans suppression de données,
   refusé si un module implémenté et activé n'est pas proposé, audité.
8. **Contexte consolidé** : un seul appel (`/me/capabilities`, TanStack Query) fournit
   tenant, site, profil, secteur, profil UX, modules, permissions, terminologie, navigation,
   tableau de bord et thème.

## Conséquences

- Ajouter un profil = un fichier + ses traductions ; aucun service métier modifié (test
  d'extensibilité `retail.librairie`). Livrer un module spécialisé l'intègre au menu et au
  tableau de bord des profils qui le déclarent, sans autre changement.
- Nouvelles permissions `organization.profile.view` (lecture) et
  `organization.profile.manage` (admin). Nouveaux modules planifiés `automobile.workshop`,
  `automobile.vehicles` (hors plans).
- `capabilities.profile` s'enrichit (`sector`, `ux_profile`) et un champ `ux` est ajouté ;
  `navigation` (ordre plat des modules) et `terminology` restent pour compatibilité.
- Les codes de profil changent (`alimentation` → `retail.alimentation`…) : CLI, E2E et
  documentation mis à jour ; la migration bascule les tenants existants.

## Alternatives écartées

- **Une application (ou un frontend) par secteur** : duplication du Core, corrections et
  sécurité à reporter N fois, incohérences de données et de reporting.
- **`if business_type == …` dispersés (backend ou frontend)** : non évolutif, non testable,
  contraire aux ADR-0003 et 0009 ; chaque nouveau secteur toucherait le Core.
- **Profil UX dans le code frontend** : les profils et plans sont déjà des données
  synchronisées ; garder la présentation au même endroit permet la validation croisée avec le
  registre des modules et une API de catalogue.
- **Menus entièrement décrits par le backend (libellés, routes, icônes)** : couple le backend
  à la présentation ; le backend ne décrit que rubriques, ordre et identifiants, le frontend
  garde ses registres typés.
- **Afficher les modules futurs comme des écrans « en construction »** : laisserait croire
  qu'ils fonctionnent ; ils sont seulement annoncés « à venir ».
