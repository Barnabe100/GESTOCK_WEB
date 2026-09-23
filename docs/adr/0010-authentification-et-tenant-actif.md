# ADR-0010 — Authentification, sessions et tenant actif

- **Statut** : Proposée (choix d'implémentation de la Phase 1, à valider)
- **Date** : 2026-09-23

## Contexte

Un utilisateur peut appartenir à plusieurs tenants ; le tenant actif doit être déterminé
**explicitement** dans le contexte de session/requête. Clients : web (Phase 1), mobile Flutter
(plus tard).

## Décision

- **Mots de passe** : Argon2id (réhachage automatique si les paramètres évoluent) ;
  longueur minimale configurable ; verrouillage temporaire après N échecs
  (`SM_LOGIN_MAX_FAILURES`, `SM_LOGIN_LOCKOUT_MINUTES`) ; message générique pour un compte
  inconnu, avec temps de réponse égalisé.
- **Jeton d'accès** : JWT HS256 court (15 min par défaut) portant `sub` (utilisateur),
  `sid` (session) et **`tid` (tenant actif)**. À chaque requête, le backend revérifie :
  session non révoquée, utilisateur actif, appartenance active au tenant `tid`, tenant actif.
  Un jeton lié à un tenant dont l'utilisateur n'est pas (ou plus) membre est refusé.
- **Choix du tenant** : à la connexion (`tenant_id` optionnel) ou au rafraîchissement
  (`POST /auth/refresh {tenant_id}`), toujours validé contre les appartenances. Sans choix
  et avec une seule appartenance, celle-ci est retenue ; sinon le jeton est émis **sans
  tenant** et ne donne accès qu'à `/me` (écran de choix).
- **Site actif** : en-tête `X-Site-Id`, revalidé à chaque requête (site du tenant, actif,
  accessible au membre). Les rôles limités à un site ne s'appliquent que sur ce site.
- **Jeton de rafraîchissement** : opaque (`<session>.<secret>`), seul le **hash** est
  stocké (`auth_sessions`) ; cookie `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`
  (30 jours). **Rotation** à chaque usage ; l'ancien jeton reste accepté 60 s (onglets
  concurrents, sans nouvelle rotation) ; au-delà, sa réutilisation est traitée comme un vol :
  la session est révoquée et l'évènement audité.
- **Déconnexion** : révoque la session (le jeton d'accès devient invalide immédiatement).
  Changement de mot de passe : révoque les autres sessions.
- **Frontend** : jeton d'accès **en mémoire** uniquement ; tenant et site choisis sont des
  préférences d'onglet (`sessionStorage`), sans valeur de sécurité.

## Conséquences

- Un onglet = un tenant ; plusieurs onglets peuvent travailler sur des tenants différents.
- Chaque requête authentifiée lit la session et l'appartenance (quelques accès par clé
  primaire) : coût accepté au profit d'une révocation immédiate.
- Mobile : le même mécanisme s'appliquera avec un jeton de rafraîchissement transmis dans
  le corps plutôt qu'en cookie (à ajouter à ce moment-là).
- En production : `SM_JWT_SECRET` obligatoire (≥ 32 caractères), HTTPS obligatoire.
