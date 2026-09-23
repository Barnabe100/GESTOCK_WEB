"""Modules métier (catalogue, stock, ventes, caisse, restaurant, ...).

Chaque module est un paquet autonome qui déclare un manifeste (code, dépendances,
permissions, entrées de navigation) et n'est chargé/exposé que si la capacité
correspondante est active pour le tenant. Voir docs/architecture/ARCHITECTURE.md (section 5).
"""
