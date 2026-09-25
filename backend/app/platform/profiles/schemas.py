from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.platform.catalog.ux import NavGroup, UxConfig
from app.platform.profiles.registry import EffectiveExperience


class SectorInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    icon: str | None


class SectorOut(SectorInfo):
    description: str | None
    sort_order: int


class NavGroupOut(BaseModel):
    group: str
    modules: list[str]

    @classmethod
    def from_group(cls, group: NavGroup) -> "NavGroupOut":
        return cls(group=group.group, modules=list(group.modules))


class DashboardOut(BaseModel):
    widgets: list[str]
    shortcuts: list[str]


class ThemeOut(BaseModel):
    accent: str | None = None
    density: str | None = None
    icon: str | None = None


class UxOut(BaseModel):
    """Expérience effective (capacités) : seulement les modules effectifs et implémentés."""

    code: str | None
    navigation: list[NavGroupOut]
    dashboard: DashboardOut
    theme: ThemeOut
    # Modules planifiés du profil : affichés comme « à venir », jamais comme accessibles.
    upcoming: list[str]

    @classmethod
    def from_experience(cls, experience: EffectiveExperience) -> "UxOut":
        return cls(
            code=experience.ux_profile,
            navigation=[NavGroupOut.from_group(g) for g in experience.navigation],
            dashboard=DashboardOut(
                widgets=list(experience.widgets), shortcuts=list(experience.shortcuts)
            ),
            theme=ThemeOut(**experience.theme),
            upcoming=list(experience.upcoming),
        )


class DefaultUxOut(BaseModel):
    """Configuration **par défaut** d'un profil (profil UX + surcharges), avant plan,
    activations et permissions du tenant."""

    code: str | None
    navigation: list[NavGroupOut]
    dashboard: DashboardOut
    terminology: dict[str, Any]
    theme: ThemeOut

    @classmethod
    def from_config(cls, code: str | None, config: UxConfig) -> "DefaultUxOut":
        return cls(
            code=code,
            navigation=[NavGroupOut.from_group(g) for g in config.navigation],
            dashboard=DashboardOut(widgets=list(config.widgets), shortcuts=list(config.shortcuts)),
            terminology=config.terminology,
            theme=ThemeOut(**config.theme),
        )


class BusinessProfileOut(BaseModel):
    code: str
    name: str
    description: str | None
    sector: str | None
    ux_profile: str | None
    sort_order: int
    is_active: bool
    # Modules proposés : activés à la création du tenant / proposés désactivés.
    default_modules: list[str]
    optional_modules: list[str]


class BusinessProfileDetail(BusinessProfileOut):
    ux: DefaultUxOut
    upcoming: list[str]


class BusinessProfileCatalogOut(BaseModel):
    sectors: list[SectorOut]
    profiles: list[BusinessProfileOut]


class BusinessProfileChange(BaseModel):
    code: str = Field(min_length=1, max_length=50)
