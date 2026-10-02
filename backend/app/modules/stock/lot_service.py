"""Lots et péremption (Lot 3-G, ADR-0045).

- **Référentiel** : un lot appartient à un article ; identité (article, numéro sans distinction de
  casse, espaces de bord retirés) — le fournisseur n'en fait pas partie (D2, D3). Il est créé ou
  retrouvé à la validation d'une réception (D9 : achat et stock initial) ; un lot connu reçu
  avec une autre date de péremption est refusé (D4) ; numéro et dates figés ensuite (D11).
- **Soldes** : par lot et par site (``stock_lot_levels``), tenus par ``StockService`` seulement.
- **Péremption** : état calculé à la lecture, jamais stocké — périmé si la date est antérieure à
  aujourd'hui (``tenant_today``), bientôt périmé jusqu'à aujourd'hui + seuil (aujourd'hui inclus),
  normal au-delà, aucun état sans date (D16, D17).
- **Portée** (D15) : le référentiel est celui du tenant, mais un lot n'est visible que par les
  sites où il a un solde (y compris nul) parmi les sites visibles du membre ; ailleurs, il est
  introuvable. Aucun coût (D12, C1).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import Select, and_, case, func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ForbiddenError, NotFoundError
from app.modules.catalog.api import ArticleRef, articles_view
from app.modules.stock.models import StockEntryLine, StockLot, StockLotLevel, StockSettings
from app.modules.stock.sites import filter_site_ids, tenant_today, visible_site_ids
from app.platform.audit.service import audit_action
from app.platform.context import RequestContext
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, apply_sort, paginate_rows, search_filter, text_sort

# Seuil « bientôt périmé » par défaut (D16), en jours ; réglable par le tenant (0 à 365).
DEFAULT_EXPIRY_WARNING_DAYS = 30
FAR_FUTURE = date(9999, 12, 31)


class LotState(StrEnum):
    NO_EXPIRY = "no_expiry"
    OK = "ok"
    EXPIRING_SOON = "expiring_soon"
    EXPIRED = "expired"


class LotStateFilter(StrEnum):
    ALL = "all"
    NO_EXPIRY = "no_expiry"
    OK = "ok"
    EXPIRING_SOON = "expiring_soon"
    EXPIRED = "expired"


def expiry_state(expiry: date | None, today: date, warning_days: int) -> LotState:
    """État de péremption (D17) : périmé AVANT aujourd'hui ; aujourd'hui = bientôt périmé."""
    if expiry is None:
        return LotState.NO_EXPIRY
    if expiry < today:
        return LotState.EXPIRED
    if expiry <= today + timedelta(days=warning_days):
        return LotState.EXPIRING_SOON
    return LotState.OK


def expiry_warning_days(db: Session, tenant_id: uuid.UUID) -> int:
    value = db.scalar(
        select(StockSettings.expiry_warning_days).where(StockSettings.tenant_id == tenant_id)
    )
    return DEFAULT_EXPIRY_WARNING_DAYS if value is None else value


@dataclass(frozen=True)
class ExpiryContext:
    """Date du jour du tenant et seuil : tout calcul d'état de péremption d'une requête."""

    today: date
    warning_days: int

    def state(self, expiry: date | None) -> LotState:
        return expiry_state(expiry, self.today, self.warning_days)


def expiry_context(db: Session, ctx: RequestContext, now: datetime) -> ExpiryContext:
    return ExpiryContext(tenant_today(ctx, now), expiry_warning_days(db, ctx.tenant_id))


def lot_stocked_sites(db: Session, tenant_id: uuid.UUID, article_id: uuid.UUID) -> list[str]:
    """Port du catalogue (D7) : sites où un lot de l'article a un solde non nul."""
    rows = db.execute(
        select(Site.name)
        .join(
            StockLotLevel,
            and_(StockLotLevel.site_id == Site.id, StockLotLevel.tenant_id == Site.tenant_id),
        )
        .where(
            StockLotLevel.tenant_id == tenant_id,
            StockLotLevel.article_id == article_id,
            StockLotLevel.quantity != 0,
        )
        .distinct()
        .order_by(Site.name)
    ).all()
    return [row[0] for row in rows]


# --- Saisie et résolution des lots d'une réception ----------------------------------------------


@dataclass(frozen=True)
class LotInput:
    """Lot saisi sur une ligne de réception (brouillon)."""

    article_id: uuid.UUID
    number: str | None
    expiry_date: date | None
    manufacturing_date: date | None


def lot_key(article_id: uuid.UUID, number: str) -> tuple[uuid.UUID, str]:
    """Identité d'un lot : article + numéro sans distinction de casse (D2)."""
    return article_id, number.lower()


def check_lot_inputs(lines: list[LotInput], refs: dict[uuid.UUID, Any]) -> None:
    """Règles de saisie (brouillon ET validation, D5, D8, D17) : lot obligatoire pour un article
    suivi, interdit sinon ; péremption obligatoire si l'article est suivi en péremption ;
    fabrication ≤ péremption ; un même lot saisi sur plusieurs lignes (plusieurs présentations,
    D19) porte partout les mêmes dates."""
    seen: dict[tuple[uuid.UUID, str], tuple[date | None, date | None]] = {}
    for line in lines:
        ref: ArticleRef = refs[line.article_id]
        has_data = (
            line.number is not None
            or line.expiry_date is not None
            or line.manufacturing_date is not None
        )
        if not ref.lot_tracked:
            if has_data:
                raise BusinessRuleError(
                    "Cet article n'est pas suivi par lot",
                    code="article_not_lot_tracked",
                    extra={"articles": [ref.reference]},
                )
            continue
        if line.number is None:
            raise BusinessRuleError(
                "Numéro de lot obligatoire pour cet article",
                code="lot_number_required",
                extra={"articles": [ref.reference]},
            )
        if ref.expiry_tracked and line.expiry_date is None:
            raise BusinessRuleError(
                "Date de péremption obligatoire pour cet article",
                code="lot_expiry_required",
                extra={"articles": [ref.reference], "lots": [line.number]},
            )
        if (
            line.manufacturing_date is not None
            and line.expiry_date is not None
            and line.manufacturing_date > line.expiry_date
        ):
            raise BusinessRuleError(
                "La date de fabrication ne peut pas être postérieure à la date de péremption",
                code="lot_dates_invalid",
                extra={"articles": [ref.reference], "lots": [line.number]},
            )
        key = lot_key(line.article_id, line.number)
        dates = (line.expiry_date, line.manufacturing_date)
        if seen.setdefault(key, dates) != dates:
            raise BusinessRuleError(
                "Un même lot est saisi avec des dates différentes",
                code="lot_data_inconsistent",
                extra={"articles": [ref.reference], "lots": [line.number]},
            )


def _existing_lots(
    db: Session, keys: set[tuple[uuid.UUID, str]]
) -> dict[tuple[uuid.UUID, str], StockLot]:
    if not keys:
        return {}
    article_ids = {article_id for article_id, _ in keys}
    numbers = {number for _, number in keys}
    rows = db.scalars(
        select(StockLot).where(
            StockLot.article_id.in_(article_ids), func.lower(StockLot.number).in_(numbers)
        )
    )
    return {lot_key(lot.article_id, lot.number): lot for lot in rows}


def check_known_lots(db: Session, lines: list[LotInput], refs: dict[uuid.UUID, Any]) -> None:
    """D4 : un lot déjà connu de l'article doit être reçu avec SA date de péremption (et, si les
    deux sont renseignées, sa date de fabrication) — sinon refus. Contrôlé dès le brouillon et
    revérifié à la validation."""
    keyed = {lot_key(line.article_id, line.number): line for line in lines if line.number}
    for key, lot in _existing_lots(db, set(keyed)).items():
        line = keyed[key]
        reference = refs[line.article_id].reference
        if lot.expiry_date != line.expiry_date:
            raise BusinessRuleError(
                "Ce lot existe déjà avec une autre date de péremption",
                code="lot_expiry_mismatch",
                extra={
                    "articles": [reference],
                    "lots": [lot.number],
                    "expiry_date": lot.expiry_date.isoformat() if lot.expiry_date else None,
                },
            )
        if (
            lot.manufacturing_date is not None
            and line.manufacturing_date is not None
            and lot.manufacturing_date != line.manufacturing_date
        ):
            raise BusinessRuleError(
                "Ce lot existe déjà avec une autre date de fabrication",
                code="lot_manufacturing_mismatch",
                extra={"articles": [reference], "lots": [lot.number]},
            )


def resolve_lots(
    db: Session, ctx: RequestContext, lines: list[LotInput], refs: dict[uuid.UUID, Any]
) -> dict[tuple[uuid.UUID, str], uuid.UUID]:
    """Validation (D4, D9) : lot existant retrouvé, sinon créé. Deux validations simultanées
    créant le même lot : l'unicité (article, numéro) en garde un seul, l'autre est relu. Chaque
    lot créé est audité."""
    keyed = {lot_key(line.article_id, line.number): line for line in lines if line.number}
    if not keyed:
        return {}
    known = _existing_lots(db, set(keyed))
    for key, line in keyed.items():
        if key in known:
            continue
        created = db.execute(
            insert(StockLot)
            .values(
                id=uuid.uuid4(),
                tenant_id=ctx.tenant_id,
                article_id=line.article_id,
                number=line.number,
                expiry_date=line.expiry_date,
                manufacturing_date=line.manufacturing_date,
                created_by=ctx.user.id,
            )
            .on_conflict_do_nothing()
            .returning(StockLot.id)
        ).scalar_one_or_none()
        if created is not None:
            audit_action(
                db,
                ctx,
                "stock_lot.created",
                entity_type="stock_lot",
                entity_id=created,
                data={
                    "reference": refs[line.article_id].reference,
                    "lot_number": line.number,
                    "expiry_date": line.expiry_date.isoformat() if line.expiry_date else None,
                    "manufacturing_date": (
                        line.manufacturing_date.isoformat() if line.manufacturing_date else None
                    ),
                },
            )
    # Relecture (lots créés ici ou par une validation concurrente) puis contrôle D4.
    check_known_lots(db, list(keyed.values()), refs)
    return {key: lot.id for key, lot in _existing_lots(db, set(keyed)).items()}


def entries_with_lot(lot_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    """Réceptions comportant une ligne de ce lot (fiche lot)."""
    return select(StockEntryLine.entry_id).where(StockEntryLine.lot_id == lot_id)


def lot_names(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, StockLot]:
    if not ids:
        return {}
    return {lot.id: lot for lot in db.scalars(select(StockLot).where(StockLot.id.in_(ids)))}


# --- Consultation ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class LotRow:
    id: uuid.UUID
    article_id: uuid.UUID
    article_reference: str
    article_designation: str
    unit: str
    number: str
    expiry_date: date | None
    manufacturing_date: date | None
    state: LotState
    quantity: Any
    site_count: int
    created_at: datetime


@dataclass(frozen=True)
class LotBalance:
    site_id: uuid.UUID
    site_name: str
    quantity: Any


class LotService:
    """Lecture seule (le référentiel n'est alimenté que par les réceptions validées)."""

    def __init__(self, db: Session, ctx: RequestContext, now: datetime) -> None:
        self.db = db
        self.ctx = ctx
        self.expiry = expiry_context(db, ctx, now)

    def _state_case(self) -> Any:
        today = self.expiry.today
        soon = today + timedelta(days=self.expiry.warning_days)
        return case(
            (StockLot.expiry_date.is_(None), literal(LotState.NO_EXPIRY.value)),
            (StockLot.expiry_date < today, literal(LotState.EXPIRED.value)),
            (StockLot.expiry_date <= soon, literal(LotState.EXPIRING_SOON.value)),
            else_=literal(LotState.OK.value),
        )

    def _query(self, site_ids: set[uuid.UUID]) -> Select[Any]:
        """Lots ayant un solde (même nul) sur au moins un des sites donnés : quantité et nombre
        de sites sur ces seuls sites."""
        balances = (
            select(
                StockLotLevel.lot_id,
                func.sum(StockLotLevel.quantity).label("quantity"),
                func.count().label("site_count"),
            )
            .where(StockLotLevel.site_id.in_(site_ids))
            .group_by(StockLotLevel.lot_id)
            .subquery("balances")
        )
        articles = articles_view()
        return (
            select(
                StockLot.id,
                StockLot.article_id,
                articles.c.reference.label("article_reference"),
                articles.c.designation.label("article_designation"),
                articles.c.unit,
                StockLot.number,
                StockLot.expiry_date,
                StockLot.manufacturing_date,
                self._state_case().label("state"),
                balances.c.quantity,
                balances.c.site_count,
                StockLot.created_at,
            )
            .join(balances, balances.c.lot_id == StockLot.id)
            .join(
                articles,
                and_(
                    articles.c.id == StockLot.article_id,
                    articles.c.tenant_id == StockLot.tenant_id,
                ),
            )
            .where(StockLot.tenant_id == self.ctx.tenant_id)
        )

    def search(
        self,
        params: PageParams,
        *,
        search: str | None = None,
        article_id: uuid.UUID | None = None,
        site_id: uuid.UUID | None = None,
        state: LotStateFilter = LotStateFilter.ALL,
        expires_before: date | None = None,
        in_stock: bool = False,
    ) -> tuple[list[LotRow], int]:
        stmt = self._query(filter_site_ids(self.ctx, site_id))
        columns = stmt.selected_columns
        conditions: list[Any] = [
            search_filter(
                search, StockLot.number, columns.article_reference, columns.article_designation
            ),
            StockLot.article_id == article_id if article_id else None,
            self._state_case() == state.value if state is not LotStateFilter.ALL else None,
            StockLot.expiry_date <= expires_before if expires_before else None,
            columns.quantity > 0 if in_stock else None,
        ]
        for condition in conditions:
            if condition is not None:
                stmt = stmt.where(condition)
        sortable = {
            # Échéance la plus proche d'abord ; lots sans date en fin de liste.
            "expiry_date": func.coalesce(StockLot.expiry_date, FAR_FUTURE),
            "number": text_sort(StockLot.number),
            "article": text_sort(columns.article_reference),
            "quantity": columns.quantity,
            "created_at": StockLot.created_at,
        }
        stmt = apply_sort(stmt, params.sort, sortable, "expiry_date", StockLot.id)
        rows, total = paginate_rows(self.db, stmt, params)
        return [LotRow(**row._mapping) for row in rows], total

    def get(self, lot_id: uuid.UUID) -> tuple[LotRow, list[LotBalance]]:
        """Fiche lot : introuvable si le lot n'a aucun solde sur un site visible (D15)."""
        site_ids = visible_site_ids(self.ctx)
        row = self.db.execute(self._query(site_ids).where(StockLot.id == lot_id)).one_or_none()
        if row is None:
            raise NotFoundError("Lot introuvable", code="stock_lot_not_found")
        balances = self.db.execute(
            select(StockLotLevel.site_id, Site.name.label("site_name"), StockLotLevel.quantity)
            .join(
                Site,
                and_(Site.id == StockLotLevel.site_id, Site.tenant_id == StockLotLevel.tenant_id),
            )
            .where(StockLotLevel.lot_id == lot_id, StockLotLevel.site_id.in_(site_ids))
            .order_by(Site.name)
        ).all()
        return LotRow(**row._mapping), [LotBalance(**b._mapping) for b in balances]


# --- Réglages du tenant (D16) ---------------------------------------------------------------------


class SettingsService:
    def __init__(self, db: Session, ctx: RequestContext) -> None:
        self.db = db
        self.ctx = ctx

    def get(self) -> int:
        return expiry_warning_days(self.db, self.ctx.tenant_id)

    def update(self, warning_days: int) -> int:
        """Réglage de TOUT le tenant : réservé à un membre sans restriction de site (un rôle
        limité à un site ne modifie pas un réglage commun aux autres sites)."""
        membership = self.ctx.membership
        if not (membership.is_owner or membership.all_sites):
            raise ForbiddenError(
                "Ce réglage concerne toute l'entreprise : accès à tous les sites requis",
                code="tenant_wide_access_required",
            )
        before = self.get()
        if before == warning_days:
            return before
        self.db.execute(
            insert(StockSettings)
            .values(id=uuid.uuid4(), tenant_id=self.ctx.tenant_id, expiry_warning_days=warning_days)
            .on_conflict_do_update(
                index_elements=["tenant_id"],
                set_={"expiry_warning_days": warning_days, "updated_at": func.now()},
            )
        )
        audit_action(
            self.db,
            self.ctx,
            "stock_settings.updated",
            entity_type="stock_settings",
            entity_id=self.ctx.tenant_id,
            data={"expiry_warning_days": {"before": before, "after": warning_days}},
        )
        return warning_days
