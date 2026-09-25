"""Registre des profils d'activité : accès unique aux secteurs, profils et profils UX
(synchronisés en base depuis ``catalog/data``), et résolution de l'expérience.

- **Configuration par défaut** d'un profil = son profil UX + ses surcharges (ce que le profil
  propose) ;
- **Expérience effective** d'un tenant = cette configuration restreinte aux modules
  **effectifs et implémentés** (profil ∩ plan ∩ activations, dépendances comprises) ; les
  modules seulement planifiés du profil sont signalés « à venir », jamais proposés comme
  utilisables. Les permissions, le plan et l'abonnement restent appliqués par
  ``CapabilityService`` et par chaque endpoint : ces données ne sont jamais une frontière de
  sécurité (ADR-0024).

Aucune règle ne dépend du code d'un secteur ou d'un profil : tout est porté par les données.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.platform.catalog.models import BusinessProfile, BusinessSector, UxProfile
from app.platform.catalog.ux import NavGroup, UxConfig, merge_ux, parse_navigation, ref_module
from app.platform.registry import ModuleRegistry, ModuleStatus


@dataclass(frozen=True)
class EffectiveExperience:
    ux_profile: str | None
    navigation: tuple[NavGroup, ...]
    # Ordre plat des modules (compatibilité : champ ``navigation`` des capacités).
    module_order: tuple[str, ...]
    widgets: tuple[str, ...]
    shortcuts: tuple[str, ...]
    terminology: dict[str, Any] = field(default_factory=dict)
    theme: dict[str, Any] = field(default_factory=dict)
    # Modules du profil seulement planifiés (non implémentés) : information, jamais un accès.
    upcoming: tuple[str, ...] = ()


def ux_config(ux: UxProfile | None) -> UxConfig:
    if ux is None:
        return UxConfig()
    return UxConfig(
        navigation=parse_navigation(ux.navigation),
        widgets=tuple(ux.dashboard.get("widgets", ())),
        shortcuts=tuple(ux.dashboard.get("shortcuts", ())),
        terminology=ux.terminology,
        theme=ux.theme,
    )


class BusinessProfileRegistry:
    def __init__(self, session: Session, modules: ModuleRegistry) -> None:
        self.session = session
        self.modules = modules

    # --- Lecture ------------------------------------------------------------------------

    def find(self, code: str) -> BusinessProfile | None:
        return self.session.get(BusinessProfile, code)

    def exists(self, code: str, *, active_only: bool = True) -> bool:
        profile = self.find(code)
        return profile is not None and (profile.is_active or not active_only)

    def get(self, code: str) -> BusinessProfile:
        profile = self.find(code)
        if profile is None:
            raise NotFoundError(f"Profil inconnu : {code}", code="unknown_profile")
        return profile

    def list_profiles(self, *, active_only: bool = True) -> list[BusinessProfile]:
        """Profils classés par secteur (ordre du secteur), puis par ordre du profil."""
        query = select(BusinessProfile).outerjoin(BusinessProfile.sector)
        if active_only:
            query = query.where(BusinessProfile.is_active.is_(True))
        query = query.order_by(
            BusinessSector.sort_order, BusinessProfile.sort_order, BusinessProfile.code
        )
        return list(self.session.scalars(query).unique())

    def list_sectors(self, *, active_only: bool = True) -> list[BusinessSector]:
        query = select(BusinessSector)
        if active_only:
            query = query.where(BusinessSector.is_active.is_(True))
        return list(self.session.scalars(query.order_by(BusinessSector.sort_order)))

    # --- Résolution ---------------------------------------------------------------------

    def default_ux(self, profile: BusinessProfile) -> UxConfig:
        """Configuration proposée par le profil : profil UX + surcharges du profil."""
        return merge_ux(
            ux_config(profile.ux_profile),
            navigation=parse_navigation(profile.navigation),
            dashboard=profile.dashboard,
            terminology=profile.terminology,
            theme=profile.theme,
        )

    def _status(self, code: str) -> ModuleStatus | None:
        return self.modules.get(code).status if code in self.modules else None

    def upcoming(self, profile: BusinessProfile) -> tuple[str, ...]:
        """Modules planifiés proposés par le profil, dans l'ordre de sa navigation."""
        order = [m for g in self.default_ux(profile).navigation for m in g.modules]
        planned = {
            link.module_code
            for link in profile.modules
            if self._status(link.module_code) == ModuleStatus.PLANNED
        }
        return tuple(
            sorted(planned, key=lambda c: (order.index(c) if c in order else len(order), c))
        )

    def effective(
        self, profile: BusinessProfile, effective_modules: Iterable[str]
    ) -> EffectiveExperience:
        """Expérience d'un tenant : seules les entrées des modules effectifs et implémentés
        sont conservées (rubriques vides retirées)."""
        config = self.default_ux(profile)
        usable = {c for c in effective_modules if self._status(c) == ModuleStatus.AVAILABLE}
        navigation = tuple(
            NavGroup(group=g.group, modules=tuple(m for m in g.modules if m in usable))
            for g in config.navigation
        )
        navigation = tuple(g for g in navigation if g.modules)
        order = [m for g in navigation for m in g.modules]
        order += sorted(self.modules.core_codes() - set(order))
        return EffectiveExperience(
            ux_profile=profile.ux_profile_code,
            navigation=navigation,
            module_order=tuple(order),
            widgets=tuple(r for r in config.widgets if ref_module(r) in usable),
            shortcuts=tuple(r for r in config.shortcuts if ref_module(r) in usable),
            terminology=config.terminology,
            theme=config.theme,
            upcoming=self.upcoming(profile),
        )
