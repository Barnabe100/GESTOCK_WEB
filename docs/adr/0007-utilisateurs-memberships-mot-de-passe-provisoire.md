# ADR-0007 — Utilisateurs multi-tenants, TenantMembership et mot de passe provisoire

- **Statut** : Acceptée (décisions TechNova du 2026-09-23)
- **Date** : 2026-09-23

## Contexte

Un même utilisateur doit pouvoir appartenir à plusieurs entreprises (ex. gérant de deux
sociétés, comptable). Il n'y a pas de service d'email en Phase 1. Les utilisateurs doivent
pouvoir être rattachés à certains sites seulement.

## Décision

**Modèle**

- `users` : identité **globale** (email unique en minuscules, hash Argon2id, statut,
  `must_change_password`, compteur d'échecs et verrouillage).
- `tenant_memberships` : appartenance utilisateur ↔ tenant (`status`, `is_owner`,
  `all_sites`), unique par couple (tenant, utilisateur).
- `membership_sites` : sites accessibles quand `all_sites` est faux.
- `membership_roles` : rôle attribué **à tout le tenant** (`site_id` nul) **ou à un site**.
- Clés étrangères composites `(tenant_id, …)` : une appartenance ne peut référencer ni un
  rôle ni un site d'un autre tenant, même en cas d'erreur applicative.

**Ajout d'un utilisateur par un administrateur du tenant**

- Saisie : nom, email, rôle(s), site(s) ou tous les sites, **mot de passe provisoire**.
- Seul le **hash** est stocké ; le mot de passe n'est jamais renvoyé ni journalisé.
- `must_change_password = true` : tant qu'il n'est pas changé, seuls `/me`,
  `/me/password` et la déconnexion sont accessibles (`403 password_change_required`
  ailleurs) ; l'interface impose l'écran de changement.
- **Email déjà connu** de la plateforme : aucun second compte ; seule une appartenance
  est créée. Le mot de passe et l'identité du compte existant ne sont **jamais** modifiés
  par un autre tenant (le mot de passe saisi est ignoré).
- Les limites du plan (`max_users`) sont contrôlées côté serveur.
- **Anti-escalade** : un membre non propriétaire ne peut attribuer que des rôles dont il
  détient toutes les permissions ; il ne peut modifier ni le propriétaire ni ses propres
  accès.

**Réinitialisation de mot de passe par un administrateur** : non fournie. Un compte peut
appartenir à plusieurs tenants ; laisser un tenant réinitialiser un mot de passe global
serait une faille inter-tenant. La réinitialisation passera par l'email (phase ultérieure).

## Évolution prévue (non développée)

Invitations par email : table `invitations` (tenant, email, rôles/sites prévus, **hash**
d'un jeton aléatoire, expiration, date d'utilisation), usage unique, écran d'acceptation
où l'utilisateur définit son mot de passe. Le modèle actuel n'a pas à changer : l'acceptation
crée l'utilisateur (si besoin) puis l'appartenance, exactement comme aujourd'hui.

## Conséquences

- La liste des tenants d'un utilisateur est lisible **uniquement hors tenant actif** (écran
  de choix) grâce à deux politiques RLS restreintes à `app.tenant_id` vide.
- Le propriétaire est un attribut de l'appartenance, pas un rôle : il reçoit toutes les
  permissions des modules actifs, présents et futurs.
