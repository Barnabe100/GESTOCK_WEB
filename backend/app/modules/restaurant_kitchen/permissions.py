"""Permissions du module ``restaurant.kitchen`` (ADR-0049 D14 : ``restaurant.kitchen.<ressource>.
<action>`` ; ADR-0050 K5). Préparer une ligne (commencer, prête, retour) reste la permission
EXISTANTE ``restaurant.orders.order.prepare`` ; servir, ``restaurant.orders.order.serve``."""

# Postes, affectations et écran cuisine (l'écran exige aussi ``restaurant.orders.order.view``).
STATION_VIEW = "restaurant.kitchen.station.view"
# Postes et affectations : configuration du site (nature ``admin``).
STATION_MANAGE = "restaurant.kitchen.station.manage"
# Correction manuelle du poste d'une ligne.
LINE_ROUTE = "restaurant.kitchen.line.route"
# Priorité d'une commande.
PRIORITY_SET = "restaurant.kitchen.priority.set"
