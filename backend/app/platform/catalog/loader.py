"""Chargement et validation du catalogue (fichiers TOML de ``data/``)."""

import fnmatch
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.platform.catalog.ux import (
    NAME_RE,
    QUALIFIED_RE,
    NavGroup,
    UxConfig,
    merge_ux,
    parse_navigation,
    validate_ux_parts,
)
from app.platform.registry import AccessKind, ModuleRegistry
from app.platform.subscriptions.models import SubscriptionStatus

DATA_DIR = Path(__file__).parent / "data"


class CatalogError(Exception):
    pass


@dataclass(frozen=True)
class SectorDef:
    """Secteur : classification (commerce, restauration…) ; aucune règle métier."""

    code: str
    name: str
    description: str | None
    sort_order: int
    icon: str | None
    is_active: bool


@dataclass(frozen=True)
class UxProfileDef:
    """Profil UX : présentation (navigation, tableau de bord, terminologie, thème) et modules
    proposés par défaut aux profils d'activité qui l'utilisent."""

    code: str
    name: str
    description: str | None
    is_active: bool
    modules: tuple[str, ...]
    optional_modules: tuple[str, ...]
    config: UxConfig


@dataclass(frozen=True)
class ProfileDef:
    """Profil d'activité (Business Profile) : activité précise d'un secteur. ``modules`` est
    déjà résolu (ceux du profil, sinon ceux de son profil UX) ; navigation, tableau de bord,
    terminologie et thème sont des **surcharges** de son profil UX (vides = hérités)."""

    code: str
    sector: str
    ux_profile: str
    name: str
    description: str | None
    sort_order: int
    is_active: bool
    modules: tuple[str, ...]
    optional_modules: tuple[str, ...]
    navigation: tuple[NavGroup, ...]
    dashboard: dict[str, Any]
    terminology: dict[str, Any]
    theme: dict[str, Any]
    settings: dict[str, Any]


@dataclass(frozen=True)
class PlanDef:
    code: str
    name: str
    description: str | None
    sort_order: int
    grace_days: int
    modules: tuple[str, ...]
    features: tuple[str, ...]
    limits: dict[str, int]


@dataclass(frozen=True)
class PolicyDef:
    status: str
    description: str | None
    allowed_access: tuple[str, ...]


@dataclass(frozen=True)
class RoleTemplate:
    code: str
    name: str
    description: str | None
    permission_patterns: tuple[str, ...]
    # Motifs retirés du résultat (ex. Consultant : ``*.view`` sauf ``users.*``).
    exclude_patterns: tuple[str, ...] = ()
    # Rôle protégé : ni désactivable, ni modifiable, ni renommable par le tenant.
    protected: bool = False

    def resolve(self, available_permissions: set[str]) -> list[str]:
        def matches(code: str, patterns: tuple[str, ...]) -> bool:
            return any(fnmatch.fnmatchcase(code, pattern) for pattern in patterns)

        return sorted(
            code
            for code in available_permissions
            if matches(code, self.permission_patterns) and not matches(code, self.exclude_patterns)
        )


@dataclass(frozen=True)
class Catalog:
    sectors: dict[str, SectorDef] = field(default_factory=dict)
    ux_profiles: dict[str, UxProfileDef] = field(default_factory=dict)
    profiles: dict[str, ProfileDef] = field(default_factory=dict)
    plans: dict[str, PlanDef] = field(default_factory=dict)
    policies: dict[str, PolicyDef] = field(default_factory=dict)
    role_templates: dict[str, RoleTemplate] = field(default_factory=dict)


def _read(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_catalog(registry: ModuleRegistry, data_dir: Path = DATA_DIR) -> Catalog:
    ux_profiles = _load_ux_profiles(data_dir / "ux_profiles")
    catalog = Catalog(
        sectors=_load_sectors(_read(data_dir / "sectors.toml")),
        ux_profiles=ux_profiles,
        profiles=_load_profiles(data_dir / "profiles", ux_profiles),
        plans=_load_plans(_read(data_dir / "plans.toml")),
        policies=_load_policies(_read(data_dir / "subscription_policies.toml")),
        role_templates=_load_role_templates(_read(data_dir / "role_templates.toml")),
    )
    validate_catalog(catalog, registry)
    return catalog


def _load_sectors(raw: dict[str, Any]) -> dict[str, SectorDef]:
    return {
        code: SectorDef(
            code=code,
            name=data["name"],
            description=data.get("description"),
            sort_order=int(data.get("sort_order", 0)),
            icon=data.get("icon"),
            is_active=bool(data.get("is_active", True)),
        )
        for code, data in raw.get("sectors", {}).items()
    }


def _dashboard(raw: dict[str, Any]) -> dict[str, Any]:
    return {key: list(value) for key, value in raw.get("dashboard", {}).items()}


def _load_ux_profiles(directory: Path) -> dict[str, UxProfileDef]:
    profiles: dict[str, UxProfileDef] = {}
    for path in sorted(directory.glob("*.toml")):
        raw = _read(path)
        code = raw["code"]
        if code != path.stem:
            raise CatalogError(
                f"{path.name} : le code '{code}' doit correspondre au nom du fichier"
            )
        dashboard = _dashboard(raw)
        profiles[code] = UxProfileDef(
            code=code,
            name=raw["name"],
            description=raw.get("description"),
            is_active=bool(raw.get("is_active", True)),
            modules=tuple(raw.get("modules", ())),
            optional_modules=tuple(raw.get("optional_modules", ())),
            config=UxConfig(
                navigation=parse_navigation(raw.get("navigation", [])),
                widgets=tuple(dashboard.get("widgets", ())),
                shortcuts=tuple(dashboard.get("shortcuts", ())),
                terminology=raw.get("terminology", {}),
                theme=raw.get("theme", {}),
            ),
        )
    return profiles


def _load_profiles(directory: Path, ux_profiles: dict[str, UxProfileDef]) -> dict[str, ProfileDef]:
    """Un fichier par profil : ``profiles/<secteur>/<activité>.toml``, code
    ``<secteur>.<activité>``. Les modules non déclarés sont ceux du profil UX."""
    stray = sorted(p.name for p in directory.glob("*.toml"))
    if stray:
        raise CatalogError(f"profils hors d'un dossier de secteur : {', '.join(stray)}")
    profiles: dict[str, ProfileDef] = {}
    for path in sorted(directory.glob("*/*.toml")):
        raw = _read(path)
        code = raw["code"]
        sector = raw["sector"]
        if code != f"{path.parent.name}.{path.stem}" or sector != path.parent.name:
            raise CatalogError(
                f"{path.parent.name}/{path.name} : code '{code}' et secteur '{sector}' doivent "
                "correspondre au chemin (<secteur>/<activité>.toml)"
            )
        ux = ux_profiles.get(raw["ux_profile"])
        profiles[code] = ProfileDef(
            code=code,
            sector=sector,
            ux_profile=raw["ux_profile"],
            name=raw["name"],
            description=raw.get("description"),
            sort_order=int(raw.get("sort_order", 0)),
            is_active=bool(raw.get("is_active", True)),
            modules=tuple(raw.get("modules", ux.modules if ux else ())),
            optional_modules=tuple(raw.get("optional_modules", ux.optional_modules if ux else ())),
            navigation=parse_navigation(raw.get("navigation", [])),
            dashboard=_dashboard(raw),
            terminology=raw.get("terminology", {}),
            theme=raw.get("theme", {}),
            settings=raw.get("settings", {}),
        )
    return profiles


def profile_ux(catalog: Catalog, profile: ProfileDef) -> UxConfig:
    """Configuration par défaut d'un profil (profil UX + surcharges), avant capacités."""
    return merge_ux(
        catalog.ux_profiles[profile.ux_profile].config,
        navigation=profile.navigation,
        dashboard=profile.dashboard,
        terminology=profile.terminology,
        theme=profile.theme,
    )


def _load_plans(raw: dict[str, Any]) -> dict[str, PlanDef]:
    return {
        code: PlanDef(
            code=code,
            name=data["name"],
            description=data.get("description"),
            sort_order=int(data.get("sort_order", 0)),
            grace_days=int(data.get("grace_days", 0)),
            modules=tuple(data.get("modules", ())),
            features=tuple(data.get("features", ())),
            limits={k: int(v) for k, v in data.get("limits", {}).items()},
        )
        for code, data in raw.get("plans", {}).items()
    }


def _load_policies(raw: dict[str, Any]) -> dict[str, PolicyDef]:
    return {
        status: PolicyDef(
            status=status,
            description=data.get("description"),
            allowed_access=tuple(data["allowed_access"]),
        )
        for status, data in raw.get("policies", {}).items()
    }


def _load_role_templates(raw: dict[str, Any]) -> dict[str, RoleTemplate]:
    return {
        code: RoleTemplate(
            code=code,
            name=data["name"],
            description=data.get("description"),
            permission_patterns=tuple(data.get("permissions", ())),
            exclude_patterns=tuple(data.get("exclude", ())),
            protected=bool(data.get("protected", False)),
        )
        for code, data in raw.get("roles", {}).items()
    }


def validate_catalog(catalog: Catalog, registry: ModuleRegistry) -> None:
    errors: list[str] = []
    core = registry.core_codes()

    known = {m.code for m in registry.all()}

    def check_modules(owner: str, modules: tuple[str, ...], optional: tuple[str, ...]) -> None:
        offered = {*modules, *optional}
        for code in offered:
            if code not in registry:
                errors.append(f"{owner} : module inconnu {code}")
            elif code in core:
                errors.append(f"{owner} : {code} est un module core (implicite)")
        for code in offered & {c for c in offered if c in registry}:
            missing = [d for d in registry.get(code).depends_on if d not in offered]
            if missing:
                errors.append(f"{owner} : {code} dépend de {missing} non proposés")

    for sector in catalog.sectors.values():
        if not NAME_RE.match(sector.code):
            errors.append(f"secteur {sector.code} : code invalide (minuscules, chiffres, _)")
        if sector.icon is not None and not sector.icon.startswith("pi pi-"):
            errors.append(f"secteur {sector.code} : icône invalide {sector.icon!r}")

    for ux in catalog.ux_profiles.values():
        owner = f"profil UX {ux.code}"
        if not QUALIFIED_RE.match(ux.code):
            errors.append(f"{owner} : code invalide (attendu <famille>.<variante>)")
        check_modules(owner, ux.modules, ux.optional_modules)
        errors.extend(
            validate_ux_parts(
                owner,
                known_modules=known,
                navigation=ux.config.navigation,
                dashboard={"widgets": ux.config.widgets, "shortcuts": ux.config.shortcuts},
                theme=ux.config.theme,
                terminology=ux.config.terminology,
            )
        )

    for profile in catalog.profiles.values():
        owner = f"profil {profile.code}"
        if not QUALIFIED_RE.match(profile.code):
            errors.append(f"{owner} : code invalide (attendu <secteur>.<activité>)")
        owner_sector = catalog.sectors.get(profile.sector)
        owner_ux = catalog.ux_profiles.get(profile.ux_profile)
        if owner_sector is None:
            errors.append(f"{owner} : secteur inconnu {profile.sector}")
        if owner_ux is None:
            errors.append(f"{owner} : profil UX inconnu {profile.ux_profile}")
        if profile.is_active and owner_sector is not None and not owner_sector.is_active:
            errors.append(f"{owner} : actif dans un secteur inactif {owner_sector.code}")
        if profile.is_active and owner_ux is not None and not owner_ux.is_active:
            errors.append(f"{owner} : actif avec un profil UX inactif {owner_ux.code}")
        check_modules(owner, profile.modules, profile.optional_modules)
        errors.extend(
            validate_ux_parts(
                owner,
                known_modules=known,
                navigation=profile.navigation,
                dashboard=profile.dashboard,
                theme=profile.theme,
                terminology=profile.terminology,
            )
        )

    for plan in catalog.plans.values():
        for code in plan.modules:
            if code not in registry:
                errors.append(f"plan {plan.code} : module inconnu {code}")
            elif code in core:
                errors.append(f"plan {plan.code} : {code} est un module core (implicite)")
        for key, value in plan.limits.items():
            if registry.limit(key) is None:
                errors.append(f"plan {plan.code} : limite inconnue {key}")
            elif value < 0:
                errors.append(f"plan {plan.code} : limite négative {key}")
        for feature in plan.features:
            module = registry.module_of_feature(feature)
            if module is None:
                errors.append(f"plan {plan.code} : fonctionnalité inconnue {feature}")
            elif module not in plan.modules and module not in core:
                errors.append(f"plan {plan.code} : {feature} exige le module {module}")

    statuses = {s.value for s in SubscriptionStatus}
    if set(catalog.policies) != statuses:
        errors.append(f"politiques d'abonnement : statuts attendus {sorted(statuses)}")
    kinds = {k.value for k in AccessKind}
    for policy in catalog.policies.values():
        unknown = set(policy.allowed_access) - kinds
        if unknown:
            errors.append(f"politique {policy.status} : natures inconnues {sorted(unknown)}")

    names = [t.name.strip().lower() for t in catalog.role_templates.values()]
    if len(names) != len(set(names)):
        errors.append("modèles de rôles : noms en double (casse ignorée)")
    if not any(t.protected for t in catalog.role_templates.values()):
        errors.append("modèles de rôles : aucun rôle protégé (administration du tenant)")
    for template in catalog.role_templates.values():
        if not template.permission_patterns:
            errors.append(f"modèle de rôle {template.code} : aucune permission")

    if errors:
        raise CatalogError("Catalogue invalide :\n- " + "\n- ".join(errors))


@lru_cache
def role_templates() -> dict[str, RoleTemplate]:
    """Modèles de rôles système (lus une fois ; source : ``data/role_templates.toml``)."""
    return _load_role_templates(_read(DATA_DIR / "role_templates.toml"))
