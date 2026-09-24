"""Registre des modules.

Un module se déclare par un ``ModuleManifest`` : code, dépendances, permissions, et ce que les
plans commerciaux peuvent paramétrer — **limites** (quantités comptées par le module, ex.
``max_sites``) et **fonctionnalités** optionnelles (ex. ``stock.transfers``). Le registre est
construit explicitement (pas de découverte automatique) et validé au démarrage : dépendances
connues, absence de cycle, codes préfixés par le code du module (permissions, fonctionnalités),
aucun doublon.
"""

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter
    from sqlalchemy.orm import Session


class AccessKind(StrEnum):
    """Nature d'une permission. La politique d'abonnement autorise ou non chaque nature
    (ex. après expiration : lecture, export et facturation seulement)."""

    READ = "read"
    WRITE = "write"
    EXPORT = "export"
    ADMIN = "admin"
    BILLING = "billing"


class ModuleStatus(StrEnum):
    AVAILABLE = "available"  # implémenté et exposé
    PLANNED = "planned"  # déclaré (profils, plans) mais pas encore implémenté


@dataclass(frozen=True)
class PermissionDef:
    code: str
    access: AccessKind
    # Fonctionnalité de plan dont dépend la permission (ex. ``stock.transfers``) : sans elle,
    # la permission n'est ni accordée ni proposée à l'édition des rôles.
    feature: str | None = None


@dataclass(frozen=True)
class LimitDef:
    """Quantité plafonnable par un plan. ``counter`` compte l'usage courant du tenant actif
    (la session est déjà dans le contexte du tenant)."""

    code: str
    counter: "Callable[[Session], int]" = field(compare=False, hash=False)


@dataclass(frozen=True)
class ModuleManifest:
    code: str
    status: ModuleStatus = ModuleStatus.AVAILABLE
    # Un module « core » est toujours actif, quels que soient profil, plan et activations.
    core: bool = False
    depends_on: tuple[str, ...] = ()
    permissions: tuple[PermissionDef, ...] = field(default_factory=tuple)
    limits: tuple[LimitDef, ...] = field(default_factory=tuple)
    # Fonctionnalités optionnelles activables par plan (codes préfixés par le module).
    features: tuple[str, ...] = ()
    # Initialisation des données du module pour un nouveau tenant (appelée au provisioning,
    # dans le contexte RLS du tenant). Ex. : motifs de sortie système du module stock.
    tenant_setup: "Callable[[Session, uuid.UUID], None] | None" = field(
        default=None, compare=False, hash=False
    )
    # Routeur HTTP du module (modules métier). Monté sous /api/v1/<code> et protégé par
    # require_module(code) : un module inactif pour le tenant répond 403 côté serveur.
    router: "APIRouter | None" = field(default=None, compare=False, hash=False)


class RegistryError(Exception):
    pass


class ModuleRegistry:
    def __init__(self, manifests: Iterable[ModuleManifest]) -> None:
        self._modules: dict[str, ModuleManifest] = {}
        for manifest in manifests:
            if manifest.code in self._modules:
                raise RegistryError(f"module en double : {manifest.code}")
            self._modules[manifest.code] = manifest
        self._permissions: dict[str, tuple[ModuleManifest, PermissionDef]] = {}
        self._limits: dict[str, tuple[ModuleManifest, LimitDef]] = {}
        self._features: dict[str, ModuleManifest] = {}
        self._validate()

    def _validate(self) -> None:
        for manifest in self._modules.values():
            for dep in manifest.depends_on:
                if dep not in self._modules:
                    raise RegistryError(f"{manifest.code} dépend d'un module inconnu : {dep}")
            for perm in manifest.permissions:
                if not perm.code.startswith(f"{manifest.code}."):
                    raise RegistryError(f"{perm.code} doit être préfixée par {manifest.code}.")
                if perm.code in self._permissions:
                    raise RegistryError(f"permission en double : {perm.code}")
                self._permissions[perm.code] = (manifest, perm)
            for limit in manifest.limits:
                if limit.code in self._limits:
                    raise RegistryError(f"limite en double : {limit.code}")
                self._limits[limit.code] = (manifest, limit)
            for feature in manifest.features:
                if not feature.startswith(f"{manifest.code}."):
                    raise RegistryError(f"{feature} doit être préfixée par {manifest.code}.")
                if feature in self._features:
                    raise RegistryError(f"fonctionnalité en double : {feature}")
                self._features[feature] = manifest
            for perm in manifest.permissions:
                if perm.feature is not None and perm.feature not in manifest.features:
                    raise RegistryError(
                        f"{perm.code} dépend d'une fonctionnalité non déclarée : {perm.feature}"
                    )
        self._check_cycles()

    def _check_cycles(self) -> None:
        visiting: set[str] = set()
        done: set[str] = set()

        def visit(code: str, path: tuple[str, ...]) -> None:
            if code in done:
                return
            if code in visiting:
                raise RegistryError(f"dépendance circulaire : {' -> '.join((*path, code))}")
            visiting.add(code)
            for dep in self._modules[code].depends_on:
                visit(dep, (*path, code))
            visiting.discard(code)
            done.add(code)

        for code in self._modules:
            visit(code, ())

    def __contains__(self, code: object) -> bool:
        return code in self._modules

    def get(self, code: str) -> ModuleManifest:
        return self._modules[code]

    def all(self) -> list[ModuleManifest]:
        return list(self._modules.values())

    def core_codes(self) -> set[str]:
        return {m.code for m in self._modules.values() if m.core}

    def permission(self, code: str) -> PermissionDef | None:
        entry = self._permissions.get(code)
        return entry[1] if entry else None

    def module_of_permission(self, code: str) -> str | None:
        entry = self._permissions.get(code)
        return entry[0].code if entry else None

    def permissions_of(self, module_codes: Iterable[str]) -> dict[str, PermissionDef]:
        result: dict[str, PermissionDef] = {}
        for code in module_codes:
            for perm in self._modules[code].permissions:
                result[perm.code] = perm
        return result

    def available_permissions(
        self, module_codes: Iterable[str], features: Iterable[str]
    ) -> dict[str, PermissionDef]:
        """Permissions utilisables par un tenant : celles de ses modules effectifs, sauf celles
        d'une fonctionnalité que son plan n'inclut pas."""
        enabled = set(features)
        return {
            code: perm
            for code, perm in self.permissions_of(module_codes).items()
            if perm.feature is None or perm.feature in enabled
        }

    def limit(self, code: str) -> LimitDef | None:
        entry = self._limits.get(code)
        return entry[1] if entry else None

    def limit_codes(self) -> set[str]:
        return set(self._limits)

    def feature_codes(self) -> set[str]:
        return set(self._features)

    def module_of_feature(self, code: str) -> str | None:
        manifest = self._features.get(code)
        return manifest.code if manifest else None

    def resolve_dependencies(self, candidates: Iterable[str]) -> set[str]:
        """Retire itérativement les modules dont une dépendance n'est pas présente."""
        active = {c for c in candidates if c in self._modules}
        changed = True
        while changed:
            changed = False
            for code in list(active):
                if any(dep not in active for dep in self._modules[code].depends_on):
                    active.discard(code)
                    changed = True
        return active

    def dependents_of(self, code: str) -> set[str]:
        return {m.code for m in self._modules.values() if code in m.depends_on}


@lru_cache
def get_registry() -> ModuleRegistry:
    from app.modules import BUSINESS_MODULES
    from app.platform.manifests import PLATFORM_MODULES

    return ModuleRegistry([*PLATFORM_MODULES, *BUSINESS_MODULES])
