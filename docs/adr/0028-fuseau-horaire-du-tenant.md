# ADR-0028 — Fuseau horaire du tenant : obligatoire, référence du temps métier

- **Statut** : Acceptée (formalisation, Phase 3.2-C ; contrainte existante depuis la Phase 1)
- **Date** : 2026-09-25

## Contexte

La page Entreprise (3.2-C) marque le fuseau horaire d'un « * », en plus du nom, du pays et de
la devise. Vérification faite, ce n'est pas une règle nouvelle : le fuseau est une contrainte
structurelle depuis le socle, jamais formalisée dans une ADR.

- `tenants.timezone` : `NOT NULL` depuis la migration `0001` (identifiant IANA) ;
  `ARCHITECTURE.md` : « `timestamptz` stockées en UTC ; fuseau d'affichage par tenant » ;
  `CATALOGUE_STOCK.md`, règle 5 : « aujourd'hui » est évalué dans le fuseau du tenant, pas
  celui du serveur (ENT-02).
- Provisioning (CLI et inscription) : fuseau du pays par défaut, validé (`invalid_timezone`) ;
  `PATCH /tenant` : validé IANA, jamais effacé (`null` ignoré, vide refusé).
- Usages dans le code :
  - **date du jour métier** `tenant_today` : date de vente par défaut et interdiction d'une
    date future (ventes), date d'opération des entrées, sorties et transferts (ENT-02) ;
  - **caisse** : filtres de sessions par jour (`_day_bounds` : jour civil local → bornes
    UTC) ;
  - **inventaires** et **journal des mouvements** : filtres `date_from` / `date_to` en jours
    du fuseau du tenant (`timezone(tz, occurred_at)` en SQL) ;
  - **interface** : dates et heures affichées dans le fuseau du tenant
    (`capabilities.tenant.timezone`, `formatDateTime`).

## Décision

1. **Règle** : tout horodatage est stocké en UTC (`timestamptz`) ; toute notion **métier** de
   date — « aujourd'hui », jour d'une vente, d'une opération ou d'une session de caisse,
   filtres par jour, et demain périodes jour / semaine / mois des rapports — est calculée
   dans le **fuseau du tenant** (`tenants.timezone`, identifiant IANA), jamais dans celui du
   serveur ni du navigateur.
2. Le fuseau est donc **obligatoire** (« * ») : proposé par le pays à la création, modifiable
   (validé, audité), jamais effacé.
3. **Multi-tenant** : chaque tenant a son propre fuseau ; deux tenants de fuseaux différents
   ont chacun leurs journées, sans interférence. Tous les sites d'un tenant partagent ce
   fuseau (un fuseau par site serait une évolution future, à décider si un client s'étend sur
   plusieurs fuseaux).
4. **Dates figées** : les dates métier enregistrées (`sale_date`, `operation_date`) sont des
   dates calendaires fixées à la saisie ; changer ensuite le fuseau ne les modifie pas, il ne
   change que l'interprétation des horodatages (affichage, filtres par jour).
5. **Futurs documents et rapports** : dates et heures imprimées dans le fuseau du tenant ;
   périodes (jour, semaine, mois) bornées dans ce fuseau puis converties en UTC pour les
   requêtes, comme le fait déjà la caisse.

## Conséquences

- Aucun changement de code ni de schéma : la contrainte existait ; elle est désormais une
  règle d'architecture (CLAUDE.md, règle 15).
- Un module qui calcule une date ou une période doit utiliser le fuseau du tenant
  (`tenant_today`, bornes converties), jamais `date.today()` ni l'heure du serveur.

## Alternatives écartées

- **Fuseau facultatif (repli sur UTC ou sur le serveur)** : « aujourd'hui » et les journées de
  caisse deviendraient faux pour les tenants éloignés d'UTC ; comportement dépendant du
  déploiement.
- **Fuseau du navigateur** : deux utilisateurs du même tenant verraient des journées
  différentes ; le backend ne peut pas en dépendre.
