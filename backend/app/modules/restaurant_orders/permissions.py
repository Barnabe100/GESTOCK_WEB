"""Permissions des commandes (ADR-0049, D14) : préfixées par le code EXACT du module
(``restaurant.orders``), selon « module.ressource.action ». Prise en charge et réattribution
(``order.claim``, ``order.reassign``) : sous-étape R2-C (D7) ; confirmation QR
(``order.confirm``) : palier R7, à son premier usage (P-12)."""

ORDER_VIEW = "restaurant.orders.order.view"
ORDER_CREATE = "restaurant.orders.order.create"
ORDER_PREPARE = "restaurant.orders.order.prepare"
ORDER_SERVE = "restaurant.orders.order.serve"
ORDER_CANCEL = "restaurant.orders.order.cancel"
ORDER_CANCEL_PREPARED = "restaurant.orders.order.cancel_prepared"
ORDER_CLAIM = "restaurant.orders.order.claim"
ORDER_REASSIGN = "restaurant.orders.order.reassign"
SETTINGS_MANAGE = "restaurant.orders.settings.manage"
