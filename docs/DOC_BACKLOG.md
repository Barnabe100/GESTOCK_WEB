# Documentation — points à reprendre lors de la révision globale finale

Consigne : la documentation complète est mise à jour à la fin de l'implémentation de
l'application. Ce fichier garde la trace des changements de comportement, permissions,
migrations et décisions qui ne sont PAS encore reportés dans les documents de référence. Les
décisions elles-mêmes restent tracées dans les ADR au moment où elles sont prises.

## R2-E — association tardive du client (Z1)

Décision tracée : [ADR-0049](adr/0049-restauration-commandes.md), section « Décisions du palier
R2-E ». À reporter :

- `docs/architecture/API.md` (commandes de restauration) : route
  `PUT /restaurant/orders/{id}/customer` (permission `restaurant.orders.order.create`, refus
  `order_settled` / `order_closed` / `order_cancelled`, `customer_unavailable`,
  `customer_not_found`, `customer_inactive`, `customer_change_reason_required`).
- `docs/architecture/DATA_MODEL.md` : migration 0044 (`GRANT UPDATE (customer_id)` sur
  `restaurant_orders`, type d'évènement `CUSTOMER_SET`, descente refusée si une association
  existe).
- `docs/architecture/RESTAURANT.md` : parcours Z1 (servie non réglée → client associé → crédit
  selon les règles des ventes), tableau des refus, liste des évènements, migrations.
- Guide utilisateur (à créer) : « Associer un client » / « Changer de client » sur la fiche
  d'une commande.

## R2-E — désignation d'un client sans consultation des clients (vérification du risque résiduel)

Comportement confirmé (aucune règle modifiée) et durci, à reporter dans `RESTAURANT.md`,
`CLIENTS.md` et la section sécurité / RBAC :

- Désigner un client par son identifiant dans une opération (création de commande, association
  tardive, comme une vente avec `sales.sale.create`) relève de la permission de l'opération ;
  `customers.customer.view` régit la CONSULTATION du référentiel (`/customers`) et la recherche
  dans l'interface. Une commande n'expose du client que `customer_id` et `customer_name`
  (réponses et évènement `CUSTOMER_SET`) ; l'audit exige `audit.log.view`.
- Durcissement : le refus `customer_inactive` des commandes de restauration ne joint le code du
  client (`customer_code`) qu'à un membre détenant `customers.customer.view` sur le site.
- Point ouvert (hors restaurant, non modifié) : les ventes (`sales`) joignent toujours
  `customer_code` au refus `customer_inactive`, quelle que soit la permission de l'appelant.
