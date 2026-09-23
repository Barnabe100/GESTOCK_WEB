import pytest

from app.platform.registry import (
    AccessKind,
    ModuleManifest,
    ModuleRegistry,
    PermissionDef,
    RegistryError,
    get_registry,
)


def test_default_registry_is_valid() -> None:
    registry = get_registry()
    assert {"dashboard", "organization", "users", "audit", "subscription"} == registry.core_codes()
    assert registry.permission("users.member.manage") is not None


def test_unknown_dependency_is_rejected() -> None:
    with pytest.raises(RegistryError, match="inconnu"):
        ModuleRegistry([ModuleManifest(code="a", depends_on=("missing",))])


def test_cycle_is_rejected() -> None:
    with pytest.raises(RegistryError, match="circulaire"):
        ModuleRegistry(
            [
                ModuleManifest(code="a", depends_on=("b",)),
                ModuleManifest(code="b", depends_on=("a",)),
            ]
        )


def test_permission_must_be_prefixed_by_module() -> None:
    with pytest.raises(RegistryError, match="préfixée"):
        ModuleRegistry(
            [ModuleManifest(code="a", permissions=(PermissionDef("b.x.view", AccessKind.READ),))]
        )


def test_resolve_dependencies_drops_modules_with_missing_dependencies() -> None:
    registry = get_registry()
    # pos exige sales, payments et cash_register.
    assert "pos" not in registry.resolve_dependencies({"pos", "sales", "payments", "catalog"})
    full = {"pos", "sales", "payments", "cash_register", "catalog"}
    assert registry.resolve_dependencies(full) == full
    # La suppression se propage : sans catalog, sales puis payments… tombent.
    assert registry.resolve_dependencies(full - {"catalog"}) == set()
