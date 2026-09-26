# ADR-0033 — Abonnement par site : 1 site = 1 abonnement

- **Statut** : Acceptée (Phase 3.3-B1, arbitrages TechNova Q1–Q6 du 2026-09-26)
- **Date** : 2026-09-26

## Contexte

Jusqu'en 3.3-A, une entreprise (tenant) avait **un** abonnement (`UniqueConstraint(tenant_id)`),
et les capacités (plan, statut, modules, fonctionnalités, limites `max_sites` / `max_users`)
valaient pour toute l'entreprise. Le modèle commercial validé par TechNova est différent :

```text
TENANT ── SITE A ── SUBSCRIPTION A ── LICENCE A (3.3-B2)
       └─ SITE B ── SUBSCRIPTION B ── LICENCE B
```

Chaque site paie son propre abonnement ; deux sites = deux abonnements, indépendants (plan,
période, statut, limites), même dans la même entreprise. Les données de l'entreprise
(catalogue, clients, fournisseurs, utilisateurs, rôles) restent communes à ses sites.

## Décision

1. **Modèle** : `subscriptions.site_id`, clé étrangère composite `(tenant_id, site_id) → sites`
   (jamais le site d'un autre tenant), unicité `(tenant_id, site_id)`. `site_id` n'est nul que
   pour l'abonnement pris à l'**inscription publique**, avant la création du premier site (au
   plus un par entreprise : index unique partiel) ; il est rattaché au premier site dans la
   transaction qui crée ce site (`subscription.site_attached`). `requested_activations` : nombre
   de postes demandé à la souscription (défaut 1, ≥ 1), confirmé ou ajusté par TechNova à la
   génération de la licence, qui le fige (3.3-B).
2. **Nouveau site (Q2)** : l'administrateur choisit une offre **publiée** et souscriptible en
   ligne (règle de l'inscription publique) et sa période ; l'abonnement démarre
   `pending_activation` (aucun essai automatique) ; le site n'est opérationnel qu'après
   paiement confirmé et licence. `site_plan_required`, `plan_not_available`,
   `site_subscription_preselected` (premier site d'une inscription) ; audit
   `subscription.created`.
3. **Capacités par site** (`CapabilityService.resolve(site_id)`) : sur un site, plan, statut
   effectif, politique d'accès, modules et fonctionnalités sont ceux de l'abonnement de CE
   site. Sans site (données de l'entreprise, vue consolidée), l'**union** de ce qu'accorde
   chaque abonnement (une permission n'est retenue que si UN abonnement l'accorde, avec son
   propre statut) ; plan et statut affichés : l'abonnement le plus permissif. Aucun nouveau
   moteur : même statut effectif (échéance + `grace_days`), mêmes politiques.
4. **Écritures par site (Q6)** : toute opération qui écrit sur un site est revérifiée pour
   l'abonnement de ce site, même lancée depuis la vue consolidée : `operation_site` et
   `ensure_document_site` appellent `RequestContext.ensure_site_allows(site_id)`, qui rejoue les
   exigences de permission de la route (enregistrées par `require_permission`) sur les
   capacités du site ciblé (`403 subscription_restricted` ou `permission_denied`, avec
   `site_id`). Les **lectures** ne sont pas revérifiées (permissions de nature `read` /
   `export`) : la consultation porte sur les sites accessibles au membre. Un transfert exige la
   permission (et la fonctionnalité `stock.transfers`) sur **ses deux sites**.
5. **Limites (Q1)** : chaque site a les limites de son propre abonnement. `max_users` : un
   utilisateur compte sur chaque site auquel il a accès (propriétaire / « tous les sites » :
   tous ; sites attribués : ceux-là ; aucun site attribué : utilisateur de l'entreprise, compté
   sur tous). Contrôle à l'ajout d'un membre, à l'ajout d'un accès et à la réactivation, sur les
   seuls sites **nouvellement** accessibles (le membre compté) ; un dépassement existant est
   toléré, jamais aggravé. `max_sites` n'est plus appliquée (chaque site a son abonnement) : elle
   reste une donnée de présentation de l'offre (un abonnement couvre un site).
6. **Console** : liste des entreprises avec le résumé de leurs abonnements (nombre, plans,
   statuts effectifs, prochaine échéance ; filtres `plan_code` et `subscription_status` = « au
   moins un abonnement ») ; fiche : un bloc par abonnement de site (limites et usage du site,
   postes demandés, actions). L'activation manuelle transitoire, la prolongation et le
   changement de plan (3.2-G) visent **un abonnement** :
   `/tenants/{id}/subscriptions/{subscription_id}/activate|extend|change-plan`. Droits SQL
   ajoutés : nom et code des sites, accès des appartenances (compteurs par site).
7. **Données existantes** (migration 0020) : l'abonnement actuel de chaque entreprise est
   rattaché à son site le plus ancien ; chaque autre site reçoit une **copie** (plan, période,
   statut, prix figé) ; aucune entreprise ne perd son accès ; les paiements existants restent sur
   l'abonnement d'origine. CLI `change-plan --site-id` (facultatif si un seul abonnement).

## Conséquences

- Le paiement (3.3-A) et la licence (3.3-B2) d'un site portent sur l'abonnement de ce site.
- Une entreprise dont un site est expiré continue d'opérer sur ses autres sites ; les données
  de l'entreprise restent modifiables tant qu'au moins un abonnement l'autorise.
- Retour arrière de 0020 : un seul abonnement est conservé par entreprise (perte assumée des
  copies, paiements rattachés à l'abonnement conservé).

## Alternatives écartées

- **Abonnement unique couvrant les sites (limite `max_sites`)** : contraire au modèle
  commercial validé.
- **Limite d'utilisateurs = maximum des limites des sites** : écartée par TechNova (Q1) ; les
  limites appartiennent à l'abonnement de chaque site.
- **Revérifier aussi les lectures par site** : la vue consolidée perdrait l'historique des
  sites expirés (consultation toujours permise par les politiques d'abonnement).
