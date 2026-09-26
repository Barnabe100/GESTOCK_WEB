# ADR-0031 — Console d'administration TechNova : identité, isolation, rôle SQL, audit

- **Statut** : Acceptée (arbitrages TechNova D1 à D6, Phase 3.2-F ; complétée en 3.2-G)
- **Date** : 2026-09-25

## Contexte

Jusqu'à la Phase 3.2-E, TechNova administrait la plateforme par la CLI (`catalog sync`,
`create-tenant`, `change-plan`, `change-profile`), les fichiers TOML versionnés et du SQL sous
le rôle propriétaire. Les **paramètres commerciaux** des plans (migration 0014 : publication,
prix, périodes, devise, essai…) n'avaient aucun outil. Il n'existait aucune notion
d'administrateur TechNova, et le rôle SQL applicatif des tenants ne peut, à juste titre, ni
écrire le catalogue ni lire plusieurs tenants (RLS, pas de `BYPASSRLS`).

## Décision

1. **Source de vérité (D1)** : le **catalogue technique** (modules, permissions,
   fonctionnalités, limites, rôles de base, profils d'activité, profils UX, politiques, pays,
   structure des plans) reste **versionné** (code et `data/*.toml`, `catalog sync`) ; la
   console l'affiche en lecture seule. Les **paramètres commerciaux** (colonnes `listed`,
   `price_display_enabled`, `monthly_price[_enabled]`, `annual_price[_enabled]`, `currency`,
   `contact_required`, `commercial_description`, `display_order`, `trial_days`) sont en base,
   modifiés par la console, jamais écrasés par `catalog sync`. Les limites restent en TOML
   (réévaluation ultérieure).
2. **Identité TechNova** : `users.is_platform_admin` sur l'identité globale (ADR-0029), sans
   seconde table d'utilisateurs. Attribution et retrait **par la CLI uniquement**
   (`stockmanager platform-admin create|revoke|list`, rôle propriétaire). Compte **dédié** :
   un e-mail déjà utilisé est refusé ; aucune appartenance à un tenant ; le retrait ferme le
   compte et révoque ses sessions. Aucune route (console ou API des tenants) ne modifie ce
   statut : le rôle applicatif n'a plus de droit d'écriture sur la colonne (droits INSERT /
   UPDATE par colonne) et le rôle de la console non plus.
3. **Comptes TechNova invisibles pour l'application des tenants** : RLS sur `users`
   (`ENABLE`, sans `FORCE` : le propriétaire — migrations, CLI — n'est pas concerné) ; le rôle
   applicatif ne voit et n'écrit que `NOT is_platform_admin`. Conséquences : connexion à
   l'application des tenants refusée (`invalid_credentials`), ajout comme membre refusé
   (`409 account_unavailable`), provisioning refusé, inscription → refus générique
   `signup_unavailable`.
4. **Isolation (D2)** : même dépôt, **application et processus distincts**
   (`app.console.main`, `uvicorn app.console.main:app --port 8001`, préfixe
   `/platform-api/v1`), qui ne monte aucun routeur des tenants et se connecte avec un **rôle
   SQL dédié** (`SM_PLATFORM_DATABASE_URL`, rôle `stockmanager_platform`, `NOBYPASSRLS`,
   créé par `docker/postgres/init/02-platform-role.sh`). Le rôle applicatif des tenants ne
   reçoit **aucun** privilège supplémentaire.
5. **Droits minimaux du rôle de la console** (migration 0017) : `SELECT` sur le catalogue
   (`plans`, `plan_modules`, politiques, secteurs, profils, profils UX, pays) ; `UPDATE` des
   **seules colonnes commerciales** de `plans` ; `SELECT` sur `users` restreint par RLS aux
   comptes TechNova et `UPDATE` des seules colonnes de verrouillage (`failed_login_count`,
   `locked_until`, `last_login_at`) ; `platform_sessions` (`SELECT, INSERT, UPDATE`) ;
   `platform_audit_logs` (`SELECT, INSERT`). **Aucun droit** sur `tenants`, `subscriptions`,
   `audit_logs`, ni sur aucune table métier : TechNova administre la plateforme, pas les
   données de ses clients (D3).
6. **Session de la console** : jeton opaque `<session>.<secret>` (haché en base) dans un
   cookie `HttpOnly; SameSite=Strict` limité à `/platform-api/v1` ; expiration absolue
   (`SM_PLATFORM_SESSION_TTL_MINUTES`, 8 h) et d'inactivité
   (`SM_PLATFORM_SESSION_IDLE_MINUTES`, 30 min) ; verrouillage après échecs (mêmes règles que
   l'application) ; en-tête `X-TechNova-Console` exigé sur toute requête modifiante
   (anti-CSRF ; la console n'active pas CORS). Aucun jeton de l'API des tenants n'est accepté.
7. **Journal de la plateforme (D6)** : table `platform_audit_logs`, distincte du journal des
   tenants, **append-only** (droits `SELECT, INSERT` et déclencheur qui refuse `UPDATE` et
   `DELETE`, même au propriétaire) : auteur (identifiant + libellé figé), action, cible, tenant
   concerné (nul en 3.2-F), avant, après, raison, métadonnées, IP. Événements : `auth.login.*`,
   `auth.logout`, `platform_admin.created|revoked` (CLI), `plan.published`,
   `plan.unpublished`, `plan.commercial.updated`. Double audit : une future opération sur un
   tenant (3.2-G) écrira aussi une entrée miroir dans le journal de ce tenant ; aucune entrée
   tenant n'est créée pour une modification générale d'un plan.
8. **Modification commerciale** : `PATCH /platform-api/v1/plans/{code}/commercial`, seuls les
   champs commerciaux (`extra="forbid"` : toute clé de structure → `422 validation_error`),
   **raison obligatoire** (non vide après suppression des espaces), validations serveur
   (prix ≥ 0 à 2 décimales, devise du référentiel des pays, période proposée ⇒ prix, prix ⇒
   devise, prix affiché ⇒ période proposée, plan publié ⇒ période proposée ou contact, plan
   retiré du catalogue non publiable, essai 0–365 jours, `0` = aucun essai), refus d'une
   requête sans changement (`no_changes`), verrou de la ligne, audit avant / après dans la
   même transaction. Les contraintes `CHECK` de la base restent la seconde barrière.
   `self_service` est la règle unique de l'inscription (`signup.service.self_service`).
9. **Prix figé** : une souscription conserve `price_at_subscription` /
   `currency_at_subscription` ; une modification du tarif n'est jamais rétroactive.
10. **Réseau et MFA** : la console doit être exposée sur un réseau restreint (VPN, liste
    d'adresses) — configuration d'**infrastructure**, non réalisée par ce code (Compose publie
    le port sur la boucle locale seulement). La MFA est **future**.
11. **Licences** : aucune signature dans StockManager ; la clé privée n'est jamais dans le
    dépôt, la base, la console ni le frontend (outil TechNova séparé, Phase 3.3-B).

## Complément Phase 3.2-G — tenants et abonnements

12. **Métadonnées seulement** : la console lit l'identité plateforme d'une entreprise (nom,
    nom commercial, identifiant, profil, pays, devise, langue, fuseau, dates), son abonnement,
    son plan et deux **compteurs** (sites actifs, appartenances actives). Droits ajoutés au rôle
    de la console (migration 0018), par **colonne** et par politiques RLS `TO` ce rôle :
    `tenants` (lecture de ces colonnes, mise à jour de `status` seul), `subscriptions`
    (lecture ; plan, statut, période, prix figé), `sites` (`tenant_id`, `is_active`),
    `tenant_memberships` (`tenant_id`, `status`), `audit_logs` (insertion d'entrées miroir
    seulement : politique permissive + politique **restrictive** `tenant_id` renseigné et
    `user_id` nul). Ni coordonnées de l'entreprise, ni utilisateurs, ni aucune table métier.
13. **Deux statuts distincts** : `Tenant.status` (`active` / `suspended`, suspension par
    TechNova : accès refusé à tous ses utilisateurs, `403 tenant_suspended`, rien n'est
    supprimé) et `Subscription.status` (statut stocké ; statut **effectif** calculé à la
    lecture — échéance, délai de grâce — en Python et en SQL pour les filtres et agrégats).
    Aucun statut n'a été ajouté.
14. **Actions** (raison obligatoire, confirmation explicite, verrou de ligne, état compatible
    exigé : rejouer une action produit `409`, jamais un second effet) :
    - suspension / réactivation du tenant (`tenant.suspended`, `tenant.reactivated`) ;
    - **activation manuelle transitoire** (arbitrage D5) d'un abonnement `pending_activation`
      **ou `trial`** vers `active`, période choisie (dates dans le fuseau du tenant ;
      proposition du serveur : aujourd'hui + une période de facturation ; 24 mois au plus) :
      TechNova autorise l'activation ; **aucun paiement n'est créé ni confirmé**, aucune
      licence ; remplacée par l'activation par licence (3.3-B)
      (`subscription.manually_activated`) ;
    - prolongation d'un abonnement `active` / `past_due` / `expired` (nouvelle échéance
      postérieure ; une période échue repart du jour) (`subscription.extended`) ;
    - changement de plan (`subscription.plan_changed`, même action que la CLI) : prix et
      devise figés au tarif **actuel** du nouveau plan pour la période souscrite, période
      inchangée, aucune proratisation ; les autres abonnements et l'ancien prix (dans le
      journal) ne changent pas. La CLI `change-plan` applique désormais la même règle
      (`apply_plan_change`).
15. **Double audit transactionnel** : journal de la plateforme (auteur, tenant, cible, avant,
    après, raison) **et** entrée miroir dans le journal du tenant (même action, `user_id` nul,
    `actor = "technova"`, raison, avant / après, identifiant de l'entrée plateforme ; jamais
    l'identité de l'agent), dans la transaction de l'action : l'action et ses deux entrées
    réussissent ou échouent ensemble (test).
16. Le filtre par profil d'activité n'est pas proposé : il enfreindrait la règle statique « aucun
    test d'un code de profil dans le code applicatif » (règle 4).

## Conséquences

- `GET /public/plans` et l'inscription lisent directement les valeurs saisies dans la console.
- Un plan `DEMO` n'existe pas dans le catalogue : aucune règle n'est déduite d'un prix nul
  (un prix 0 signifie « gratuit » pour la période proposée, rien de plus).
- Tenants et abonnements : administrés depuis 3.2-G (voir le complément). Paiements et
  licences : 3.3-A et 3.3-B ; l'activation manuelle est transitoire.

## Alternatives écartées

- **Routes `/tech-admin` dans l'API des tenants** avec un rôle applicatif élargi : affaiblit le
  moindre privilège du rôle utilisé par tous les tenants.
- **Catalogue entièrement éditable en base** : une donnée pourrait référencer du code absent ;
  perte de la revue de code.
- **Journal plateforme dans `audit_logs` avec `tenant_id` nul** : mélange des journaux, droits
  plus difficiles à restreindre.
- **Promotion d'un compte d'entreprise existant** : mélange des identités (un administrateur
  TechNova apparaîtrait membre d'un tenant).
