# ADR-0044 — Emplacements physiques des articles par site

- **Statut** : Acceptée et validée (Lot 3-F — localisation physique du stock)
- **Date** : 2026-10-01
- **Prolonge** : [ADR-0033](0033-abonnement-par-site.md) (site = périmètre d'écriture),
  [ADR-0018](0018-transferts-inter-sites.md) (transferts),
  [ADR-0019](0019-inventaires.md) (inventaires)

## Contexte

Le stock est tenu par (site, article) ; rien n'indiquait OÙ se trouve un article dans un site.
Le Desktop (mono-site) n'avait qu'un champ texte global `articles.emplacement` (100) ; la règle
ART-08 du Web l'avait reporté « par site » (CATALOGUE_STOCK §2 : « l'emplacement physique devient
une donnée du site »). Le besoin : « où se trouve physiquement cet article dans ce site ? » —
sans système de gestion d'entrepôt.

## Décision (D1 à D9 validées par TechNova)

1. **Entité « Emplacement » par site** (`stock_locations`) : site obligatoire, nom (100,
   espaces de bord retirés) **unique par site sans distinction de casse** (actifs et inactifs) ;
   le même nom sur deux sites = deux emplacements distincts (D2, D3). Création, renommage,
   activation / désactivation ; **jamais de suppression**.
2. **Un emplacement COURANT au plus par article et par site, facultatif** (D1, D4) :
   `stock_article_locations (tenant, site, article → location)`, unique (tenant, site, article).
   **FK composite `(tenant_id, site_id, location_id)` → `stock_locations (tenant_id, site_id,
   id)`** : l'emplacement d'un autre site est inaffectable en base ; le service le refuse aussi
   (`422 stock_location_other_site`). Plusieurs articles par emplacement (D5).
3. **Aucune quantité par emplacement** : le stock reste par (site, article) ; `StockService`,
   le CMUP, les ventes, le POS et les règles de stock sont inchangés. Table d'affectation
   distincte de `stock_levels` : affecter un emplacement à un article jamais stocké sur le
   site ne crée aucun niveau (l'état « non stocké » et les alertes ne changent pas).
4. **Article non géré en stock** (service) : aucun emplacement (`422 article_not_stock_managed`).
   Article sans emplacement : « Non rangé » ; rien n'est jamais bloqué (réception, vente,
   sortie, transfert, inventaire).
5. **Désactivation** : l'emplacement n'est plus affectable (`422 stock_location_inactive`) ;
   les affectations existantes restent courantes (affichées « inactif ») ; reconfirmer
   l'affectation courante est sans effet.
6. **Affichage** (D6) : niveaux de stock (colonne, recherche par nom, filtre emplacement ou
   « non rangés », tri), inventaires (colonne, tri `location` = parcours de comptage, non rangés
   en fin), entrées et sorties (emplacement courant du site du document, indicatif), fiche
   article (vue par site visible : stock, état, emplacement). Pas dans la liste globale des
   articles (un emplacement par site).
7. **Information courante seulement** (D8) : aucun instantané dans les lignes de réception,
   sortie ou inventaire ; chaque changement d'affectation est audité
   (`stock_location.assigned` : article, site, emplacement avant / après, utilisateur, date) ;
   `stock_location.created|renamed|activated|deactivated`.
8. **Transferts** (D9) : aucune copie de l'emplacement du site source vers la destination.
9. **Permission** `stock.location.manage` (nature écriture ; Administrateur via `*`,
   Gestionnaire) : créer, renommer, activer / désactiver, affecter. Consultation :
   `stock.level.view`. Jamais `stock.threshold.manage`.
10. **Portée** : lecture limitée aux sites visibles (`filter_site_ids`) — un emplacement d'un
    site non visible est introuvable (`404 stock_location_not_found`) ; écriture sur un site
    accessible et autorisé par son abonnement (`operation_site`).
11. **API** : `GET|POST /stock/locations`, `PATCH /stock/locations/{id}`,
    `POST /stock/locations/{id}/activate|deactivate`,
    `PUT /stock/levels/{site_id}/{article_id}/location` (`{location_id | null}`) ;
    `GET /stock/levels` : `location_id`, `location_name`, `location_active`, filtres
    `location_id`, `unlocated`, tri `location` ; lignes d'inventaire et de documents :
    `location_name`.

## Hors périmètre

WMS, stock ou quantité par emplacement, mouvements internes, picking, préparation de commandes,
rangement guidé, optimisation, palettes, zones hiérarchiques, codes-barres et étiquettes
d'emplacement, adresses complexes, lots, péremption, FIFO / FEFO.

## Conséquences

- Migration 0033 : deux tables tenant-scoped, RLS `ENABLE` + `FORCE` ; rôle applicatif :
  emplacements `SELECT, INSERT, UPDATE (name, is_active, updated_at)` (aucune suppression) ;
  affectations `SELECT, INSERT, DELETE, UPDATE (location_id, updated_at)` (retrait = ligne
  supprimée, audité).
- Le module `inventory_count` lit l'emplacement courant par `stock.api.locations_view`
  (dépendance déclarée, aucun import des modèles du stock).
- **Validation** (TechNova, 2026-10-02) : décisions D1 à D9 appliquées ; lot validé et clôturé.
  État de référence : commit `366332d` (CI #48 verte), qui comprend l'implémentation `32922d2`
  (CI #47 verte) et le correctif mobile `366332d` — sur petit écran, la colonne « Emplacement »
  du comptage d'inventaire est masquée et l'emplacement est affiché sous l'article (aucun
  débordement) ; ce correctif fait partie de l'état validé du lot.
