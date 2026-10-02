import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.stock.document_service import EntryService, ExitService
from app.modules.stock.level_service import (
    StateFilter,
    ThresholdService,
    get_level,
    list_levels,
)
from app.modules.stock.location_service import LocationService
from app.modules.stock.lot_service import (
    LotService,
    LotStateFilter,
    SettingsService,
    entries_with_lot,
)
from app.modules.stock.models import (
    DocumentStatus,
    EntryKind,
    MovementType,
    StockEntry,
    StockExit,
)
from app.modules.stock.movement_service import list_movements
from app.modules.stock.reason_service import ExitReasonService
from app.modules.stock.schemas import (
    CancelInput,
    EntryCreate,
    EntryInput,
    EntryOut,
    ExitCreate,
    ExitInput,
    ExitOut,
    ExitReasonInput,
    ExitReasonOut,
    LevelOut,
    LocationAssign,
    LocationCreate,
    LocationOut,
    LocationRename,
    LotBalanceOut,
    LotDetailOut,
    LotOut,
    MovementOut,
    StockSettingsInput,
    StockSettingsOut,
    SupplierArticleOut,
    SupplierSummaryOut,
    ThresholdInput,
)
from app.modules.stock.sites import filter_site_ids, operation_site
from app.modules.stock.stock_service import StockService
from app.modules.stock.supplier_view import SupplierReceptions
from app.modules.stock.transfer_router import router as transfer_router
from app.modules.suppliers.api import suppliers_named
from app.platform.context import (
    DbSession,
    NowDep,
    RequestContext,
    require_any_permission,
    require_permission,
)
from app.platform.costs import STOCK_COST_FIELDS, cost_masking_route
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page, StatusFilter

router = APIRouter(tags=["stock"], route_class=cost_masking_route(STOCK_COST_FIELDS))
router.include_router(transfer_router)


ReasonPick = Annotated[
    RequestContext,
    Depends(require_any_permission("stock.reason.view", "stock.exit.create", "stock.exit.update")),
]
ReasonManage = Annotated[RequestContext, Depends(require_permission("stock.reason.manage"))]
EntryView = Annotated[RequestContext, Depends(require_permission("stock.entry.view"))]
EntryCreateCtx = Annotated[RequestContext, Depends(require_permission("stock.entry.create"))]
EntryUpdate = Annotated[RequestContext, Depends(require_permission("stock.entry.update"))]
EntryValidate = Annotated[RequestContext, Depends(require_permission("stock.entry.validate"))]
EntryCancel = Annotated[RequestContext, Depends(require_permission("stock.entry.cancel"))]
ExitView = Annotated[RequestContext, Depends(require_permission("stock.exit.view"))]
ExitCreateCtx = Annotated[RequestContext, Depends(require_permission("stock.exit.create"))]
ExitUpdate = Annotated[RequestContext, Depends(require_permission("stock.exit.update"))]
ExitValidate = Annotated[RequestContext, Depends(require_permission("stock.exit.validate"))]
ExitCancel = Annotated[RequestContext, Depends(require_permission("stock.exit.cancel"))]
Paging = Annotated[PageParams, Depends(page_params)]
StatusParam = Annotated[StatusFilter, Query(alias="status")]
DocStatusParam = Annotated[DocumentStatus | None, Query(alias="status")]


# --- Motifs de sortie ------------------------------------------------------------------------


@router.get("/exit-reasons", response_model=Page[ExitReasonOut])
def list_exit_reasons(
    ctx: ReasonPick,
    db: DbSession,
    paging: Paging,
    search: str | None = None,
    status_filter: StatusParam = StatusFilter.ALL,
) -> Page[ExitReasonOut]:
    items, total = ExitReasonService(db, ctx).search(paging, search, status_filter)
    return Page(
        items=[ExitReasonOut.model_validate(r) for r in items],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


@router.post("/exit-reasons", response_model=ExitReasonOut, status_code=status.HTTP_201_CREATED)
def create_exit_reason(body: ExitReasonInput, ctx: ReasonManage, db: DbSession) -> ExitReasonOut:
    reason = ExitReasonService(db, ctx).create(body)
    db.commit()
    return ExitReasonOut.model_validate(reason)


@router.patch("/exit-reasons/{reason_id}", response_model=ExitReasonOut)
def update_exit_reason(
    reason_id: uuid.UUID, body: ExitReasonInput, ctx: ReasonManage, db: DbSession
) -> ExitReasonOut:
    reason = ExitReasonService(db, ctx).update(reason_id, body)
    db.commit()
    return ExitReasonOut.model_validate(reason)


@router.post("/exit-reasons/{reason_id}/activate", response_model=ExitReasonOut)
def activate_exit_reason(reason_id: uuid.UUID, ctx: ReasonManage, db: DbSession) -> ExitReasonOut:
    reason = ExitReasonService(db, ctx).set_active(reason_id, True)
    db.commit()
    return ExitReasonOut.model_validate(reason)


@router.post("/exit-reasons/{reason_id}/deactivate", response_model=ExitReasonOut)
def deactivate_exit_reason(reason_id: uuid.UUID, ctx: ReasonManage, db: DbSession) -> ExitReasonOut:
    reason = ExitReasonService(db, ctx).set_active(reason_id, False)
    db.commit()
    return ExitReasonOut.model_validate(reason)


# --- Entrées ----------------------------------------------------------------------------------


@router.get("/entries", response_model=Page[EntryOut])
def list_entries(
    ctx: EntryView,
    db: DbSession,
    now: NowDep,
    paging: Paging,
    search: str | None = None,
    status_filter: DocStatusParam = None,
    kind: EntryKind | None = None,
    supplier_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    lot_id: uuid.UUID | None = None,
) -> Page[EntryOut]:
    service = EntryService(db, ctx, now)
    extra = [
        StockEntry.kind == kind if kind else None,
        StockEntry.supplier_id == supplier_id if supplier_id else None,
        # Lot 3-G : réceptions d'un lot (fiche lot).
        StockEntry.id.in_(entries_with_lot(lot_id)) if lot_id else None,
    ]
    items, total = service.search(
        paging,
        search,
        status_filter,
        site_id,
        date_from,
        date_to,
        extra=[c for c in extra if c is not None],
        search_columns=[StockEntry.document_reference],
        # Lot 3-E : recherche aussi par nom du fournisseur (API publique de ``suppliers``).
        search_also=[StockEntry.supplier_id.in_(named)]
        if (named := suppliers_named(search)) is not None
        else [],
    )
    return Page(items=service.to_out(items), total=total, limit=paging.limit, offset=paging.offset)


@router.post("/entries", response_model=EntryOut, status_code=status.HTTP_201_CREATED)
def create_entry(body: EntryCreate, ctx: EntryCreateCtx, db: DbSession, now: NowDep) -> EntryOut:
    service = EntryService(db, ctx, now)
    entry = service.create(body)
    db.commit()
    return service.to_out([entry], with_lines=True)[0]


@router.get("/entries/{entry_id}", response_model=EntryOut)
def get_entry(entry_id: uuid.UUID, ctx: EntryView, db: DbSession, now: NowDep) -> EntryOut:
    service = EntryService(db, ctx, now)
    return service.to_out([service.get(entry_id)], with_lines=True)[0]


@router.put("/entries/{entry_id}", response_model=EntryOut)
def update_entry(
    entry_id: uuid.UUID, body: EntryInput, ctx: EntryUpdate, db: DbSession, now: NowDep
) -> EntryOut:
    service = EntryService(db, ctx, now)
    entry = service.update(entry_id, body)
    db.commit()
    return service.to_out([entry], with_lines=True)[0]


@router.post("/entries/{entry_id}/validate", response_model=EntryOut)
def validate_entry(entry_id: uuid.UUID, ctx: EntryValidate, db: DbSession, now: NowDep) -> EntryOut:
    service = EntryService(db, ctx, now)
    entry = service.validate(entry_id)
    db.commit()
    return service.to_out([entry], with_lines=True)[0]


@router.post("/entries/{entry_id}/cancel", response_model=EntryOut)
def cancel_entry(
    entry_id: uuid.UUID, body: CancelInput, ctx: EntryCancel, db: DbSession, now: NowDep
) -> EntryOut:
    service = EntryService(db, ctx, now)
    entry = service.cancel(entry_id, body.reason)
    db.commit()
    return service.to_out([entry], with_lines=True)[0]


# --- Sorties ----------------------------------------------------------------------------------


@router.get("/exits", response_model=Page[ExitOut])
def list_exits(
    ctx: ExitView,
    db: DbSession,
    now: NowDep,
    paging: Paging,
    search: str | None = None,
    status_filter: DocStatusParam = None,
    reason_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[ExitOut]:
    service = ExitService(db, ctx, now)
    items, total = service.search(
        paging,
        search,
        status_filter,
        site_id,
        date_from,
        date_to,
        extra=[StockExit.reason_id == reason_id] if reason_id else [],
        search_columns=[StockExit.reference, StockExit.beneficiary],
    )
    return Page(items=service.to_out(items), total=total, limit=paging.limit, offset=paging.offset)


@router.post("/exits", response_model=ExitOut, status_code=status.HTTP_201_CREATED)
def create_exit(body: ExitCreate, ctx: ExitCreateCtx, db: DbSession, now: NowDep) -> ExitOut:
    service = ExitService(db, ctx, now)
    document = service.create(body)
    db.commit()
    return service.to_out([document], with_lines=True)[0]


@router.get("/exits/{exit_id}", response_model=ExitOut)
def get_exit(exit_id: uuid.UUID, ctx: ExitView, db: DbSession, now: NowDep) -> ExitOut:
    service = ExitService(db, ctx, now)
    return service.to_out([service.get(exit_id)], with_lines=True)[0]


@router.put("/exits/{exit_id}", response_model=ExitOut)
def update_exit(
    exit_id: uuid.UUID, body: ExitInput, ctx: ExitUpdate, db: DbSession, now: NowDep
) -> ExitOut:
    service = ExitService(db, ctx, now)
    document = service.update(exit_id, body)
    db.commit()
    return service.to_out([document], with_lines=True)[0]


@router.post("/exits/{exit_id}/validate", response_model=ExitOut)
def validate_exit(exit_id: uuid.UUID, ctx: ExitValidate, db: DbSession, now: NowDep) -> ExitOut:
    service = ExitService(db, ctx, now)
    document = service.validate(exit_id)
    db.commit()
    return service.to_out([document], with_lines=True)[0]


@router.post("/exits/{exit_id}/cancel", response_model=ExitOut)
def cancel_exit(
    exit_id: uuid.UUID, body: CancelInput, ctx: ExitCancel, db: DbSession, now: NowDep
) -> ExitOut:
    service = ExitService(db, ctx, now)
    document = service.cancel(exit_id, body.reason)
    db.commit()
    return service.to_out([document], with_lines=True)[0]


# --- Niveaux de stock et seuils par site -----------------------------------------------------

LevelView = Annotated[RequestContext, Depends(require_permission("stock.level.view"))]
ThresholdManage = Annotated[RequestContext, Depends(require_permission("stock.threshold.manage"))]
LocationManage = Annotated[RequestContext, Depends(require_permission("stock.location.manage"))]
MovementView = Annotated[RequestContext, Depends(require_permission("stock.movement.view"))]


@router.get("/levels", response_model=Page[LevelOut])
def list_stock_levels(
    ctx: LevelView,
    db: DbSession,
    paging: Paging,
    search: str | None = None,
    site_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    state: StateFilter = StateFilter.ALL,
    include_inactive: bool = False,
    article_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    location_id: uuid.UUID | None = None,
    unlocated: bool = False,
) -> Page[LevelOut]:
    rows, total = list_levels(
        db,
        ctx.tenant_id,
        filter_site_ids(ctx, site_id),
        paging,
        search=search,
        category_id=category_id,
        state=state,
        include_inactive=include_inactive,
        article_ids=set(article_id) if article_id else None,
        location_id=location_id,
        unlocated=unlocated,
    )
    return Page(
        items=[LevelOut.model_validate(r) for r in rows],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


@router.put("/levels/{site_id}/{article_id}/thresholds", response_model=LevelOut)
def set_stock_thresholds(
    site_id: uuid.UUID,
    article_id: uuid.UUID,
    body: ThresholdInput,
    ctx: ThresholdManage,
    db: DbSession,
    now: NowDep,
) -> LevelOut:
    site = operation_site(ctx, site_id)
    stock = StockService(db, ctx.tenant_id, ctx.user.id, now)
    ThresholdService(db, ctx, stock).set_thresholds(
        site, article_id, body.min_stock, body.max_stock
    )
    db.commit()
    return LevelOut.model_validate(get_level(db, ctx.tenant_id, site, article_id))


@router.put("/levels/{site_id}/{article_id}/location", response_model=LevelOut)
def set_article_location(
    site_id: uuid.UUID,
    article_id: uuid.UUID,
    body: LocationAssign,
    ctx: LocationManage,
    db: DbSession,
) -> LevelOut:
    """Lot 3-F : emplacement COURANT de l'article sur le site (``null`` : non rangé) ; un
    emplacement actif du MÊME site ; aucun effet sur le stock ni sur son état."""
    LocationService(db, ctx).assign(site_id, article_id, body.location_id)
    db.commit()
    return LevelOut.model_validate(get_level(db, ctx.tenant_id, site_id, article_id))


# --- Emplacements physiques par site (Lot 3-F, ADR-0044) ---------------------------------------


@router.get("/locations", response_model=Page[LocationOut])
def list_locations(
    ctx: LevelView,
    db: DbSession,
    paging: Paging,
    search: str | None = None,
    site_id: uuid.UUID | None = None,
    status_filter: StatusParam = StatusFilter.ALL,
) -> Page[LocationOut]:
    """Emplacements des sites visibles du membre (``site_id`` : un seul d'entre eux)."""
    rows, total = LocationService(db, ctx).search(paging, search, status_filter, site_id)
    return Page(
        items=[LocationOut.model_validate(r) for r in rows],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


@router.post("/locations", response_model=LocationOut, status_code=status.HTTP_201_CREATED)
def create_location(body: LocationCreate, ctx: LocationManage, db: DbSession) -> LocationOut:
    service = LocationService(db, ctx)
    location = service.create(body.site_id, body.name)
    db.commit()
    return LocationOut.model_validate(service.row(location.id))


@router.patch("/locations/{location_id}", response_model=LocationOut)
def rename_location(
    location_id: uuid.UUID, body: LocationRename, ctx: LocationManage, db: DbSession
) -> LocationOut:
    service = LocationService(db, ctx)
    service.rename(location_id, body.name)
    db.commit()
    return LocationOut.model_validate(service.row(location_id))


@router.post("/locations/{location_id}/activate", response_model=LocationOut)
def activate_location(location_id: uuid.UUID, ctx: LocationManage, db: DbSession) -> LocationOut:
    service = LocationService(db, ctx)
    service.set_active(location_id, True)
    db.commit()
    return LocationOut.model_validate(service.row(location_id))


@router.post("/locations/{location_id}/deactivate", response_model=LocationOut)
def deactivate_location(location_id: uuid.UUID, ctx: LocationManage, db: DbSession) -> LocationOut:
    service = LocationService(db, ctx)
    service.set_active(location_id, False)
    db.commit()
    return LocationOut.model_validate(service.row(location_id))


# --- Journal des mouvements ------------------------------------------------------------------


@router.get("/movements", response_model=Page[MovementOut])
def list_stock_movements(
    ctx: MovementView,
    db: DbSession,
    paging: Paging,
    search: str | None = None,
    site_id: uuid.UUID | None = None,
    article_id: uuid.UUID | None = None,
    movement_type: MovementType | None = None,
    user_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    source_type: Annotated[str | None, Query(max_length=50)] = None,
    source_id: uuid.UUID | None = None,
    lot_id: uuid.UUID | None = None,
) -> Page[MovementOut]:
    rows, total = list_movements(
        db,
        ctx.tenant_id,
        ctx.tenant.timezone,
        filter_site_ids(ctx, site_id),
        paging,
        search=search,
        article_id=article_id,
        movement_type=movement_type,
        user_id=user_id,
        date_from=date_from,
        date_to=date_to,
        source_type=source_type,
        source_id=source_id,
        lot_id=lot_id,
    )
    items = [
        MovementOut.model_validate(
            {
                **{c: getattr(row.StockMovement, c) for c in _MOVEMENT_FIELDS},
                "site_name": row.site_name,
                "article_reference": row.article_reference,
                "article_designation": row.article_designation,
                "unit": row.unit,
                "user_name": row.user_name,
                "document_number": row.document_number,
                "lot_number": row.lot_number,
                "lot_expiry_date": row.lot_expiry_date,
            }
        )
        for row in rows
    ]
    return Page(items=items, total=total, limit=paging.limit, offset=paging.offset)


_MOVEMENT_FIELDS = (
    "id",
    "occurred_at",
    "site_id",
    "article_id",
    "movement_type",
    "quantity",
    "quantity_before",
    "quantity_after",
    "unit_cost",
    "average_cost_before",
    "average_cost_after",
    "source_type",
    "source_id",
    "origin_movement_id",
    "comment",
    "packaging_name",
    "packaging_conversion",
    "packaging_quantity",
    "lot_id",
)


# --- Fiche fournisseur (Lot 3-E, ADR-0043) : lecture seule -------------------------------------


@router.get("/suppliers/{supplier_id}/summary", response_model=SupplierSummaryOut)
def supplier_summary(
    supplier_id: uuid.UUID, ctx: EntryView, db: DbSession, site_id: uuid.UUID | None = None
) -> SupplierSummaryOut:
    """Réceptions VALIDÉES du fournisseur (sites visibles) : nombre, dernière date, total."""
    summary = SupplierReceptions(db, ctx, supplier_id).summary(site_id)
    return SupplierSummaryOut.model_validate(summary, from_attributes=True)


@router.get("/suppliers/{supplier_id}/articles", response_model=Page[SupplierArticleOut])
def supplier_articles(
    supplier_id: uuid.UUID,
    ctx: EntryView,
    db: DbSession,
    paging: Paging,
    search: str | None = None,
    site_id: uuid.UUID | None = None,
) -> Page[SupplierArticleOut]:
    """Articles ayant au moins une réception VALIDÉE du fournisseur (sites visibles)."""
    items, total = SupplierReceptions(db, ctx, supplier_id).articles(paging, search, site_id)
    return Page(
        items=[SupplierArticleOut.model_validate(i, from_attributes=True) for i in items],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


# --- Lots et péremption (Lot 3-G, ADR-0045) : consultation et seuil du tenant ---------------------


@router.get("/lots", response_model=Page[LotOut])
def list_lots(
    ctx: LevelView,
    db: DbSession,
    now: NowDep,
    paging: Paging,
    search: str | None = None,
    article_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
    state: LotStateFilter = LotStateFilter.ALL,
    expires_before: date | None = None,
    in_stock: bool = False,
) -> Page[LotOut]:
    """Lots ayant un solde (même nul) sur les sites visibles ; état de péremption calculé
    (fuseau et seuil du tenant) ; tri par défaut : échéance la plus proche."""
    items, total = LotService(db, ctx, now).search(
        paging,
        search=search,
        article_id=article_id,
        site_id=site_id,
        state=state,
        expires_before=expires_before,
        in_stock=in_stock,
    )
    return Page(
        items=[LotOut.model_validate(i) for i in items],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


@router.get("/lots/{lot_id}", response_model=LotDetailOut)
def get_lot(lot_id: uuid.UUID, ctx: LevelView, db: DbSession, now: NowDep) -> LotDetailOut:
    """Fiche lot : soldes par site visible ; réceptions (``GET /stock/entries?lot_id=``) et
    mouvements (``GET /stock/movements?lot_id=``) avec leurs propres permissions."""
    row, balances = LotService(db, ctx, now).get(lot_id)
    return LotDetailOut(
        **LotOut.model_validate(row).model_dump(),
        balances=[LotBalanceOut.model_validate(b) for b in balances],
    )


@router.get("/settings", response_model=StockSettingsOut)
def get_stock_settings(ctx: LevelView, db: DbSession) -> StockSettingsOut:
    return StockSettingsOut(expiry_warning_days=SettingsService(db, ctx).get())


@router.put("/settings", response_model=StockSettingsOut)
def update_stock_settings(
    body: StockSettingsInput, ctx: ThresholdManage, db: DbSession
) -> StockSettingsOut:
    """Seuil « bientôt périmé » (D16) : ``stock.threshold.manage`` et accès à tous les sites."""
    value = SettingsService(db, ctx).update(body.expiry_warning_days)
    db.commit()
    return StockSettingsOut(expiry_warning_days=value)
