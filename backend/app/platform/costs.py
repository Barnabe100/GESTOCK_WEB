"""Coûts internes réservés à ``catalog.article.cost_view`` (Lot 3-A, ADR-0039).

Prix d'achat, coût moyen pondéré (CMUP), valorisations au coût : ces champs ne doivent JAMAIS
atteindre un utilisateur qui n'a pas ``catalog.article.cost_view`` — y compris par un endpoint
secondaire. Plutôt que de compter sur chaque point d'API, les routeurs concernés utilisent une
classe de route qui retire ces champs de TOUTE réponse JSON (à toute profondeur) quand la
permission manque : le champ est absent, jamais remplacé par une valeur fictive.

Les permissions de la requête sont celles résolues par ``get_tenant_context`` (site sélectionné
compris) ; une route sans contexte tenant n'est pas concernée. Aucun test sur un nom de rôle.
"""

import json
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute

COST_VIEW = "catalog.article.cost_view"

# Champs de coût des réponses du catalogue, du stock et des inventaires.
CATALOG_COST_FIELDS = frozenset({"purchase_price", "purchase_price_before", "purchase_price_after"})
STOCK_COST_FIELDS = frozenset(
    {
        "average_cost",
        "average_cost_before",
        "average_cost_after",
        "stock_value",
        "unit_cost",
        # Lignes et totaux des documents de stock : quantité × coût.
        "amount",
        "total_amount",
    }
)
INVENTORY_COST_FIELDS = frozenset(
    {"unit_cost", "adjustment_value", "surplus_value", "shortage_value"}
)
# Journal d'audit : coûts dans les données des évènements (jamais les montants de paiement).
AUDIT_COST_FIELDS = frozenset(
    {
        "purchase_price",
        "average_cost",
        "average_cost_before",
        "average_cost_after",
        "stock_value",
        "unit_cost",
        "adjustment_value",
        "surplus_value",
        "shortage_value",
    }
)


def costs_hidden(request: Request) -> bool:
    """Vrai si la requête est faite dans un contexte tenant SANS ``cost_view``."""
    permissions = getattr(request.state, "permissions", None)
    return permissions is not None and COST_VIEW not in permissions


def strip_fields(value: Any, fields: frozenset[str]) -> Any:
    if isinstance(value, dict):
        return {k: strip_fields(v, fields) for k, v in value.items() if k not in fields}
    if isinstance(value, list):
        return [strip_fields(item, fields) for item in value]
    return value


def cost_masking_route(fields: frozenset[str]) -> type[APIRoute]:
    """Classe de route qui retire ``fields`` des réponses JSON sans ``cost_view``."""

    class CostMaskingRoute(APIRoute):
        def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
            original = super().get_route_handler()

            async def handler(request: Request) -> Response:
                response = await original(request)
                if not costs_hidden(request) or response.media_type != "application/json":
                    return response
                body = bytes(response.body)
                if not body:
                    return response
                content = strip_fields(json.loads(body), fields)
                headers = {
                    k: v for k, v in response.headers.items() if k.lower() != "content-length"
                }
                return Response(
                    content=json.dumps(content, ensure_ascii=False, separators=(",", ":")),
                    status_code=response.status_code,
                    headers=headers,
                    media_type="application/json",
                    background=response.background,
                )

            return handler

    return CostMaskingRoute
