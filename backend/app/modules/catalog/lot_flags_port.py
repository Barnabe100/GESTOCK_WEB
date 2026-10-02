"""Port « documents » du module Catalogue pour le changement de suivi par lot (Lot 3-H).

Modifier ``lot_tracked`` / ``expiry_tracked`` ne doit jamais rendre un document incohérent :
inventaire ouvert (mode figé au démarrage), brouillon portant des lots (réception, sortie,
transfert) ou, à l'ACTIVATION du suivi, document validé encore annulable dont les mouvements
n'ont pas de lot (son annulation exigerait un lot inconnu). Le catalogue ne dépend pas des
modules qui détiennent ces documents : ils déclarent ici leurs contrôles au chargement de leur
manifeste (même principe que ``stock_port``) — aucune dépendance inverse, aucun cycle.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session


class BlockerKind(StrEnum):
    OPEN_DOCUMENT = "open_document"  # document ouvert qui deviendrait incohérent
    UNTRACKED_HISTORY = "untracked_history"  # document validé annulable, mouvements sans lot


@dataclass(frozen=True)
class LotFlagsBlocker:
    kind: BlockerKind
    document: str  # numéro (ou libellé) du document, pour le message


# (session, tenant, article, activation du suivi par lot) → documents bloquants.
BlockerCheck = Callable[[Session, uuid.UUID, uuid.UUID, bool], list[LotFlagsBlocker]]
_checks: dict[str, BlockerCheck] = {}


def register_lot_flags_check(module: str, check: BlockerCheck) -> None:
    """Appelé par un module (Stock, Inventaires…) à son chargement."""
    _checks[module] = check


def lot_flags_blockers(
    db: Session, tenant_id: uuid.UUID, article_id: uuid.UUID, enabling: bool
) -> list[LotFlagsBlocker]:
    """Documents empêchant de modifier le suivi de l'article (tous modules confondus)."""
    found: list[LotFlagsBlocker] = []
    for module in sorted(_checks):
        found.extend(_checks[module](db, tenant_id, article_id, enabling))
    return found
