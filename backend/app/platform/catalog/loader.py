"""Chargement et validation du catalogue (fichiers TOML de ``data/``)."""

import fnmatch
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.platform.registry import AccessKind, ModuleRegistry
from app.platform.subscriptions.models import SubscriptionStatus

DATA_DIR = Path(__file__).parent / "data"


class CatalogError(Exception):
    pass


@dataclass(frozen=True)
class ProfileDef:
    code: str
    name: str
    description: str | None
    modules: tuple[str, ...]
    optional_modules: tuple[str, ...]
    navigation: tuple[str, ...]
    terminology: dict[str, Any]
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

    def resolve(self, available_permissions: set[str]) -> list[str]:
        return sorted(
            code
            for code in available_permissions
            if any(fnmatch.fnmatchcase(code, pattern) for pattern in self.permission_patterns)
        )


@dataclass(frozen=True)
class Catalog:
    profiles: dict[str, ProfileDef] = field(default_factory=dict)
    plans: dict[str, PlanDef] = field(default_factory=dict)
    policies: dict[str, PolicyDef] = field(default_factory=dict)
    role_templates: dict[str, RoleTemplate] = field(default_factory=dict)


def _read(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_catalog(registry: ModuleRegistry, data_dir: Path = DATA_DIR) -> Catalog:
    catalog = Catalog(
        profiles=_load_profiles(data_dir / "profiles"),
        plans=_load_plans(_read(data_dir / "plans.toml")),
        policies=_load_policies(_read(data_dir / "subscription_policies.toml")),
        role_templates=_load_role_templates(_read(data_dir / "role_templates.toml")),
    )
    validate_catalog(catalog, registry)
    return catalog


def _load_profiles(directory: Path) -> dict[str, ProfileDef]:
    profiles: dict[str, ProfileDef] = {}
    for path in sorted(directory.glob("*.toml")):
        raw = _read(path)
        code = raw["code"]
        if code != path.stem:
            raise CatalogError(
                f"{path.name} : le code '{code}' doit correspondre au nom du fichier"
            )
        profiles[code] = ProfileDef(
            code=code,
            name=raw["name"],
            description=raw.get("description"),
            modules=tuple(raw.get("modules", ())),
            optional_modules=tuple(raw.get("optional_modules", ())),
            navigation=tuple(raw.get("navigation", ())),
            terminology=raw.get("terminology", {}),
            settings=raw.get("settings", {}),
        )
    return profiles


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
        )
        for code, data in raw.get("roles", {}).items()
    }


def validate_catalog(catalog: Catalog, registry: ModuleRegistry) -> None:
    errors: list[str] = []
    core = registry.core_codes()

    for profile in catalog.profiles.values():
        offered = {*profile.modules, *profile.optional_modules}
        for code in offered:
            if code not in registry:
                errors.append(f"profil {profile.code} : module inconnu {code}")
            elif code in core:
                errors.append(f"profil {profile.code} : {code} est un module core (implicite)")
        for code in offered & {c for c in offered if c in registry}:
            missing = [d for d in registry.get(code).depends_on if d not in offered]
            if missing:
                errors.append(f"profil {profile.code} : {code} dépend de {missing} non proposés")
        for code in profile.navigation:
            if code not in offered and code not in core:
                errors.append(f"profil {profile.code} : navigation vers {code} non proposé")

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

    if errors:
        raise CatalogError("Catalogue invalide :\n- " + "\n- ".join(errors))
