# ADR-0029 — Identité globale (`User`) et appartenance au tenant (`TenantMembership`)

- **Statut** : Acceptée (décision TechNova, Phase 3.2-D)
- **Date** : 2026-09-25

## Contexte

StockManager est multi-tenant : une même personne peut travailler pour plusieurs entreprises
avec un seul compte (ex. administratrice du tenant A, vendeuse dans le tenant B). La Phase 3.2-D
ouvre l'administration complète des utilisateurs d'un tenant ; il faut fixer ce qu'un
administrateur de tenant peut modifier.

## Décision

1. **Deux notions strictement séparées** — règle d'architecture centrale :

   ```text
   User (identité globale : nom, e-mail, mot de passe, compte)
   └── TenantMembership (appartenance à UN tenant : statut, rôles, sites ; is_owner)
   ```

2. **L'administrateur d'un tenant administre l'appartenance à SON entreprise, jamais
   l'identité globale.** Via `/members` (ressource = appartenance, identifiant
   `membership_id`), il peut : ajouter, modifier l'accès (rôles tenant ou site, sites, statut),
   activer, désactiver. Il ne peut pas modifier le nom, l'e-mail, le mot de passe ni le compte
   global : aucun point d'entrée ne le permet, et `PATCH /members/{id}` refuse toute clé
   d'identité (`extra="forbid"` → `422 validation_error`). **Aucune réinitialisation de mot de
   passe par un administrateur de tenant** : elle permettrait de prendre le contrôle d'un compte
   utilisé dans une autre entreprise.
3. **Ajout** : e-mail inconnu → compte global créé (mot de passe provisoire haché
   immédiatement, `must_change_password`) ; e-mail connu → compte **réutilisé tel quel** (nom et
   mot de passe fournis ignorés, jamais appliqués), seule l'appartenance est créée ; une seule
   appartenance par tenant (`409 member_exists`).
4. **Désactivation = appartenance** (`status = suspended`, affiché « Inactif ») : accès au
   tenant refusé immédiatement (jeton existant et nouvelle connexion) ; le compte global et les
   autres tenants de l'utilisateur ne changent pas ; rôles, sites, historique (ventes,
   mouvements, audit) conservés ; aucune suppression physique (ni du compte, ni de
   l'appartenance). Réactivation soumise à la limite `max_users` du plan.
5. **Garde-fous inchangés** (ADR-0015) : anti-escalade (on n'accorde que les permissions et les
   sites de son propre périmètre, y compris pour désactiver un membre dont l'accès le dépasse),
   propriétaire protégé (`owner_protected`), aucune modification de ses propres accès
   (`self_modification`). Permissions : `users.member.view` / `users.member.manage` (le
   Consultant n'y a pas accès ; le Vendeur non plus).
6. **Audit** : `member.created`, `member.updated` (avant / après), `member.activated`,
   `member.deactivated`, `member.role_assigned` / `member.role_removed`,
   `member.site_assigned` / `member.site_removed` — acteur, tenant, appartenance, date ; jamais de
   mot de passe.

## Conséquences

- La gestion de l'identité (nom, e-mail, mot de passe, récupération de compte) relèvera d'un
  futur espace « Mon profil » de l'utilisateur, hors administration du tenant.
- Le transfert de propriété d'un tenant n'est pas défini (hors 3.2-D).
- La « dernière activité » n'est pas affichée : `users.last_login_at` est globale (connexion à
  n'importe quelle entreprise) et révélerait l'activité dans un autre tenant.
- Le statut conserve sa valeur technique `suspended` (aucune migration) ; l'interface l'affiche
  « Inactif ».

## Alternatives écartées

- **Identité modifiable si l'utilisateur n'appartient qu'à ce tenant** : règle conditionnelle
  fragile (l'utilisateur peut rejoindre un autre tenant ensuite).
- **Nom seulement modifiable** : effet visible dans les autres entreprises.
- **Désactivation du compte global** : couperait l'accès aux autres entreprises.
