# ADR-0030 — Délégation RBAC calculée par le serveur

- **Statut** : Acceptée (Phase 3.2-E ; complète l'ADR-0015)
- **Date** : 2026-09-25

## Contexte

L'ADR-0015 a posé l'anti-escalade : un non-propriétaire n'accorde que ce qu'il détient, sur la
même portée (tout le tenant ou un site de son périmètre), et seulement des sites de son
périmètre. En 3.2-E, trois constats :

1. **L'interface décidait** de ce qui pouvait être accordé (`can(code)` dans l'éditeur de rôle),
   à partir des permissions du **site courant**, alors que le serveur exige de détenir les
   permissions d'un rôle **sur tout le tenant**.
2. **Faux refus** : les contrôles comparaient les permissions d'un rôle à celles du registre
   entier. Sur une offre sans fonctionnalité optionnelle (ex. STANDARD, sans
   `stock.transfers`), un Administrateur non propriétaire était refusé
   (`permission_escalation`) en attribuant Gestionnaire ou Administrateur, à cause de
   permissions que personne ne peut détenir dans ce tenant.
3. Après une réduction d'offre, l'éditeur de rôle **retirait silencieusement** les permissions
   devenues hors offre à l'enregistrement.

## Décision

1. **Une seule logique d'autorisation**, dans `_AccessBase` (`app/platform/access/service.py`) :
   - `role_grants(role)` = permissions que le rôle accorde **réellement** dans ce tenant (son
     modèle ou sa liste, limités à l'offre : modules effectifs et fonctionnalités du plan) ;
     c'est ce que le calcul des capacités accorde, donc la base de tout contrôle ;
   - `delegable_permissions(site_id)` = ce que l'acteur peut accorder : toute l'offre pour le
     propriétaire ; sinon ce qu'il détient sur la portée (vide pour un site hors de son
     périmètre) ;
   - les contrôles (`_ensure_grantable`) et les projections utilisent ces mêmes fonctions.
2. **Projections exposées** (lecture, `users.role.manage` **ou** `users.member.manage`) :
   `GET /permissions/delegable?site_id=` (permissions accordables) et
   `GET /roles/delegable?site_id=` (rôles actifs attribuables) ; `RoleOut.delegable` sur chaque
   rôle (attribuable sur tout le tenant et, s'il est personnalisé, modifiable, duplicable,
   activable, désactivable par l'utilisateur courant). L'interface affiche ces projections et ne
   calcule rien ; le serveur refuse de toute façon le reste.
3. **Permissions hors offre conservées** : à la modification d'un rôle personnalisé, une
   permission enregistrée devenue hors de l'offre est conservée telle quelle (sans effet tant que
   l'offre ne l'inclut pas) ; une permission hors offre ne peut jamais être **ajoutée**
   (`422 unknown_permission`) ; la retirer explicitement la supprime (audit
   `permissions_removed`).
4. **Inchangé** : rôles de base (système) non modifiables, rôle Administrateur protégé (jamais
   désactivé), aucun rôle supprimé (désactivation, attributions et historique conservés),
   propriétaire hors RBAC (toutes les permissions de l'offre), audit `role.created`,
   `role.updated` (`permissions_added` / `permissions_removed`), `role.activated`,
   `role.deactivated`, `member.role_assigned` / `member.role_removed`.

## Conséquences

- Les rôles de base redeviennent attribuables par un Administrateur non propriétaire sur toute
  offre.
- Si TechNova élargit ensuite l'offre (changement de plan), les rôles de base gagnent
  automatiquement les permissions correspondantes (modèles résolus à l'exécution, ADR-0013) :
  effet d'une décision TechNova, pas d'une délégation.
- Un rôle personnalisé contenant des permissions que l'utilisateur ne détient pas est affiché
  « Hors de votre périmètre » : consultable, pas modifiable.

## Alternatives écartées

- **Comparer au registre entier** : faux refus décrits plus haut.
- **Délégation calculée dans l'interface** : contraire à la règle « le backend est la seule
  frontière de sécurité » et incohérent avec la portée tenant / site.
- **Retirer les permissions hors offre à l'enregistrement** : perte silencieuse de
  configuration après une réduction d'offre.
