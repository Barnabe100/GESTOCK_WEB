"""Port « retrait de l'assortiment » du module Catalogue (Recette, étape 1, ADR-0046, D4).

Retirer un article de l'assortiment d'un site ne doit laisser ni stock invisible ni document
impossible à valider : le retrait est refusé si l'article a, SUR CE SITE, un stock ou un solde de
lot non nul, ou figure dans un document ouvert (brouillon de vente, d'entrée, de sortie, de
transfert — site source ou destination —, inventaire non clôturé). Le catalogue ne dépend pas des
modules qui détiennent ces données : ils déclarent ici leurs contrôles au chargement de leur
manifeste (même principe que ``lot_flags_port``) — aucune dépendance inverse, aucun cycle.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session


class RemovalBlockerKind(StrEnum):
    STOCK = "stock"  # stock ou solde de lot non nul sur le site
    OPEN_DOCUMENT = "open_document"  # document ouvert du site contenant l'article


@dataclass(frozen=True)
class RemovalBlocker:
    kind: RemovalBlockerKind
    article_id: uuid.UUID
    document: str | None = None  # numéro (ou libellé) du document, pour le message


# (session, tenant, site, articles) → éléments bloquant le retrait.
RemovalCheck = Callable[[Session, uuid.UUID, uuid.UUID, set[uuid.UUID]], list[RemovalBlocker]]
_checks: dict[str, RemovalCheck] = {}


def register_assortment_removal_check(module: str, check: RemovalCheck) -> None:
    """Appelé par un module (Stock, Ventes, Inventaires…) à son chargement."""
    _checks[module] = check


def assortment_removal_blockers(
    db: Session, tenant_id: uuid.UUID, site_id: uuid.UUID, article_ids: set[uuid.UUID]
) -> list[RemovalBlocker]:
    """Éléments empêchant de retirer ces articles de l'assortiment du site (tous modules)."""
    if not article_ids:
        return []
    found: list[RemovalBlocker] = []
    for module in sorted(_checks):
        found.extend(_checks[module](db, tenant_id, site_id, article_ids))
    return found
