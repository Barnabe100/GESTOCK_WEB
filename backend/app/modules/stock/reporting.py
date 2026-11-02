"""Coût des sorties de stock d'un type de document (palier R10, ADR-0055), exposé par
``stock.api`` : la marge des rapports se calcule sur le coût RÉELLEMENT enregistré par les
mouvements (CMUP du site au moment de la sortie, ADR-0017 / ADR-0045), jamais sur un prix
d'achat de référence ni sur le CMUP courant.

Coût d'un mouvement de sortie = −quantité × ``unit_cost`` (quantité signée, négative). Les
annulations ne sont pas comptées : un document annulé est exclu par l'appelant (seules les
ventes VALIDÉES sont lues), si bien que ses mouvements d'origine ne comptent pas non plus.
"""

import uuid
from typing import Any

from sqlalchemy import func, select

from app.modules.stock.models import MovementType, StockMovement


def outflow_costs_query(tenant_id: uuid.UUID, source_type: str) -> Any:
    """Une ligne par (``source_id``, ``source_line_id``) : ``cost`` = somme des coûts des
    mouvements ``SALE`` du document (tous lots confondus). Un plat à recette (ADR-0053) porte
    les mouvements de ses ingrédients sous le même ``source_id`` (la vente) et une
    ``source_line_id`` propre à la consommation."""
    return (
        select(
            StockMovement.source_id.label("source_id"),
            StockMovement.source_line_id.label("source_line_id"),
            func.sum(-StockMovement.quantity * func.coalesce(StockMovement.unit_cost, 0)).label(
                "cost"
            ),
        )
        .where(
            StockMovement.tenant_id == tenant_id,
            StockMovement.source_type == source_type,
            StockMovement.movement_type == MovementType.SALE,
        )
        .group_by(StockMovement.source_id, StockMovement.source_line_id)
    )
