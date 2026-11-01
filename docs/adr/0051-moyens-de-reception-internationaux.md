# ADR-0051 — Moyens de réception indépendants du pays, de l'opérateur et de la devise

- **Statut** : Proposée (correction stratégique « portée internationale », en attente de validation)
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
| Devise par défaut du modèle `Tenant` | `default="XOF"` sur la colonne (l'inscription fixe pourtant la devise depuis le pays) | À retirer |
| Pays | Référentiel ISO 3166-1 complet (249 codes, devise ISO 4217, indicatif E.164, fuseau) | Oui, réutilisable |

## Décisions proposées

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
- Un moyen peut aussi désigner un **opérateur libre** (nom saisi) lorsqu'il n'est pas au
  catalogue : aucune liste fermée n'est imposée à l'entreprise.

### M3 — Pays et devise d'un moyen

- `country_code` (référentiel `geo_countries`, facultatif : moyen international) et `currency`
  (ISO 4217, par défaut la devise de l'entreprise).
- Ventes mono-devise (devise de l'entreprise figée) : un moyen d'une autre devise est enregistré
  mais **refusé à l'encaissement** par le serveur (`payment_method_currency_mismatch`) tant que
  le multidevise n'existe pas. Aucune conversion n'est supposée.

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
- Plusieurs opérateurs et plusieurs identifiants par site.
- **Aucun format universel** :
  - un format n'est contrôlé que s'il est déclaré par l'opérateur du catalogue pour ce type
    d'identifiant ;
  - sinon, seul un contrôle générique s'applique (texte non vide, longueur, caractères
    imprimables) ;
  - un téléphone est normalisé avec l'indicatif du pays du moyen (référentiel), sans supposer de
    longueur nationale.
- RLS `ENABLE` + `FORCE`, FK composites `(tenant_id, …)`, audit avant / après, permission
  existante `sales.payment_method.manage` (revérifiée pour le site d'un identifiant de site).
- Le paiement conserve un **instantané** (opérateur, identifiant utilisé) comme `method_label`
  aujourd'hui.

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
- Retirer la valeur par défaut `XOF` du modèle `Tenant` : la devise vient toujours du pays choisi
  et est validée à l'inscription.

## Conséquences

- Déployer un nouveau pays = activer le pays dans le référentiel + ajouter ses opérateurs au
  catalogue. Aucun code n'est modifié.
- Les moyens existants restent valides : leur pays et leur devise sont initialisés par migration
  avec ceux de l'entreprise ; leurs opérateurs restent libres.
- Les paiements d'abonnement (ADR-0032, champ `provider` libre) pourront utiliser le même
  catalogue.

## Questions ouvertes (à valider avant toute implémentation)

1. Périmètre : cette refonte est-elle **l'étape R7**, ou un lot distinct à placer avant R7 ?
2. Liste initiale des opérateurs et établissements du Burkina Faso à inscrire au catalogue (noms,
   types d'identifiants), à fournir ou confirmer par TechNova.
3. Affichage des identifiants de réception : sur le reçu, au point de vente, sur le futur menu
   QR ?
4. Moyen dans une autre devise : refus à l'encaissement (proposé) ou interdiction dès la saisie ?
