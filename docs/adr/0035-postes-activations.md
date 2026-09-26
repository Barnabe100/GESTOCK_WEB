# ADR-0035 — Postes : activations d'installations sous licence

- **Statut** : Acceptée (Phase 3.3-B3)
- **Date** : 2026-09-26

## Contexte

La licence d'un site (ADR-0034) fige un nombre de **postes** (`max_activations`). Il faut
compter et contrôler les installations qui l'utilisent, site par site, sans jamais toucher à la
licence ni à sa période. Règles TechNova : activations = postes / installations identifiés par
un identifiant **aléatoire** (jamais adresse MAC, processeur ni IP) ; quotas indépendants par
site ; désactiver un poste libère **seulement** une place (jamais de suspension de la licence,
jamais de jours ajoutés ou retirés, `valid_from` / `valid_until` inchangés) ; réactiver réutilise
la licence et la période ; le Web n'active jamais de navigateur ; messages distincts (licence
expirée, révoquée, quota atteint — « Le nombre maximal de postes autorisés pour ce site est
atteint. » —, autre site, déjà actif, invalide).

## Décision

1. **Table `license_activations`** (migration 0022) : site, abonnement du site (FK composite
   `(tenant_id, site_id, subscription_id)`), licence en vigueur au moment de l'activation,
   `installation_id` (UUID généré par l'installation), libellé, version du client,
   `activated_at` / `activated_by`, `last_seen_at`, statut `ACTIVE` / `RELEASED`, libération
   (`released_at`, `released_by`, `release_source` `TENANT` | `TECHNOVA`, raison). Index unique
   partiel `(tenant_id, installation_id) WHERE status = 'ACTIVE'` : une installation n'est active
   que sur **un** site de l'entreprise, et une demande rejouée ne crée pas de doublon.
   Déclencheur `license_activations_final` : identité du poste immuable, poste libéré figé. RLS
   `ENABLE` + `FORCE` ; rôle applicatif : `SELECT`, `INSERT`, `UPDATE` des seules colonnes de
   présence et de libération ; rôle de la console : lecture et libération d'un poste `ACTIVE`.
2. **Activation** (`POST /license-activations`, site sélectionné `X-Site-Id`, permission
   `subscription.activation.manage` de nature `billing`) : sous **verrou de la ligne de
   l'abonnement du site**, licence **en vigueur** exigée ; si le client présente sa licence
   (`license_id` du `.lic`), ce doit être celle-ci. Quota = `max_activations` de la licence en
   vigueur, compté sur les postes `ACTIVE` de l'abonnement (renouvellements et réémissions
   compris) : la dernière place n'est jamais prise deux fois. Même installation déjà active sur
   ce site : `200` et le même poste (présence mise à jour). Refus `409`, **journalisés**
   (`license.activation_failed`, transaction propre) : `license_missing`, `license_expired`,
   `license_revoked`, `license_not_yet_valid`, `license_invalid`, `license_wrong_site`,
   `license_superseded`, `activation_quota_reached`, `installation_active_elsewhere`.
3. **Contrôle** (`POST /license-activations/check-in`, `subscription.activation.check`, nature
   `read`, accordée aux rôles de base Gestionnaire et Vendeur) : l'installation met à jour
   `last_seen_at` et reçoit la licence en vigueur (ou aucune : le client bloque) et la durée hors
   ligne tolérée (`SM_ACTIVATION_OFFLINE_GRACE_DAYS`, défaut 7). Un poste non vu au-delà est
   signalé « non vu récemment » ; il n'est **jamais** libéré automatiquement.
4. **Libération** : par l'entreprise (`POST /license-activations/{id}/release`, raison
   obligatoire) ou par TechNova dans la console (`POST /activations/{id}/release`, double audit ;
   l'entreprise voit « libéré par TechNova », jamais l'agent). Une place se libère ; la licence,
   sa période et l'abonnement ne changent pas. Réactiver = nouvelle activation (historique
   conservé), même licence, même période.
5. **Réduction du quota** (réémission avec moins de postes) : les postes actifs restent actifs
   (dépassement toléré, jamais aggravé) ; aucune nouvelle activation tant que le nombre de postes
   actifs n'est pas repassé sous le quota.
6. **Interface** : page Abonnement — « 3 postes autorisés · 2 utilisés · 1 disponible », postes
   actifs du site, libération (raison + confirmation) ; **aucun** bouton d'activation (le Web
   n'active pas de navigateur). Console — postes de la licence (actifs et libérés), libération.
7. **Audit** : `license.activation.created`, `license.activation.released`,
   `license.activation_failed`.

## Conséquences

- Le client Desktop (hors de ce dépôt) s'authentifie comme un membre, choisit le site, s'active
  puis se contrôle régulièrement ; la preuve hors ligne locale (licence signée + dernier
  contrôle) est à construire côté client.
- `activated_by` (utilisateur de l'entreprise) n'est jamais exposé à la console.

## Alternatives écartées

- **Empreinte matérielle (MAC, processeur) ou IP** : interdite (vie privée, changements de
  matériel, NAT) ; identifiant aléatoire de l'installation.
- **Libération automatique des postes inactifs** : contraire à « désactiver = décision
  explicite » ; signalement seulement.
- **Quota global à l'entreprise** : contraire à 1 site = 1 abonnement = 1 licence.
