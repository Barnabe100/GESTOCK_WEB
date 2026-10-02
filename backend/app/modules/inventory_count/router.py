import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.inventory_count.models import InventoryStatus, InventoryType
from app.modules.inventory_count.schemas import (
    CancelInput,
    CandidateOut,
    CountsInput,
    CountsOut,
    DiscoveredLotInput,
    InventoryCreate,
    InventoryLineOut,
    InventoryOut,
    InventoryUpdate,
    LineState,
    LotCountsInput,
)
from app.modules.inventory_count.service import InventoryService
from app.platform.context import (
    DbSession,
    NowDep,
    RequestContext,
    require_any_permission,
    require_permission,
)
from app.platform.costs import INVENTORY_COST_FIELDS, cost_masking_route
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page

router = APIRouter(tags=["inventories"], route_class=cost_masking_route(INVENTORY_COST_FIELDS))

P = "inventory_count.inventory"
View = Annotated[RequestContext, Depends(require_permission(f"{P}.view"))]
Create = Annotated[RequestContext, Depends(require_permission(f"{P}.create"))]
Update = Annotated[RequestContext, Depends(require_permission(f"{P}.update"))]
Count = Annotated[RequestContext, Depends(require_permission(f"{P}.count"))]
Validate = Annotated[RequestContext, Depends(require_permission(f"{P}.validate"))]
Cancel = Annotated[RequestContext, Depends(require_permission(f"{P}.cancel"))]
Prepare = Annotated[RequestContext, Depends(require_any_permission(f"{P}.create", f"{P}.update"))]
Paging = Annotated[PageParams, Depends(page_params)]


def _detail(service: InventoryService, inventory_id: uuid.UUID) -> InventoryOut:
    return service.to_out([service.get(inventory_id)], with_summary=True)[0]


@router.get("", response_model=Page[InventoryOut])
def list_inventories(
    ctx: View,
    db: DbSession,
    now: NowDep,
    paging: Paging,
    search: str | None = None,
    status_filter: Annotated[InventoryStatus | None, Query(alias="status")] = None,
    inventory_type: InventoryType | None = None,
    site_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[InventoryOut]:
    service = InventoryService(db, ctx, now)
    items, total = service.search(
        paging,
        search=search,
        status=status_filter,
        inventory_type=inventory_type,
        site_id=site_id,
        date_from=date_from,
        date_to=date_to,
    )
    return Page(items=service.to_out(items), total=total, limit=paging.limit, offset=paging.offset)


@router.get("/candidates", response_model=Page[CandidateOut])
def list_candidates(
    ctx: Prepare,
    db: DbSession,
    now: NowDep,
    paging: Paging,
    site_id: uuid.UUID | None = None,
    search: str | None = None,
    stocked_only: bool = False,
) -> Page[CandidateOut]:
    """Articles actifs proposables pour un inventaire du site (recherche serveur)."""
    items, total = InventoryService(db, ctx, now).candidates(
        site_id, paging, search=search, stocked_only=stocked_only
    )
    return Page(items=items, total=total, limit=paging.limit, offset=paging.offset)


@router.post("", response_model=InventoryOut, status_code=status.HTTP_201_CREATED)
def create_inventory(
    body: InventoryCreate, ctx: Create, db: DbSession, now: NowDep
) -> InventoryOut:
    service = InventoryService(db, ctx, now)
    inventory = service.create(body)
    db.commit()
    return _detail(service, inventory.id)


@router.get("/{inventory_id}", response_model=InventoryOut)
def get_inventory(inventory_id: uuid.UUID, ctx: View, db: DbSession, now: NowDep) -> InventoryOut:
    return _detail(InventoryService(db, ctx, now), inventory_id)


@router.put("/{inventory_id}", response_model=InventoryOut)
def update_inventory(
    inventory_id: uuid.UUID, body: InventoryUpdate, ctx: Update, db: DbSession, now: NowDep
) -> InventoryOut:
    service = InventoryService(db, ctx, now)
    service.update(inventory_id, body)
    db.commit()
    return _detail(service, inventory_id)


@router.get("/{inventory_id}/lines", response_model=Page[InventoryLineOut])
def list_lines(
    inventory_id: uuid.UUID,
    ctx: View,
    db: DbSession,
    now: NowDep,
    paging: Paging,
    search: str | None = None,
    state: LineState = LineState.ALL,
    article_id: uuid.UUID | None = None,
) -> Page[InventoryLineOut]:
    """``article_id`` (Lot 3-D) : ligne EXACTE d'un article identifié par un scan."""
    service = InventoryService(db, ctx, now)
    items, total = service.lines(
        service.get(inventory_id), paging, search=search, state=state, article_id=article_id
    )
    return Page(items=items, total=total, limit=paging.limit, offset=paging.offset)


@router.patch("/{inventory_id}/lines", response_model=CountsOut)
def save_counts(
    inventory_id: uuid.UUID, body: CountsInput, ctx: Count, db: DbSession, now: NowDep
) -> CountsOut:
    """Saisie progressive des quantités physiques (lot de lignes)."""
    service = InventoryService(db, ctx, now)
    inventory, line_ids = service.save_counts(inventory_id, body.counts)
    db.commit()
    inventory = service.get(inventory_id)
    lines, _ = service.lines(
        inventory, PageParams(limit=len(line_ids), offset=0, sort=None), line_ids=line_ids
    )
    return CountsOut(lines=lines, summary=service.summary(inventory))


def _line_and_summary(
    service: InventoryService, inventory_id: uuid.UUID, line_id: uuid.UUID
) -> CountsOut:
    inventory = service.get(inventory_id)
    lines, _ = service.lines(
        inventory, PageParams(limit=1, offset=0, sort=None), line_ids=[line_id]
    )
    return CountsOut(lines=lines, summary=service.summary(inventory))


# --- Lots d'une ligne suivie par lot (Lot 3-H) : comptage, découverte, actualisation ----------


@router.put("/{inventory_id}/lines/{line_id}/lots", response_model=CountsOut)
def save_lot_counts(
    inventory_id: uuid.UUID,
    line_id: uuid.UUID,
    body: LotCountsInput,
    ctx: Count,
    db: DbSession,
    now: NowDep,
) -> CountsOut:
    """Comptage complet d'une ligne suivie par lot (remplacement) ; lot non saisi = 0."""
    service = InventoryService(db, ctx, now)
    service.save_lot_counts(inventory_id, line_id, body.counts)
    db.commit()
    return _line_and_summary(service, inventory_id, line_id)


@router.post(
    "/{inventory_id}/lines/{line_id}/lots",
    response_model=CountsOut,
    status_code=status.HTTP_201_CREATED,
)
def add_discovered_lot(
    inventory_id: uuid.UUID,
    line_id: uuid.UUID,
    body: DiscoveredLotInput,
    ctx: Count,
    db: DbSession,
    now: NowDep,
) -> CountsOut:
    """Lot trouvé physiquement : rattaché au lot existant, ou créé à la validation seulement
    (``inventory_count.inventory.validate``)."""
    service = InventoryService(db, ctx, now)
    service.add_discovered_lot(inventory_id, line_id, body)
    db.commit()
    return _line_and_summary(service, inventory_id, line_id)


@router.delete("/{inventory_id}/lines/{line_id}/lots/{lot_row_id}", response_model=CountsOut)
def remove_discovered_lot(
    inventory_id: uuid.UUID,
    line_id: uuid.UUID,
    lot_row_id: uuid.UUID,
    ctx: Count,
    db: DbSession,
    now: NowDep,
) -> CountsOut:
    """Retrait d'un lot DÉCOUVERT (un lot attendu ne se retire pas : non trouvé = 0)."""
    service = InventoryService(db, ctx, now)
    service.remove_discovered_lot(inventory_id, line_id, lot_row_id)
    db.commit()
    return _line_and_summary(service, inventory_id, line_id)


@router.post("/{inventory_id}/refresh-lots", response_model=InventoryOut)
def refresh_lots(inventory_id: uuid.UUID, ctx: Count, db: DbSession, now: NowDep) -> InventoryOut:
    """« Actualiser les lots » : lots apparus sur le site depuis le démarrage ajoutés au
    comptage (retour au comptage si nécessaire)."""
    service = InventoryService(db, ctx, now)
    service.refresh_lots(inventory_id)
    db.commit()
    return _detail(service, inventory_id)


@router.post("/{inventory_id}/start", response_model=InventoryOut)
def start_inventory(
    inventory_id: uuid.UUID, ctx: Count, db: DbSession, now: NowDep
) -> InventoryOut:
    service = InventoryService(db, ctx, now)
    service.start(inventory_id)
    db.commit()
    return _detail(service, inventory_id)


@router.post("/{inventory_id}/complete-counting", response_model=InventoryOut)
def complete_counting(
    inventory_id: uuid.UUID, ctx: Count, db: DbSession, now: NowDep
) -> InventoryOut:
    service = InventoryService(db, ctx, now)
    service.complete_counting(inventory_id)
    db.commit()
    return _detail(service, inventory_id)


@router.post("/{inventory_id}/reopen-counting", response_model=InventoryOut)
def reopen_counting(
    inventory_id: uuid.UUID, ctx: Count, db: DbSession, now: NowDep
) -> InventoryOut:
    service = InventoryService(db, ctx, now)
    service.reopen_counting(inventory_id)
    db.commit()
    return _detail(service, inventory_id)


@router.post("/{inventory_id}/validate", response_model=InventoryOut)
def validate_inventory(
    inventory_id: uuid.UUID, ctx: Validate, db: DbSession, now: NowDep
) -> InventoryOut:
    service = InventoryService(db, ctx, now)
    service.validate(inventory_id)
    db.commit()
    return _detail(service, inventory_id)


@router.post("/{inventory_id}/cancel", response_model=InventoryOut)
def cancel_inventory(
    inventory_id: uuid.UUID, body: CancelInput, ctx: Cancel, db: DbSession, now: NowDep
) -> InventoryOut:
    service = InventoryService(db, ctx, now)
    service.cancel(inventory_id, body.reason)
    db.commit()
    return _detail(service, inventory_id)
