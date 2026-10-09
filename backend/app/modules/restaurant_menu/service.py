"""Menu d'un site de restauration (palier R1, ADR-0049 ; ``RESTAURANT.md`` §4).

Le menu présente des produits du catalogue GLOBAL sans les dupliquer : un élément référence une
présentation (article en unité de base ou conditionnement de l'article). Contrôles du serveur à
la création et à la réactivation d'un élément (interface publique du catalogue seulement) :
article du tenant et actif, dans l'assortiment ACTIF du site, conditionnement de l'article,
actif et avec un prix configuré. Ensuite, l'état « commandable » est recalculé à chaque lecture
depuis l'état COURANT du catalogue et de l'assortiment (motifs dans ``blockers``) : le menu ne
bloque jamais le catalogue (seules les commandes ouvertes le feront, palier R2).

Portée : ``sites.py`` (lecture limitée aux sites où le menu est effectif pour le membre ;
écriture revérifiée pour l'abonnement et les modules du site visé). Isolation entre
entreprises : RLS + FK composites. Unicités (nom de section par site, présentation par site)
garanties par la base : une création concurrente donne un ``409``. Aucun prix par site, aucun
coût exposé ; aucune suppression (désactivation). Ne valide pas la transaction (ADR-0008).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.modules.catalog.api import (
    PackagingRef,
    active_packagings,
    articles_view,
    assortment_view,
    barcode_search,
    check_packagings,
    ensure_in_assortment,
    get_article_refs,
)
from app.modules.restaurant_menu.models import (
    ITEM_PRESENTATION_CONSTRAINT,
    SECTION_NAME_INDEX,
    MenuItem,
    MenuSection,
)
from app.modules.restaurant_menu.schemas import ItemCreate, ItemUpdate
from app.modules.restaurant_menu.sites import ensure_row_site, operation_site, readable_site_ids
from app.platform.audit.service import audit_action
from app.platform.context import RequestContext
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, apply_sort, paginate_rows, search_filter, text_sort
from app.shared.schemas import StatusFilter

SECTION_NAME_TAKEN = ("menu_section_name_taken", "Une section de ce nom existe déjà sur ce site")
ITEM_EXISTS = ("menu_item_exists", "Cette présentation figure déjà au menu de ce site")
SECTION_NOT_FOUND = "menu_section_not_found"
ITEM_NOT_FOUND = "menu_item_not_found"


class AvailabilityFilter(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    ALL = "all"


@dataclass(frozen=True)
class SectionRow:
    id: uuid.UUID
    site_id: uuid.UUID
    site_name: str
    name: str
    sort_order: int
    is_active: bool
    item_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ItemRow:
    id: uuid.UUID
    site_id: uuid.UUID
    site_name: str
    section_id: uuid.UUID
    section_name: str
    section_active: bool
    article_id: uuid.UUID
    reference: str
    designation: str
    unit: str
    packaging_id: uuid.UUID | None
    packaging_name: str | None
    conversion: Decimal | None
    display_name: str | None
    description: str | None
    sort_order: int
    is_active: bool
    available: bool
    unavailable_reason: str | None
    price: Decimal | None
    orderable: bool
    blockers: list[str]
    created_at: datetime
    updated_at: datetime


def _blockers(row: Any, packaging: PackagingRef | None) -> list[str]:
    """Motifs pour lesquels l'élément n'est pas commandable maintenant (ordre stable)."""
    blockers: list[str] = []
    if not row.is_active:
        blockers.append("item_inactive")
    if not row.section_active:
        blockers.append("section_inactive")
    if not row.available:
        blockers.append("unavailable")
    if not row.article_active:
        blockers.append("article_inactive")
    if not row.in_assortment:
        blockers.append("article_not_in_site_assortment")
    if row.packaging_id is not None:
        if packaging is None:
            blockers.append("packaging_inactive")
        elif packaging.sale_price is None:
            blockers.append("packaging_price_not_set")
    return blockers


class MenuService:
    def __init__(self, db: Session, ctx: RequestContext) -> None:
        self.db = db
        self.ctx = ctx

    # --- Commun -------------------------------------------------------------------------------

    def _flush(self) -> None:
        """Les unicités font foi en base : une création concurrente échoue ici (``409``)."""
        try:
            self.db.flush()
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == SECTION_NAME_INDEX:
                raise ConflictError(SECTION_NAME_TAKEN[1], code=SECTION_NAME_TAKEN[0]) from exc
            if constraint == ITEM_PRESENTATION_CONSTRAINT:
                raise ConflictError(ITEM_EXISTS[1], code=ITEM_EXISTS[0]) from exc
            raise

    def _audit(self, action: str, entity_type: str, entity: Any, data: dict[str, Any]) -> None:
        audit_action(
            self.db,
            self.ctx,
            f"restaurant_menu.{action}",
            entity_type=entity_type,
            entity_id=entity.id,
            site_id=entity.site_id,
            data=data,
        )

    # --- Sections : lecture ---------------------------------------------------------------------

    def _section_query(self) -> Select[Any]:
        counts = (
            select(MenuItem.section_id, func.count().label("item_count"))
            .group_by(MenuItem.section_id)
            .subquery("menu_item_counts")
        )
        return (
            select(
                MenuSection.id,
                MenuSection.site_id,
                Site.name.label("site_name"),
                MenuSection.name,
                MenuSection.sort_order,
                MenuSection.is_active,
                func.coalesce(counts.c.item_count, 0).label("item_count"),
                MenuSection.created_at,
                MenuSection.updated_at,
            )
            .join(
                Site, (Site.tenant_id == MenuSection.tenant_id) & (Site.id == MenuSection.site_id)
            )
            .outerjoin(counts, counts.c.section_id == MenuSection.id)
            .where(MenuSection.tenant_id == self.ctx.tenant_id)
        )

    def search_sections(
        self,
        params: PageParams,
        search: str | None,
        status: StatusFilter,
        site_id: uuid.UUID | None,
    ) -> tuple[list[SectionRow], int]:
        stmt = self._section_query().where(
            MenuSection.site_id.in_(readable_site_ids(self.ctx, site_id))
        )
        condition = search_filter(search, MenuSection.name)
        if condition is not None:
            stmt = stmt.where(condition)
        if status is not StatusFilter.ALL:
            stmt = stmt.where(MenuSection.is_active.is_(status is StatusFilter.ACTIVE))
        if params.sort in (None, "position"):
            stmt = stmt.order_by(
                MenuSection.sort_order, text_sort(MenuSection.name), MenuSection.id
            )
        else:
            sortable = {
                "position": MenuSection.sort_order,
                "name": text_sort(MenuSection.name),
                "site": text_sort(Site.name),
                "created_at": MenuSection.created_at,
            }
            stmt = apply_sort(stmt, params.sort, sortable, "position", MenuSection.id)
        rows, total = paginate_rows(self.db, stmt, params)
        return [SectionRow(**row._mapping) for row in rows], total

    def section_row(self, section_id: uuid.UUID) -> SectionRow:
        row = self.db.execute(self._section_query().where(MenuSection.id == section_id)).one()
        return SectionRow(**row._mapping)

    def _get_section(self, section_id: uuid.UUID, *, lock: bool = False) -> MenuSection:
        """Section d'un site lisible par le membre ; sinon introuvable (jamais révélée)."""
        stmt = select(MenuSection).where(MenuSection.id == section_id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        section = self.db.scalars(stmt).one_or_none()
        if section is None:
            raise NotFoundError("Section introuvable", code=SECTION_NOT_FOUND)
        ensure_row_site(self.ctx, section.site_id, SECTION_NOT_FOUND)
        return section

    def get_section(self, section_id: uuid.UUID) -> SectionRow:
        section = self._get_section(section_id)
        return self.section_row(section.id)

    # --- Sections : écriture --------------------------------------------------------------------

    def _ensure_name_free(
        self, site_id: uuid.UUID, name: str, exclude: uuid.UUID | None = None
    ) -> None:
        stmt = select(MenuSection.id).where(
            MenuSection.site_id == site_id, func.lower(MenuSection.name) == name.lower()
        )
        if exclude is not None:
            stmt = stmt.where(MenuSection.id != exclude)
        if self.db.scalar(stmt) is not None:
            raise ConflictError(SECTION_NAME_TAKEN[1], code=SECTION_NAME_TAKEN[0])

    def create_section(self, site_id: uuid.UUID | None, name: str, sort_order: int) -> MenuSection:
        site = operation_site(self.ctx, site_id)
        self._ensure_name_free(site, name)
        section = MenuSection(
            tenant_id=self.ctx.tenant_id, site_id=site, name=name, sort_order=sort_order
        )
        self.db.add(section)
        self._flush()
        self._audit(
            "section_created", "menu_section", section, {"name": name, "sort_order": sort_order}
        )
        return section

    def update_section(self, section_id: uuid.UUID, name: str, sort_order: int) -> MenuSection:
        section = self._get_section(section_id, lock=True)
        before = {"name": section.name, "sort_order": section.sort_order}
        after = {"name": name, "sort_order": sort_order}
        if before == after:
            return section
        if section.name != name:
            self._ensure_name_free(section.site_id, name, exclude=section.id)
        section.name = name
        section.sort_order = sort_order
        self._flush()
        self._audit("section_updated", "menu_section", section, {"before": before, "after": after})
        return section

    def set_section_active(self, section_id: uuid.UUID, active: bool) -> MenuSection:
        """Section désactivée : ses éléments restent au menu mais ne sont plus commandables."""
        section = self._get_section(section_id, lock=True)
        if section.is_active != active:
            section.is_active = active
            self._flush()
            self._audit(
                "section_activated" if active else "section_deactivated",
                "menu_section",
                section,
                {"name": section.name},
            )
        return section

    # --- Éléments : lecture ---------------------------------------------------------------------

    def _item_query(self) -> Select[Any]:
        articles = articles_view()
        assortment = assortment_view()
        return (
            select(
                MenuItem.id,
                MenuItem.site_id,
                Site.name.label("site_name"),
                MenuItem.section_id,
                MenuSection.name.label("section_name"),
                MenuSection.is_active.label("section_active"),
                MenuItem.article_id,
                articles.c.reference,
                articles.c.designation,
                articles.c.unit,
                articles.c.is_active.label("article_active"),
                assortment.c.article_id.is_not(None).label("in_assortment"),
                MenuItem.packaging_id,
                MenuItem.display_name,
                MenuItem.description,
                MenuItem.sort_order,
                MenuItem.is_active,
                MenuItem.available,
                MenuItem.unavailable_reason,
                MenuItem.created_at,
                MenuItem.updated_at,
            )
            .join(Site, (Site.tenant_id == MenuItem.tenant_id) & (Site.id == MenuItem.site_id))
            .join(
                MenuSection,
                (MenuSection.tenant_id == MenuItem.tenant_id)
                & (MenuSection.id == MenuItem.section_id),
            )
            .join(
                articles,
                (articles.c.tenant_id == MenuItem.tenant_id)
                & (articles.c.id == MenuItem.article_id),
            )
            .outerjoin(
                assortment,
                (assortment.c.tenant_id == MenuItem.tenant_id)
                & (assortment.c.site_id == MenuItem.site_id)
                & (assortment.c.article_id == MenuItem.article_id),
            )
            .where(MenuItem.tenant_id == self.ctx.tenant_id)
        )

    def _item_rows(self, rows: list[Any]) -> list[ItemRow]:
        """Lignes enrichies de l'état COURANT du catalogue (prix, conditionnement) — lecture
        seule, sans verrou ; un conditionnement désactivé n'est pas exposé par le catalogue."""
        article_ids = {row.article_id for row in rows}
        refs = get_article_refs(self.db, article_ids)
        packagings = {
            p.id: p
            for items in active_packagings(self.db, article_ids, priced_only=False).values()
            for p in items
        }
        result: list[ItemRow] = []
        for row in rows:
            packaging = packagings.get(row.packaging_id) if row.packaging_id else None
            if row.packaging_id is None:
                price: Decimal | None = refs[row.article_id].sale_price
            else:
                price = packaging.sale_price if packaging is not None else None
            blockers = _blockers(row, packaging)
            result.append(
                ItemRow(
                    id=row.id,
                    site_id=row.site_id,
                    site_name=row.site_name,
                    section_id=row.section_id,
                    section_name=row.section_name,
                    section_active=row.section_active,
                    article_id=row.article_id,
                    reference=row.reference,
                    designation=row.designation,
                    unit=row.unit,
                    packaging_id=row.packaging_id,
                    packaging_name=packaging.name if packaging is not None else None,
                    conversion=packaging.conversion if packaging is not None else None,
                    display_name=row.display_name,
                    description=row.description,
                    sort_order=row.sort_order,
                    is_active=row.is_active,
                    available=row.available,
                    unavailable_reason=row.unavailable_reason,
                    price=price,
                    orderable=not blockers,
                    blockers=blockers,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
            )
        return result

    def search_items(
        self,
        params: PageParams,
        *,
        search: str | None,
        status: StatusFilter,
        availability: AvailabilityFilter,
        site_id: uuid.UUID | None,
        section_id: uuid.UUID | None,
    ) -> tuple[list[ItemRow], int]:
        stmt = self._item_query().where(MenuItem.site_id.in_(readable_site_ids(self.ctx, site_id)))
        articles = stmt.selected_columns
        condition = search_filter(
            search, MenuItem.display_name, articles.reference, articles.designation
        )
        condition = barcode_search(search, condition, MenuItem.article_id)
        if condition is not None:
            stmt = stmt.where(condition)
        if section_id is not None:
            stmt = stmt.where(MenuItem.section_id == section_id)
        if status is not StatusFilter.ALL:
            stmt = stmt.where(MenuItem.is_active.is_(status is StatusFilter.ACTIVE))
        if availability is not AvailabilityFilter.ALL:
            stmt = stmt.where(MenuItem.available.is_(availability is AvailabilityFilter.AVAILABLE))
        label = func.coalesce(MenuItem.display_name, articles.designation)
        if params.sort in (None, "position"):
            stmt = stmt.order_by(
                MenuSection.sort_order,
                text_sort(MenuSection.name),
                MenuSection.id,
                MenuItem.sort_order,
                text_sort(label),
                MenuItem.id,
            )
        else:
            sortable = {
                "position": MenuItem.sort_order,
                "name": text_sort(label),
                "reference": text_sort(articles.reference),
                "section": text_sort(MenuSection.name),
                "created_at": MenuItem.created_at,
            }
            stmt = apply_sort(stmt, params.sort, sortable, "position", MenuItem.id)
        rows, total = paginate_rows(self.db, stmt, params)
        return self._item_rows(rows), total

    def item_row(self, item_id: uuid.UUID) -> ItemRow:
        row = self.db.execute(self._item_query().where(MenuItem.id == item_id)).one()
        return self._item_rows([row])[0]

    def _get_item(self, item_id: uuid.UUID, *, lock: bool = False) -> MenuItem:
        """Élément d'un site lisible par le membre ; sinon introuvable (jamais révélé)."""
        stmt = select(MenuItem).where(MenuItem.id == item_id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        item = self.db.scalars(stmt).one_or_none()
        if item is None:
            raise NotFoundError("Élément de menu introuvable", code=ITEM_NOT_FOUND)
        ensure_row_site(self.ctx, item.site_id, ITEM_NOT_FOUND)
        return item

    def get_item(self, item_id: uuid.UUID) -> ItemRow:
        item = self._get_item(item_id)
        return self.item_row(item.id)

    # --- Éléments : écriture --------------------------------------------------------------------

    def _section_of_site(self, section_id: uuid.UUID, site_id: uuid.UUID) -> MenuSection:
        """Section active du MÊME site (la FK composite le garantit aussi en base)."""
        section = self.db.scalars(
            select(MenuSection).where(MenuSection.id == section_id)
        ).one_or_none()
        if section is None or section.site_id != site_id:
            raise NotFoundError("Section introuvable", code=SECTION_NOT_FOUND)
        if not section.is_active:
            raise BusinessRuleError("Cette section est désactivée", code="menu_section_inactive")
        return section

    def _check_presentation(
        self, site_id: uuid.UUID, article_id: uuid.UUID, packaging_id: uuid.UUID | None
    ) -> str:
        """Présentation commandable sur ce site, contrôlée par le serveur (catalogue public) :
        article du tenant et actif, dans l'assortiment ACTIF du site, conditionnement de
        l'article, actif et avec un prix configuré. Renvoie la référence de l'article."""
        ref = get_article_refs(self.db, {article_id}).get(article_id)
        if ref is None:
            raise NotFoundError("Article introuvable", code="article_not_found")
        if not ref.is_active:
            raise BusinessRuleError(
                "Article désactivé", code="article_inactive", extra={"articles": [ref.reference]}
            )
        ensure_in_assortment(self.db, site_id, {article_id})
        if packaging_id is not None:
            packaging = check_packagings(self.db, [(article_id, packaging_id)])[packaging_id]
            if packaging.sale_price is None:
                raise BusinessRuleError(
                    "Prix du conditionnement non configuré : il ne peut pas être vendu",
                    code="packaging_price_not_set",
                    extra={"packagings": [packaging.name]},
                )
        return ref.reference

    def create_item(self, data: ItemCreate) -> MenuItem:
        site = operation_site(self.ctx, data.site_id)
        self._section_of_site(data.section_id, site)
        reference = self._check_presentation(site, data.article_id, data.packaging_id)
        existing = self.db.scalar(
            select(MenuItem.id).where(
                MenuItem.site_id == site,
                MenuItem.article_id == data.article_id,
                MenuItem.packaging_id.is_(None)
                if data.packaging_id is None
                else MenuItem.packaging_id == data.packaging_id,
            )
        )
        if existing is not None:
            raise ConflictError(ITEM_EXISTS[1], code=ITEM_EXISTS[0], extra={"id": str(existing)})
        item = MenuItem(
            tenant_id=self.ctx.tenant_id,
            site_id=site,
            section_id=data.section_id,
            article_id=data.article_id,
            packaging_id=data.packaging_id,
            display_name=data.display_name,
            description=data.description,
            sort_order=data.sort_order,
        )
        self.db.add(item)
        self._flush()
        self._audit(
            "item_created",
            "menu_item",
            item,
            {
                "reference": reference,
                "packaging_id": str(data.packaging_id) if data.packaging_id else None,
                "section_id": str(data.section_id),
                "display_name": data.display_name,
            },
        )
        return item

    def update_item(self, item_id: uuid.UUID, data: ItemUpdate) -> MenuItem:
        item = self._get_item(item_id, lock=True)
        before = {
            "section_id": str(item.section_id),
            "display_name": item.display_name,
            "description": item.description,
            "sort_order": item.sort_order,
        }
        after = {
            "section_id": str(data.section_id),
            "display_name": data.display_name,
            "description": data.description,
            "sort_order": data.sort_order,
        }
        if before == after:
            return item
        if data.section_id != item.section_id:
            self._section_of_site(data.section_id, item.site_id)
        item.section_id = data.section_id
        item.display_name = data.display_name
        item.description = data.description
        item.sort_order = data.sort_order
        self._flush()
        changed = {
            key: {"before": before[key], "after": after[key]}
            for key in before
            if before[key] != after[key]
        }
        self._audit("item_updated", "menu_item", item, changed)
        return item

    def set_item_active(self, item_id: uuid.UUID, active: bool) -> MenuItem:
        """Réactivation = même ligne, contrôles de la présentation refaits."""
        item = self._get_item(item_id, lock=True)
        if item.is_active == active:
            return item
        if active:
            self._check_presentation(item.site_id, item.article_id, item.packaging_id)
        item.is_active = active
        self._flush()
        self._audit(
            "item_activated" if active else "item_deactivated",
            "menu_item",
            item,
            {"article_id": str(item.article_id)},
        )
        return item

    def set_availability(self, item_id: uuid.UUID, available: bool, reason: str | None) -> MenuItem:
        """« Épuisé » manuel (et son motif facultatif) ; aucun effet sur le stock."""
        item = self._get_item(item_id, lock=True)
        new_reason = None if available else reason
        if item.available == available and item.unavailable_reason == new_reason:
            return item
        before = {"available": item.available, "reason": item.unavailable_reason}
        item.available = available
        item.unavailable_reason = new_reason
        self._flush()
        self._audit(
            "item_availability_changed",
            "menu_item",
            item,
            {"before": before, "after": {"available": available, "reason": new_reason}},
        )
        return item
