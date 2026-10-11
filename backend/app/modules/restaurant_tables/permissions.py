"""Permissions du module ``restaurant.tables`` (ADR-0049 D14 : ``restaurant.tables.<ressource>.
<action>``). Associer une table à une commande ou en changer relève de la permission EXISTANTE
``restaurant.orders.order.create`` (A8, palier R5-B), jamais d'une permission de ce module."""

TABLE_VIEW = "restaurant.tables.table.view"
# Zones et tables : configuration du site (nature ``admin``).
TABLE_MANAGE = "restaurant.tables.table.manage"
