"""Permissions du module ``restaurant.qr`` (ADR-0049 D14 : ``restaurant.qr.<ressource>.<action>``).

Confirmer ou refuser une commande reçue par QR Code relève de la permission des COMMANDES
``restaurant.orders.order.confirm`` (P-12), jamais d'une permission de ce module."""

POINT_VIEW = "restaurant.qr.point.view"
# Points QR (création, désactivation, régénération du jeton) : configuration du site.
POINT_MANAGE = "restaurant.qr.point.manage"
# Réglages QR du site (plafonds, informations demandées au client).
SETTINGS_MANAGE = "restaurant.qr.settings.manage"
