# ADR-0025 — Inscription publique, abonnement en attente d'activation, chaîne paiement → licence

- **Statut** : Acceptée (décision TechNova du 2026-09-25, Phase 3.2-A)
- **Date** : 2026-09-25

## Contexte

La Phase 3.2 ouvre l'inscription en libre-service : un visiteur crée son compte et sa nouvelle
entreprise, choisit son activité (ADR-0024) et une offre publiée par TechNova. Aucun paiement
en ligne n'existe. Jusqu'ici, un abonnement était créé par la CLI TechNova, **actif** (ou en
essai). Une inscription publique sans essai ne peut pas être « active » : rien n'a été payé
ni activé.

## Décision

1. **Chaîne validée** : `PLAN → SUBSCRIPTION → PAYMENT → LICENCE → ACTIVATION`.
   - le **plan** est l'offre (structure dans `plans.toml`, paramètres commerciaux en base,
     gérés par TechNova, jamais écrasés par `catalog sync`) ;
   - l'**abonnement** est la souscription commerciale d'un tenant (plan, période, prix et
     devise figés à la souscription, statut) ;
   - le **paiement** (futur) est une opération financière : `PENDING`, `CONFIRMED`,
     `REJECTED` ; une déclaration de paiement du client n'est jamais un paiement confirmé ;
     seul TechNova confirme ou rejette ;
   - la **licence** (future, `.lic`) est générée par TechNova après confirmation du paiement,
     signée avec la clé privée TechNova (jamais présente dans StockManager) et vérifiée par
     l'application avec la clé publique ; son **activation** valide met à jour l'abonnement
     (statut, période) à partir de ses informations signées. L'import d'une licence ne crée
     ni ne confirme jamais un paiement.
2. **Nouveau statut `pending_activation`** : souscription enregistrée, aucun paiement
   confirmé, aucune licence activée. Politique d'accès (données :
   `subscription_policies.toml`) : `read`, `admin`, `billing` — connexion, configuration de
   l'entreprise, onboarding administratif, premier site, utilisateurs, rôles et permissions
   (selon le RBAC) ; toute opération métier (ventes, stock, caisse, catalogue…) est refusée
   par le backend (`403 subscription_restricted`).
3. **Inscription** (`POST /api/v1/public/signup`) :
   - réutilise `TenantProvisioningService` (une transaction : tout ou rien), rendu
     configurable : `create_first_site=False` (étape d'onboarding `first_site`),
     `subscription_start=PENDING_ACTIVATION`, mot de passe choisi (`must_change_password`
     faux), informations d'entreprise ;
   - plan publié (`listed`), actif, sans `contact_required`, période ouverte ; sinon
     `422 plan_not_available` (offre sur contact : « Contacter TechNova ») ;
   - `trial_days > 0` : `trial` pour N jours puis `expired` (comportement existant) ;
     `trial_days = 0` : `pending_activation`. Jamais `active`.
   - le nouvel utilisateur est **propriétaire** (`is_owner`, protégé) **et administrateur
     principal** (rôle système protégé) ; ces deux notions restent distinctes. La
     migration `0015` attribue aussi le rôle d'administration aux propriétaires existants.
   - le corps de requête est strict (`extra="forbid"`) : aucun champ d'état, de paiement,
     d'activation, de propriété ou d'administration plateforme n'est accepté du client.
4. **Anti-énumération** : e-mail déjà connu → `422 signup_unavailable` au message générique,
   **après** toutes les autres validations, avec un temps de hachage égalisé ; jamais de
   rattachement à un compte existant. Risque résiduel : un succès (session ouverte) reste
   distinguable d'un échec ; seule une vérification par e-mail (service d'envoi futur) le
   supprimera.
5. **Limitation de fréquence** : 5 inscriptions / heure / adresse IP par défaut
   (`SM_SIGNUP_RATE_LIMIT_ATTEMPTS`, `SM_SIGNUP_RATE_LIMIT_WINDOW_MINUTES`), persistée en base
   (`rate_limit_hits`, clé hachée) pour fonctionner avec plusieurs processus ; tentative
   comptée avant traitement ; `429 rate_limited` + `Retry-After`. Derrière un reverse proxy,
   l'IP réelle doit être transmise à uvicorn (`--proxy-headers --forwarded-allow-ips`).
   `SM_SIGNUP_ENABLED=false` ferme l'inscription (`403 signup_closed`).
6. **CLI TechNova inchangée** : `active` sans essai, `trial` avec `--trial-days`, site initial
   créé, mot de passe provisoire.

## Conséquences

- Hors périmètre de la 3.2-A : tables de paiements et de licences, génération et import de
  `.lic`, clés. Rien ne l'empêche : l'abonnement ne porte aucun champ de paiement ; le statut
  `pending_activation` est la porte d'entrée de l'activation future.
- Un tenant en attente d'activation ne peut pas créer d'articles : l'étape d'onboarding
  « catalogue » (recommandée) attend l'activation.
- `GET /public/plans` n'expose que les offres publiées et leurs informations publiques ; un
  prix n'est renvoyé que si TechNova l'affiche.

## Alternatives écartées

- **Abonnement `active` dès l'inscription** : considérerait un abonnement non payé comme
  payé ; contraire à la chaîne paiement → licence → activation.
- **Inscription réservée aux offres avec essai** : obligerait TechNova à configurer un essai
  pour ouvrir la vente en ligne.
- **Accepter la confirmation de paiement ou le statut depuis le client** : le client ne
  contrôle jamais un état financier ou technique.
