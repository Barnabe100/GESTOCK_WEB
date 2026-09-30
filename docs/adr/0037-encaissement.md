# ADR-0037 — Encaissement : moyens de paiement configurables, caisse optionnelle par site, monnaie, crédit et numérotation des ventes

- **Statut** : Acceptée (Lot 1 — Encaissement)
- **Date** : 2026-09-30

## Contexte

Le workflow d'encaissement (ventes 2.4, paiements 2.7, créances 2.8, caisse 2.9, point de vente
3.0) reposait sur des choix provisoires qu'il fallait aligner sur les décisions métier validées :

- moyens de paiement **fixes** (énumération `CASH`, `MOBILE_MONEY`…) sans configuration par
  l'entreprise ni par site ;
- caisse **obligatoire** pour tout paiement en espèces (session ouverte exigée sur le site),
  session d'une caisse utilisable par n'importe quel utilisateur du site ;
- aucun montant reçu ni monnaie rendue ;
- vente à crédit possible **sans client** (créance sans débiteur) et sans permission dédiée ;
  limite de crédit bloquante sans exception possible ;
- numéro de vente `VTE-000001` attribué **au brouillon**, par entreprise ;
- toute personne consultant les ventes voyait toutes celles de ses sites.

## Décision

1. **Moyens de paiement configurables** (`payment_methods`, module `sales`) : libellé libre
   (« Espèces », « Orange Money », « Wave »…), **type** (`CASH`, `MOBILE_MONEY`, `CARD`,
   `BANK_TRANSFER`, `OTHER`) qui seul gouverne le comportement (jamais le libellé), actif /
   inactif (jamais supprimé), référence obligatoire ou non, ordre d'affichage, mode de saisie
   `MANUAL` (le mode `API` est réservé : refusé `payment_integration_unavailable` tant
   qu'aucune intégration n'existe ; l'ajouter ne changera pas le modèle du paiement). Le type
   n'est jamais modifiable. Disponibilité **par site** (`payment_method_sites`, ligne absente :
   disponible). Permission `sales.payment_method.manage` (nature `admin`), revérifiée pour le
   site modifié. Chaque entreprise reçoit des moyens par défaut (`tenant_setup` ; migration
   pour les existantes), modifiables ensuite.
2. **Instantané sur le paiement** : `payment_method_id`, `method_label` (libellé au moment du
   paiement) et `method` (type) ; renommer ou désactiver un moyen ne modifie aucun paiement
   enregistré. Reprise : chaque paiement existant est rattaché au moyen par défaut de son type ;
   son libellé figé est la précision saisie autrefois (`provider`), sinon le libellé historique
   du type — rien n'est inventé, `provider` est conservé. Le rôle applicatif ne peut plus
   modifier que les colonnes d'annulation d'un paiement (droits SQL par colonne).
3. **Caisse optionnelle par site** (`cash_site_settings`, module `cash_register`) : site sans
   caisse → espèces encaissées sans session, fond, clôture ni comptage (aucun mouvement de
   caisse) ; site avec caisse → encaissement dans la session de l'utilisateur. Activation /
   désactivation : permission de configuration existante `organization.site.manage`,
   revérifiée pour CE site (jamais un nom de rôle) ; audit `cash_site.enabled|disabled`.
   **Désactivation refusée** (`409 cash_sessions_open`, message demandant la clôture) tant
   qu'une session est ouverte sur le site ; verrou exclusif du réglage (une ouverture
   concurrente attend et voit la caisse désactivée : `409 cash_disabled_for_site`) ;
   réactivation possible ; historique conservé. Reprise : caisse activée pour les sites qui
   avaient déjà des caisses (comportement inchangé), non activée ailleurs.
4. **Poste de caisse = ordinateur / machine physique** : l'entité existante `CashRegister`
   représente le poste (renommée « Poste de caisse » dans l'interface) ; un site peut en avoir
   plusieurs. Rien à voir avec une installation / licence (ADR-0035), un utilisateur ou une
   session. **Session = site + poste + utilisateur** : un encaissement en espèces n'utilise
   jamais la session d'un autre utilisateur ; plusieurs sessions ouvertes par l'utilisateur sur
   le site → poste à préciser (`cash_register_required`).
5. **Espèces : montant reçu et monnaie calculés par le serveur**, sur la seule partie espèces.
   Saisie : `amount_received` seul (montant imputé = min(reçu, reste dû)) ou avec `amount`
   (reçu ≥ montant, sinon `cash_received_insufficient`) ; `change_given = amount_received −
   amount` (contrainte en base). Aucun autre type ne rend de monnaie (`change_not_allowed`).
   Paiements immédiats d'une validation : autres moyens imputés d'abord, espèces ensuite
   (la monnaie ne porte que sur les espèces). Paiements multiples, partiels et mixtes inchangés.
6. **Crédit** : reste dû à la validation (total − encaissements immédiats) ⇒ vente à crédit
   (`sales.is_credit`). Client identifié **obligatoire** (`credit_customer_required`) ;
   permission `sales.sale.credit_create` (convention `module.ressource.action`) sur le site ;
   la permission ne lève aucune autre règle. Limite de crédit (NULL = pas de limite) : le
   dépassement est bloqué (`credit_limit_exceeded`, avec `override_allowed`) sauf **exception**
   autorisée par un utilisateur détenant `sales.sale.credit_override` sur le site, avec
   justification (5 à 500 caractères) : autorisateur, date, montant du dépassement et motif
   enregistrés sur la vente et audités (`sale.credit_limit_overridden`), dans la transaction de
   validation, sous le verrou du client (concurrence : ADR-0021). Statut **calculé**
   `OPEN` / `PARTIAL` / `PAID` / `CANCELLED` à partir des paiements effectués.
7. **Numérotation** `VENT-{CODE_SITE}-{ANNÉE}-{SÉQUENCE}` attribuée **à la validation** (jamais
   au brouillon, dont le numéro est nul), compteur `document_sequences` par tenant, site et
   année (clé `{site_id}:sale:{année}`, `BIGINT`, ligne verrouillée jusqu'à la fin de la
   transaction : numéros distincts et consécutifs sous concurrence ; un échec n'en consomme
   aucun). Rembourrage de présentation à 6 chiffres, sans limite (`999999` → `1000000`) ; année
   dans le fuseau du tenant (ADR-0028). Anciens numéros `VTE-…` conservés, jamais renumérotés ;
   un brouillon historique garde le sien. Numéro d'une vente validée **définitif** (déclencheur
   `sales_number_immutable`, même pour le propriétaire du schéma). Code de site **figé** dès son
   premier numéro (`409 site_code_locked`).
8. **Portée des ventes** : `sales.sale.view` = ses propres ventes ; `sales.sale.view_all` =
   toutes les ventes des sites autorisés (liste, fiche, paiements). Jamais de test sur un nom
   de rôle. Les créances ont leur propre permission et ne sont pas réduites à ses ventes.
   Rôles de base : Gestionnaire et Consultant reçoivent `view_all` (Gestionnaire :
   `credit_create`) ; le Vendeur voit ses ventes et vend comptant ; l'Administrateur a tout.

## Conséquences

- Une vente sans client doit être entièrement payée ; l'interface affiche « Ordinaire ».
- Les tests et E2E existants qui validaient des ventes non payées sans client ou encaissaient
  des espèces dans la session d'autrui ont été adaptés.
- Retour arrière (`downgrade`) refusé si des numéros `VENT-…` dépassent l'ancienne longueur.
- L'intégration d'API de paiement, la configuration des moyens par profil d'activité et les
  reçus imprimés restent des étapes ultérieures.

## Alternatives écartées

- **Libellé comme comportement** (ex. « Espèces » rend la monnaie) : fragile et traduisible ;
  le type fait foi.
- **Nouvelle entité « poste »** distincte de `CashRegister` : doublon ; la caisse existante est
  déjà l'objet physique d'un site.
- **Numéro au brouillon** puis renumérotation : trous et renumérotation interdits.
- **Séquence PostgreSQL par site et année** : création dynamique de séquences ; le compteur
  existant (`document_sequences`, verrou de ligne, rollback) suffit.
