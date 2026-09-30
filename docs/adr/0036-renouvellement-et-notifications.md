# ADR-0036 — Renouvellement par site, tarif par poste et rappels d'échéance

- **Statut** : Acceptée (Phase 3.3-B4)
- **Date** : 2026-09-30

## Contexte

1 site = 1 abonnement = 1 licence (ADR-0033, ADR-0034) ; les postes consomment le quota de la
licence en vigueur (ADR-0035). Il manquait le **renouvellement** d'un site et les **rappels**
avant et après l'échéance. Règles TechNova : renouvellement par site ; période attachée à la
licence ; aucun jour perdu (ni offert) ; désactiver un poste ne suspend jamais la période ;
quota indépendant par site ; pas d'activation navigateur ; pas de paiement en ligne ;
notifications idempotentes avec historique ; ni e-mail, ni SMS, ni Celery / Redis.

Points d'impact relevés avant l'implémentation : le client choisissait la période et le montant
de sa déclaration ; la proposition de licence reprenait les postes **demandés à la
souscription** à chaque renouvellement ; un renouvellement pendant la grâce commençait
« aujourd'hui » (jours perdus) ; les capacités affichaient le plan de l'abonnement même quand un
changement de plan n'était pas encore couvert par une licence ; aucun tarif par poste.

## Décision

1. **Devis serveur (R3)** — `renewal_quote` (`app/platform/licensing/renewal.py`) calcule, pour
   l'abonnement d'UN site : type (`initial` sans licence, sinon `renewal`), plan (celui de
   l'abonnement, qui s'appliquera à la prochaine licence), période, postes, montant, continuité
   de grâce, pertinence du renouvellement. Exposé par `GET /subscriptions` (`renewal`) et
   `GET /subscriptions/{id}/renewal-quote?requested_activations=`. La déclaration
   (`POST /subscription/payments`) **n'accepte plus** `period_start` / `period_end` (`422`) :
   la période enregistrée est celle du devis. Montant : si un tarif est figé, calculé par le
   serveur (`422 amount_computed_by_server` si le client en envoie un) ; sinon (offre sur devis)
   le client déclare le montant convenu (`422 amount_required` s'il manque).
2. **Période (R2)** — `next_valid_from` : lendemain de la couverture en cours ; sans couverture,
   si la dernière licence est échue depuis **au plus** le délai de grâce de son plan, lendemain
   de cette licence (continuité, rien de perdu ni d'offert) ; au-delà, le jour de la génération
   (jamais rétroactif). Même règle pour le devis et pour la génération par TechNova.
3. **Postes (R1)** — proposés = ceux de la **licence de référence** du site (en vigueur, sinon la
   plus récente) ; les postes demandés à la souscription ne servent qu'à la première licence.
   Un autre nombre n'est retenu que sur **demande explicite** de l'entreprise
   (`requested_activations` du paiement, 1 à 10 000, enregistré seulement s'il diffère) ;
   TechNova le confirme ou l'ajuste à la génération (`max_activations`). Les postes actifs ne
   sont **jamais** libérés par un renouvellement ni par l'expiration ; quota réduit : postes
   existants tolérés, nouvelles activations refusées (ADR-0035).
4. **Offre en vigueur (R4)** — les droits restent ceux de la licence en vigueur
   (`PlanTerms`) ; `GET /subscriptions` expose `effective_plan` (plan de la licence en vigueur,
   sinon de l'abonnement) et `next_plan` (plan de l'abonnement s'il diffère) ; les capacités
   affichent le plan en vigueur. Aucun job ne bascule les droits : la licence suivante est en
   vigueur dès son premier jour, par simple calcul de dates.
5. **Tarif plan + postes** — paramètres commerciaux du plan (console, raison, audit) :
   `included_activations` (défaut 1), `monthly_activation_price`, `annual_activation_price`.
   Montant d'une période = prix de base + max(0, postes − compris) × prix par poste. Le tarif
   est **figé** sur l'abonnement du site à la souscription, à la création d'un site et au
   changement de plan (`included_activations_at_subscription`,
   `activation_price_at_subscription`, avec `price_at_subscription`) ; une modification du
   catalogue ne change jamais un abonnement existant. Aucun prix dans le frontend.
6. **Rappels d'échéance** — table `notifications` (tenant, site, abonnement, type
   `subscription.expiry`, étape, échéance = dernier jour couvert dans le fuseau de l'entreprise,
   statut `SENT` / `SKIPPED`, données) et `notification_reads` (lu / non lu **par membre**).
   Étapes : `SM_RENEWAL_NOTICE_DAYS` (défaut `30,15,10,5,1,0,-1,-7`, seule source de ces
   valeurs) ; J0 « Votre licence expire aujourd'hui », J+1 premier jour non couvert ; essais :
   J-5, J-1, J0 seulement. Abonnements notifiés : `active`, `past_due`, `expired`, `trial`
   (jusqu'à la dernière étape) ; jamais `pending_activation`, `suspended`, `cancelled`, ni une
   entreprise suspendue.
7. **Job** — `stockmanager notifications run [--now]`, quotidien (cron / systemd), rôle SQL de la
   console (lecture des abonnements, insertion des notifications ; aucune donnée métier), sans
   nouvelle infrastructure. Idempotence : unicité `(tenant, abonnement, type, étape, échéance)`
   et `ON CONFLICT DO NOTHING` ; une échéance modifiée (renouvellement) ouvre une nouvelle série.
   Pas de rafale : seule l'étape la plus récente due est envoyée, les étapes antérieures jamais
   enregistrées (job manqué) sont notées `SKIPPED` (jamais affichées). Exclusion mutuelle :
   `pg_try_advisory_xact_lock` ; une exécution concurrente s'arrête sans rien écrire. Journal
   de la plateforme : `notifications.run` (compteurs).
8. **Visibilité** — `GET /notifications`, `GET /notifications/unread-count`,
   `POST /notifications/{id}/read`, `POST /notifications/read-all` : permission
   `subscription.subscription.view` revérifiée **site par site**, sites accessibles seulement ;
   RLS `ENABLE` + `FORCE` ; rôle applicatif : lecture des notifications, lecture et ajout de SES
   lectures (politique `user_id = app_current_user_id()`) ; aucune route n'écrit une
   notification. Interface : indicateur dans la barre supérieure (déclaré par le module
   `subscription`), rappels non lus en tête de la page Abonnement, centre `/notifications`
   (historique conservé).
9. **Révocation** — inchangée (ADR-0034) : une licence révoquée n'est jamais restaurée ; un
   renouvellement déjà payé n'est pas substitué automatiquement (le site reste suspendu, même
   quand la période de la licence suivante arrive) ; TechNova réémet explicitement.

## Conséquences

- Migration 0023 : tables `notifications`, `notification_reads` ; colonnes de tarif par poste
  (plans, abonnements) ; `subscription_payments.requested_activations` (déclencheur de
  finalité mis à jour).
- Clients de l'API : la déclaration n'envoie plus de période ; le montant seulement pour une
  offre sans tarif.
- Moyens de paiement : liste fixe inchangée dans cette phase (déclaration manuelle ; aucune
  intégration Orange / Moov / cartes) ; leur configuration par TechNova reste à faire.

## Alternatives écartées

- **Période choisie par le client** : source de trous ou de chevauchements ; contraire à
  « aucune confiance dans le frontend ».
- **Renouvellement après la grâce rétroactif** : offrirait des jours déjà écoulés sans licence.
- **Job de bascule nocturne des droits** : inutile, la licence en vigueur se déduit des dates.
- **File de messages (Celery / Redis), e-mail, SMS** : hors périmètre ; le job idempotent suffit.
- **Rattrapage de toutes les étapes manquées** : rafale de rappels obsolètes.
