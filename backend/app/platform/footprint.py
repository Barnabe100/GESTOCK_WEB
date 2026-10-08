"""Empreinte d'un module sur un site (palier D, changement de profil d'un site).

Chaque module métier peut déclarer dans son manifeste ``site_footprint(session, site_id,
lock)`` : une fonction en **lecture seule** qui compte, dans SES propres tables, ce que le site
contient. La plateforme s'en sert pour qualifier un changement de profil (SIMPLE / STRONG /
BLOCKED) et pour refuser la désactivation d'un module dont une opération en cours deviendrait
impossible, sans jamais importer les modèles d'un module (règle d'architecture 10).

- ``history`` : données commerciales enregistrées (ventes validées, mouvements…) → STRONG ;
- ``open`` : documents ouverts portés par le module (brouillons…) → STRONG ;
- ``active`` : travail en cours qui deviendrait **impossible** si le module cessait d'être
  effectif sur le site (session de caisse ouverte…) → BLOCKED dans ce seul cas ; sinon affiché,
  sans élever le niveau à lui seul ;
- ``info`` : configuration du site (assortiment, postes de caisse…), affichée seulement.

``lock`` : appelé sous le verrou du site par une reconfiguration (changement de profil,
désactivation d'un module), le module verrouille ce qui empêche un nouveau travail ``active``
de commencer (ex. réglage de caisse du site, que l'ouverture d'une session prend en partage).
Aucune écriture, jamais.
"""

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.platform.registry import ModuleRegistry

# Comptages plafonnés : l'aperçu affiche « 10 000+ » au-delà, sans parcourir tout l'historique.
COUNT_CAP = 10_000


@dataclass(frozen=True)
class SiteFootprint:
    history: Mapping[str, int] = field(default_factory=dict)
    open: Mapping[str, int] = field(default_factory=dict)
    active: Mapping[str, int] = field(default_factory=dict)
    info: Mapping[str, int] = field(default_factory=dict)

    @classmethod
    def of(
        cls,
        *,
        history: Mapping[str, int] | None = None,
        open: Mapping[str, int] | None = None,
        active: Mapping[str, int] | None = None,
        info: Mapping[str, int] | None = None,
    ) -> "SiteFootprint":
        """Seules les valeurs non nulles sont conservées."""

        def keep(values: Mapping[str, int] | None) -> Mapping[str, int]:
            return {k: v for k, v in (values or {}).items() if v}

        return cls(keep(history), keep(open), keep(active), keep(info))


SiteFootprintFn = Callable[[Session, uuid.UUID, bool], SiteFootprint]


def capped_count(session: Session, stmt: Select[Any]) -> int:
    """Nombre de lignes de ``stmt``, plafonné à ``COUNT_CAP + 1`` (« au-delà du plafond »)."""
    limited = stmt.limit(COUNT_CAP + 1).subquery()
    return int(session.scalar(select(func.count()).select_from(limited)) or 0)


def module_footprint(
    registry: "ModuleRegistry",
    session: Session,
    code: str,
    site_id: uuid.UUID,
    *,
    lock: bool = False,
) -> SiteFootprint:
    """Empreinte d'un module sur un site (vide si le module n'en déclare pas)."""
    if code not in registry:
        return SiteFootprint()
    fn = registry.get(code).site_footprint
    return fn(session, site_id, lock) if fn is not None else SiteFootprint()
