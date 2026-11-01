# ADR-0051 — Moyens de réception indépendants du pays, de l'opérateur et de la devise

- **Statut** : Acceptée — orientation validée le 2026-11-01 (décisions D1 à D5), livraison R6.5
  validée par l'utilisateur le 2026-11-01 avec deux clarifications (confirmation, numéros de
  téléphone : section « Clarifications de clôture »)
- **Lot** : **R6.5**, distinct, réalisé APRÈS la validation fonctionnelle de R6 (Cuisine) sur
  Ubuntu et AVANT R7 (Menu QR Code, numérotation inchangée)
- **Date** : 2026-11-01
- **Prolonge** : [ADR-0037](0037-encaissement.md) (moyens de paiement configurables), ADR-0020
  (paiements des ventes), ADR-0032 (paiements d'abonnement), référentiel des pays (Phase 3.2,
  `countries.toml` → `geo_countries`)

## Contexte

StockManager Web est commercialisé d'abord au Burkina Faso mais doit pouvoir être déployé dans
n'importe quel pays **sans modification structurelle du code**. Le pays, l'opérateur, le type de
moyen, la devise et l'identifiant de réception doivent être des **données configurables**, jamais
des constantes métier.

### État des lieux (audit du 2026-11-01)

| Élément | Situation actuelle | Conforme ? |
| --- | --- | --- |
| Type de moyen (`payment_methods.kind`) | `CASH`, `MOBILE_MONEY`, `CARD`, `BANK_TRANSFER`, `OTHER` : **comportement** (monnaie rendue, caisse), jamais un pays ni un opérateur | Oui |
| Libellé du moyen | Texte libre de l'entreprise (60) ; instantané `method_label` sur le paiement | Oui |
| Opérateur | Aucun nom d'opérateur dans la logique ; seulement du texte libre (`provider`, 50, paiements d'abonnement) | Oui, mais non structuré |
| Disponibilité par site | `payment_method_sites` (ligne absente : disponible) | Oui |
| Nombre de moyens | Aucune limite | Oui |
| Pays / devise d'un moyen | **Absents** | Non (exigence 4) |
| Catalogue d'opérateurs et d'établissements | **Absent** | Non (exigence 3) |
| Identifiants de réception (téléphone, code marchand, code agent, compte…) | **Absents** | Non (exigences 5, 7) |
| Passerelles | `integration_mode = API` réservé, aucune dépendance | Oui (point d'extension) |
| Textes d'interface | Exemples burkinabè dans les aides (`labelPlaceholder`, `providerPlaceholder`, `paymentReferenceHelp` : « Orange Money, Moov Money, Wave… ») | À neutraliser |
| Devise `XOF` codée en dur | `Tenant.currency` : `default="XOF"` (`platform/tenancy/models.py`, l'inscription fixe pourtant la devise depuis le pays) ; `saleError(…, currency = 'XOF')` (`frontend/src/modules/sales/ui.tsx`) ; commentaire de `shared/lib/decimal.ts` ; données de test | À retirer (D4) |
| Pays | Référentiel ISO 3166-1 complet (249 codes, devise ISO 4217, indicatif E.164, fuseau) | Oui, réutilisable |

## Décisions de l'utilisateur (2026-11-01)

- **D1 — Feuille de route** : lot distinct **R6.5** après la validation de R6 sur Ubuntu, avant
  R7 (Menu QR Code). R6.5 commence par un audit de l'existant, puis une implémentation
  progressive et testée.
- **D2 — Catalogue** :
  - catalogue initial géré par TechNova, opérateurs et établissements associés à leurs pays et
    devises ;
  - **aucune liste exhaustive figée** : les opérateurs burkinabè sont ajoutés progressivement,
    après vérification de leurs types d'identifiants ;
  - **aucun opérateur obligatoire**, et le catalogue n'est **jamais une condition** pour
    enregistrer un moyen : une entreprise peut toujours enregistrer un opérateur personnalisé
    absent du catalogue ;
  - plusieurs identifiants possibles pour un même opérateur et un même site.
- **D3 — Affichage (V1)** :
  - identifiants de réception affichés sur l'**écran de caisse** (POS et dialogue
    d'encaissement), lors du choix du moyen de règlement ;
  - identifiants affichés sur le **reçu**, avec un **instantané** des informations réellement
    utilisées, figé sur le paiement ;
  - **aucun paiement dans le parcours du Menu QR** (traité par R7, sans paiement intégré pour
    l'instant).
- **D4 — Devises** :
  - un moyen peut être configuré dans une devise différente de celle de l'entreprise ;
  - il n'encaisse une vente que si **sa devise est celle de la vente** (refus serveur sinon) ;
  - **aucune conversion automatique** dans cette version ;
  - toute dépendance à `XOF` codée en dur est supprimée : la devise par défaut de l'entreprise
    provient du **référentiel du pays**, comme aujourd'hui à l'inscription.
- **D5 — Préservation** : fonctionnalités existantes, règles multi-tenant, RLS et RBAC conservées ;
  modifications locales non liées au développement préservées.

## Décisions proposées (amendées par D1 à D5)

### M1 — Trois notions distinctes

1. **Type de moyen** (`kind`) : comportement technique fermé (espèces, mobile money, carte,
   virement, autre). Il ne change pas d'un pays à l'autre et reste dans le code.
2. **Opérateur / établissement financier** : **donnée de référence** (catalogue versionné), jamais
   une constante.
3. **Moyen de réception de l'entreprise** : donnée du tenant (libellé, type, opérateur, pays,
   devise, identifiants, disponibilité par site).

### M2 — Catalogue des opérateurs et établissements (données de référence)

- Fichier versionné `payment_providers.toml` → table de plateforme `ref_payment_providers`,
  synchronisée par `stockmanager catalog sync` et en **lecture seule** pour l'application (même
  modèle que `countries.toml` → `geo_countries`). Il est maintenu par TechNova, jamais par une
  entreprise.
- Champs : `code` stable, nom (traductions par langue), types de moyens couverts (`kinds`), pays
  couverts (codes ISO ; vide = international), devises acceptées (ISO 4217), types d'identifiants
  acceptés avec, **facultativement**, un format par type (longueur, motif), `is_active`, ainsi
  que des instructions éventuelles traduites.
- Ajouter un pays ou un opérateur = **ajouter des données**, jamais du code. La première
  commercialisation renseigne les opérateurs du Burkina Faso dans ce fichier.
- Un moyen peut aussi désigner un **opérateur personnalisé** (nom saisi) lorsqu'il n'est pas au
  catalogue, ou aucun opérateur (espèces, autre…) : aucune liste fermée, aucun opérateur
  obligatoire, le catalogue n'est jamais requis pour enregistrer un moyen (D2).
- Le catalogue initial reste volontairement **minimal et non exhaustif** ; une entrée n'y est
  ajoutée qu'après vérification de ses types d'identifiants (D2).

### M3 — Pays et devise d'un moyen

- `country_code` (référentiel `geo_countries`, facultatif : moyen international) et `currency`
  (ISO 4217, par défaut la devise de l'entreprise).
- Règle validée (D4) : un moyen dans une autre devise peut être enregistré, mais il n'encaisse
  une vente que si sa devise est **celle de la vente**. Le serveur refuse sinon
  (`payment_method_currency_mismatch`), à l'encaissement comme au point de vente. Aujourd'hui,
  la devise d'une vente est celle de l'entreprise, figée. Aucune conversion automatique n'est
  faite.
- La devise par défaut d'un nouveau moyen est celle de l'entreprise, issue du référentiel du pays
  (D4).

### M4 — Identifiants de réception

- Nouvelle table du tenant `payment_method_accounts`, sans limite de nombre :
  - moyen ;
  - site (facultatif, vide = tous les sites) ;
  - type d'identifiant : `PHONE`, `MERCHANT_CODE`, `AGENT_CODE`, `ACCOUNT_NUMBER`, `CUSTOM` ;
  - libellé personnalisé, **obligatoire** pour `CUSTOM` ;
  - valeur ;
  - titulaire ;
  - instructions ;
  - ordre d'affichage et état actif ;
  - désactivation au lieu de la suppression.
- Plusieurs opérateurs par site, et plusieurs identifiants pour un même opérateur et un même
  site (D2).
- **Aucun format universel** :
  - un format n'est contrôlé que s'il est déclaré par l'opérateur du catalogue pour ce type
    d'identifiant ;
  - sinon, seul un contrôle générique s'applique (texte non vide, longueur, caractères
    imprimables) ;
  - un téléphone est normalisé avec l'indicatif du pays du moyen (référentiel), sans supposer de
    longueur nationale.
- RLS `ENABLE` + `FORCE`, FK composites `(tenant_id, …)`, audit avant / après, permission
  existante `sales.payment_method.manage` (revérifiée pour le site d'un identifiant de site).
- Le paiement conserve un **instantané** des informations réellement utilisées (opérateur, type
  et valeur de l'identifiant, titulaire), comme `method_label` aujourd'hui. Le reçu est construit
  par le serveur depuis cet instantané, jamais depuis la configuration courante (D3,
  ADR-0047).
- Affichage V1 (D3) : écran de caisse (POS et dialogue d'encaissement) à la sélection du moyen ;
  reçu. Rien dans le Menu QR (R7).

### M5 — Internationalisation

- Les libellés et instructions du catalogue portent leurs traductions. Les données saisies par
  l'entreprise restent dans sa langue.
- Les textes d'interface passent toujours par i18n, et leurs exemples ne citent aucun opérateur
  national (placeholders neutres).
- Un test statique interdit les noms d'opérateurs dans le code applicatif. C'est la même règle
  que pour les secteurs et les profils (CLAUDE.md, règle 4).

### M6 — Passerelles de paiement (plus tard, jamais obligatoires)

- `integration_mode` (`MANUAL` / `API`) est conservé.
- Une intégration future se déclarera par un **port** (connecteur fourni par un module,
  référencé par un code du catalogue), sans dépendance du module des ventes vers un fournisseur.
- Le mode manuel reste toujours disponible ; l'état `PENDING` des paiements, déjà réservé, sert
  aux encaissements asynchrones.

### M7 — Corrections immédiates sans changement de schéma

- Neutraliser les exemples d'opérateurs des textes d'interface.
- Retirer toute dépendance à `XOF` codée en dur (D4) : valeur par défaut du modèle `Tenant`
  (la devise vient toujours du référentiel du pays, validée à l'inscription), paramètre par
  défaut de `saleError` (devise lue dans les capacités), commentaires ; les données de test
  gardent une devise explicite.
- Ces corrections font partie de R6.5 et ne sont pas appliquées avant son lancement (D1).

## Implémentation R6.5 (2026-11-01)

Livrée par étapes A à G, puis clarification du téléphone (validation de la livraison) :

| Étape | Contenu | Migration |
| --- | --- | --- |
| A | Plus aucune devise (`XOF`), aucun fuseau national ni nom d'opérateur dans le code ; test statique | — |
| B | Catalogue `payment_providers.toml` (vide) → `ref_payment_providers` (SELECT seul), `GET /payment-providers` | 0051 |
| C | `payment_methods` : opérateur (catalogue / personnalisé / aucun), pays, devise ; règle de devise | 0052 |
| D | `payment_method_accounts` : identifiants propres à un site ou communs, sans limite | 0053 |
| E | Paiements : confirmation manuelle (`confirmed_by` / `confirmed_at`), devise, instantané des coordonnées | 0054 |
| F | Interface : administration (opérateur, pays, devise, identifiants par portée, accès depuis la page Sites), caisse (identifiant, confirmation), reçu, historique | — |
| G | Vérification globale, documentation | — |
| Clarif. | Pays de rattachement des identifiants, numéros locaux / internationaux, instantané du pays sur le paiement | 0055 |

Précisions de mise en œuvre (fidèles aux décisions D1 à D5) :

- **Confirmation** : un paiement hors espèces exige `received_confirmed` (case « J'ai vérifié que
  les fonds ont bien été reçus ») ; sinon `422 payment_confirmation_required`, contrôlé après les
  règles de saisie (référence, monnaie). Les **espèces**, remises en main propre, sont confirmées
  par leur enregistrement. Tout paiement encaissé porte `confirmed_by` et `confirmed_at`
  (contrainte `completed_is_confirmed`) ; `PENDING` reste réservé aux passerelles futures. Les
  paiements antérieurs sont repris comme confirmés par leur auteur à leur date d'encaissement.
- **Identifiant à la caisse** : jamais deviné par le serveur ; l'interface présélectionne l'unique
  identifiant actif, laisse choisir parmi plusieurs (« Aucun identifiant » toujours possible).
  Contrôlé à l'encaissement : même moyen, actif, commun ou propre au site de la vente
  (`payment_account_unavailable`), verrou partagé.
- **Portées** : identifiant propre à un site géré avec `sales.payment_method.manage` sur CE site ;
  identifiant commun géré seulement avec la permission sur TOUS les sites ; lecture limitée aux
  identifiants communs et aux sites accessibles (autres : introuvables).
- **Formats** : règle de longueur ou de motif appliquée seulement si l'opérateur du catalogue la
  déclare ; types acceptés limités par le catalogue s'il en déclare. Téléphone : voir
  « Clarifications de clôture ».
- **Devise** : contrôlée dans `resolve_method` (point unique des ventes, du POS et du règlement
  des commandes) ; la liste par site renvoie `unavailable_reason = currency_mismatch` ; la
  compatibilité « par type » ne choisit jamais un moyen d'une autre devise.
- **Capacités** : `tenant.country_code` ajouté (pays proposé par défaut d'un nouveau moyen).
- **Permissions** : aucune nouvelle.

## Clarifications de clôture (validation du 2026-11-01)

### C1 — Confirmation des paiements

- **Espèces** : l'enregistrement vaut confirmation ; aucune case supplémentaire.
- **Hors espèces** : confirmation explicite obligatoire (`received_confirmed`) après vérification
  effective de la réception des fonds ; choisir le moyen ou l'identifiant n'en tient jamais lieu.
- Dans les deux cas, l'utilisateur ayant confirmé (`confirmed_by`) et la date / heure de
  confirmation (`confirmed_at`, UTC, affichée dans le fuseau du tenant) restent historisés sur le
  paiement (contrainte `completed_is_confirmed`).

### C2 — Numéros de téléphone : local, international, pays de rattachement

Comportement retenu (migration **0055**, cohérent avec M4 et D3 : aucun format universel) :

1. **Pays de rattachement** : chaque identifiant porte `country_code` (référentiel des pays), par
   défaut celui du moyen, modifiable, `NULL` = international. Les identifiants existants sont
   repris avec le pays de leur moyen.
2. **Saisie** : séparateurs (espaces, points, tirets, parenthèses) retirés ; rien d'autre n'est
   réécrit (un préfixe « 00 » n'est pas converti en « + » : le numéro est alors traité comme une
   forme locale, conservée telle quelle).
3. **Forme internationale** (« + » suivi de 15 chiffres au plus, E.164) : conservée telle quelle ;
   si le pays de rattachement déclare un indicatif, le numéro doit commencer par cet indicatif
   (`422 payment_account_invalid`, `reason = phone_country_mismatch`, avec `country_code` et
   `calling_code`). **L'indicatif n'est jamais ajouté une seconde fois.**
4. **Forme locale** (sans « + ») : conservée **telle quelle**, sans longueur imposée ;
   l'indicatif du pays de rattachement n'est **jamais ajouté** au numéro enregistré ; il est
   seulement **affiché à côté** (« 70112233 · BF +226 ») dans l'administration, à la caisse et
   sur le reçu.
5. **Aucun format national imposé** : la seule règle supplémentaire possible est celle qu'un
   opérateur vérifié du catalogue déclare (longueur, motif) ; sans règle fiable, contrôle
   générique seulement.
6. **Instantané** : le paiement fige `account_country_code` avec la valeur ; l'historique et le
   reçu affichent l'indicatif de ce pays, même si l'identifiant change ensuite.
7. La descente de 0055 est refusée si un identifiant a un pays différent de celui de son moyen
   ou si un paiement porte un pays d'identifiant (aucune perte silencieuse).

### C3 — Résultats de validation (2026-11-01)

- Backend : 1 476 tests réussis à la livraison (`986a1c0`) ; après la clarification C2
  (`38137bf`) : `tests/test_receiving_methods.py` 23 réussis ; suite complète **1 479 réussis**
  (0 échec, 43 min).
- Frontend : Vitest 536 réussis ; E2E `receiving.e2e.ts` 5 / 5.
- E2E complets : 171 réussis, 6 échecs **non masqués** — 5 liés à l'environnement (service de
  signature, limitation de fréquence) et 1 intermittent (`lots-3ha.e2e.ts`), consignés dans
  [`SUIVI_TECHNIQUE.md`](../SUIVI_TECHNIQUE.md) (A-1, E-1 à E-5) ; A-1 à résoudre avant la
  campagne de validation sur Ubuntu.
- CI GitHub Actions verte sur `986a1c0` (run 41308912290).

## Conséquences

- Déployer un nouveau pays = activer le pays dans le référentiel + ajouter ses opérateurs au
  catalogue. Aucun code n'est modifié.
- Les moyens existants restent valides : leur pays et leur devise sont initialisés par migration
  avec ceux de l'entreprise ; leurs opérateurs restent libres.
- Les paiements d'abonnement (ADR-0032, champ `provider` libre) pourront utiliser le même
  catalogue.

## Questions ouvertes

Les questions de la première version sont tranchées par D1 à D4. Restent à préciser lors de
l'audit de R6.5 :

1. Contenu exact du catalogue initial : entrées vérifiées par TechNova, ajoutées progressivement
   (D2).
2. Langues des libellés traduits au-delà du français (l'interface n'existe aujourd'hui qu'en
   français).
