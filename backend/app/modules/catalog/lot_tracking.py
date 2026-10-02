"""Disponibilité du suivi par lot — P1-b LEVÉE (Lot 3-H clôturé, ADR-0045).

Le Lot 3-G a livré le modèle des lots (référentiel, soldes par lot et par site, réceptions par
lot) avec une fermeture P1-b : tant que la consommation des lots n'existait pas, un article
suivi par lot aurait vu son stock baisser sans qu'aucun lot ne baisse (invariant Σ lots = stock
rompu), et le serveur refusait donc le passage au suivi par lot (``lot_tracking_unavailable``).

Le Lot 3-H a livré la consommation des lots par tous les flux (ventes, POS, sorties, transferts,
inventaires), les annulations exactes, le garde-fou serveur (``lot_required``) et la garde du
changement de suivi : la fermeture est levée, ``LOT_TRACKING_AVAILABLE`` vaut ``True``.

La disponibilité reste une règle applicative du produit, figée dans le code : elle ne dépend ni
du tenant, ni du plan, ni de la console TechNova, ni d'un paramètre du client, ni d'une variable
d'environnement, ni d'un réglage. Aucun code de l'application ne modifie cette constante (test
statique) ; les tests peuvent la remplacer le temps d'un test (``monkeypatch``) pour vérifier le
refus si elle était refermée.
"""

from typing import Final

LOT_TRACKING_AVAILABLE: Final[bool] = True


def lot_tracking_available() -> bool:
    """Activation du suivi par lot possible (P1-b levée avec la clôture du Lot 3-H)."""
    return LOT_TRACKING_AVAILABLE
