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


def test_feature_permission_requires_a_declared_feature() -> None:
    with pytest.raises(RegistryError, match="fonctionnalité non déclarée"):
        ModuleRegistry(
            [
                ModuleManifest(
                    code="a",
                    permissions=(PermissionDef("a.x.view", AccessKind.READ, feature="a.extra"),),
                )
            ]
        )


def test_feature_permissions_are_available_only_with_the_feature() -> None:
    registry = get_registry()
    without = registry.available_permissions({"catalog", "stock"}, set())
    with_feature = registry.available_permissions({"catalog", "stock"}, {"stock.transfers"})
    assert "stock.entry.create" in without and "stock.transfer.create" not in without
    assert {c for c in with_feature if c.startswith("stock.transfer.")} == {
        "stock.transfer.view",
        "stock.transfer.create",
        "stock.transfer.update",
        "stock.transfer.validate",
        "stock.transfer.cancel",
    }


def test_resolve_dependencies_drops_modules_with_missing_dependencies() -> None:
    registry = get_registry()
    # pos exige sales, payments et cash_register.
    assert "pos" not in registry.resolve_dependencies({"pos", "sales", "payments", "catalog"})
    # sales exige catalog, stock et customers.
    full = {"pos", "sales", "payments", "cash_register", "catalog", "stock", "customers"}
    assert registry.resolve_dependencies(full) == full
    # La suppression se propage : sans catalog, stock, sales puis payments… tombent ;
    # customers (sans dépendance) reste.
    assert registry.resolve_dependencies(full - {"catalog"}) == {"customers"}


def test_route_prefix_defaults_to_code_and_must_be_unique() -> None:
    from fastapi import APIRouter

    assert ModuleManifest(code="restaurant.menu").url_prefix == "/restaurant/menu"
    inventory = get_registry().get("inventory_count")
    assert inventory.url_prefix == "/inventories"  # code inchangé, URL lisible
    with pytest.raises(RegistryError, match="préfixe"):
        ModuleRegistry(
            [
                ModuleManifest(code="a", router=APIRouter(), route_prefix="/shared"),
                ModuleManifest(code="b", router=APIRouter(), route_prefix="/shared"),
            ]
        )


def test_extra_routers_mount_sub_resources_of_another_module() -> None:
    from fastapi import APIRouter

    receivables = get_registry().get("receivables")
    assert receivables.url_prefix == "/receivables"
    assert [prefix for prefix, _ in receivables.extra_routers] == ["/customers"]
    assert receivables.depends_on == ("sales", "customers", "stock")
    for invalid in ("customers", "/customers/"):
        with pytest.raises(RegistryError, match="préfixe"):
            ModuleRegistry(
                [
                    ModuleManifest(
                        code="a", router=APIRouter(), extra_routers=((invalid, APIRouter()),)
                    )
                ]
            )
