"""Fermeture P1-b du suivi par lot (Lot 3-G, ADR-0045).

Le Lot 3-G livre le modèle des lots (référentiel, soldes par lot et par site, réceptions par lot)
mais la consommation des lots par les ventes, le POS, les sorties, les transferts et les
inventaires n'arrive qu'avec le Lot 3-H. Tant qu'elle n'existe pas, un article suivi par lot
verrait son stock baisser sans qu'aucun lot ne baisse : l'invariant Σ lots = stock (site,
article) serait rompu. Le passage d'un article au suivi par lot est donc REFUSÉ par le serveur.

Cette fermeture est une règle applicative du produit, figée dans le code : elle ne dépend ni du
tenant, ni du plan, ni de la console TechNova, ni d'un paramètre du client, ni d'une variable
d'environnement, ni d'un réglage. Seule la livraison validée du Lot 3-H (changement de code) la
lève, en passant ``LOT_TRACKING_AVAILABLE`` à ``True``.

Tests automatisés seulement : la fixture pytest ``lot_tracking_open`` remplace la constante
le temps d'un test (``monkeypatch``), dans le processus de test ; les tests de bout en bout
préparent leurs données avec le rôle propriétaire de la base (comme les autres données que
seule l'administration peut fixer). Aucun code de l'application ne modifie cette constante (test
statique).
"""

from typing import Final

LOT_TRACKING_AVAILABLE: Final[bool] = False


def lot_tracking_available() -> bool:
    """Activation du suivi par lot possible (Lot 3-H livré)."""
    return LOT_TRACKING_AVAILABLE
