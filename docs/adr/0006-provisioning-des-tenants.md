# ADR-0006 — Provisioning des tenants : service dédié + CLI TechNova

- **Statut** : Acceptée (décision TechNova du 2026-09-23)
- **Date** : 2026-09-23

## Contexte

En Phase 1, il n'y a ni inscription publique ni console d'administration plateforme.
Il faut pourtant pouvoir créer une entreprise cliente complète (tenant, propriétaire,
abonnement, configuration initiale) sans introduire une seconde surface d'authentification
ni affaiblir l'isolation multi-tenant.

## Décision

- La logique de création vit dans un **service interne** :
  `app/platform/provisioning/service.py::TenantProvisioningService`.
  Il crée en une transaction : tenant, abonnement (essai ou actif), activations de modules
  (profil ∩ plan, modules optionnels désactivés), rôles système issus des modèles
  (`role_templates.toml`), premier site, propriétaire (créé ou existant) et son
  appartenance, puis une entrée d'audit `tenant.provisioned`.
- Le service **ne valide pas la transaction** : l'appelant la contrôle.
- La **CLI** `stockmanager create-tenant` n'est qu'un adaptateur : lecture des arguments,
  du mot de passe provisoire (invite masquée, `--owner-password-stdin` ou
  `SM_OWNER_PASSWORD`), appel du service, commit, affichage.
- Le service fonctionne avec le **rôle SQL applicatif standard** : il se place dans le
  contexte RLS du nouveau tenant (`app.tenant_id` = identifiant généré avant insertion).
  **Aucun privilège `BYPASSRLS`** n'est nécessaire ni accordé.
- `stockmanager catalog sync` (profils, plans, politiques) utilise, lui, la connexion
  propriétaire : le catalogue est en lecture seule pour le rôle applicatif.

## Conséquences

- Une future console TechNova, une inscription publique ou une API d'administration
  réutiliseront le même service, sans duplication.
- Pas de nouvelle surface d'attaque en Phase 1 (la CLI s'exécute sur l'infrastructure).
- La future administration plateforme (lister/suspendre des tenants) nécessitera une
  conception dédiée : identités séparées et, pour les lectures transverses, un rôle SQL
  distinct et restreint — à concevoir le moment venu, jamais par élargissement du rôle
  applicatif.

## Alternatives écartées

- Logique de création dans la CLI : non réutilisable.
- Rôle applicatif `BYPASSRLS` pour la création : affaiblit l'isolation pour tout le code.
- Console d'administration dès la Phase 1 : hors périmètre, seconde authentification.
