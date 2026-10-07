"""Transferts inter-sites (Phase 2.5) : BROUILLON → VALIDÉ → ANNULÉ, fonctionnalité de plan
``stock.transfers`` (ADR-0018).

- Le stock ne change qu'à la validation et à l'annulation d'un transfert validé, exclusivement
  via ``StockService`` (``transfer`` puis ``apply_many``), dans la transaction de la requête :
  sortie du site source et entrée du site destination réussissent ou échouent ensemble.
- Le transfert est verrouillé (``SELECT … FOR UPDATE``) avant tout changement de statut : une
  double validation, même simultanée, ne s'applique qu'une fois.
- Sites : deux sites distincts, actifs et accessibles au membre ; la permission de l'opération
  est exigée **sur les deux sites** (un rôle limité à un site n'y suffit pas pour l'autre).
- Lot 3-H-B1 (ADR-0045) : un article suivi par lot est réparti MANUELLEMENT sur ses lots
  (brouillon incomplet admis, somme exacte à la validation) ; ``StockService.transfer_lots``
  produit une paire sortie / entrée par lot, sous le MÊME lot des deux côtés ; lot périmé
  refusé (``lot_expired_not_transferable``, sans dérogation) ; aucune permission nouvelle.
"""

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, ForbiddenError, NotFoundError
from app.modules.catalog.api import (
    QUANTITY_STEP,
    ArticleRef,
    ensure_in_assortment,
    get_article_refs,
)
from app.modules.stock.api import available_lots_out
from app.modules.stock.document_service import (
    check_lot_choices,
    packaging_snapshot,
    present_lines,
    revalidate_lines,
)
from app.modules.stock.lot_service import (
    ExpiryContext,
    expiry_context,
    lot_allocations,
    lot_infos,
)
from app.modules.stock.models import (
    DocumentStatus,
    MovementType,
    StockMovement,
    StockTransfer,
    StockTransferLine,
    StockTransferLineLot,
)
from app.modules.stock.schemas import (
    AvailableLotsOut,
    LineLotOut,
    LineOut,
    TransferCreate,
    TransferInput,
    TransferOut,
)
from app.modules.stock.sites import tenant_today, visible_site_ids
from app.modules.stock.stock_service import (
    LotPick,
    MovementRequest,
    StockService,
    TransferItem,
    inverse_packaging,
    round_money,
)
from app.platform.audit.service import audit_action
from app.platform.capabilities.service import CapabilityService, SubscriptionMissingError
from app.platform.context import RequestContext
from app.platform.identity.models import User
from app.platform.registry import get_registry
from app.platform.sequences.service import next_number
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, apply_sort, paginate, search_filter

SOURCE_TYPE = "stock_transfer"
AUDIT_PREFIX = "stock_transfer"
NOT_FOUND = "stock_transfer_not_found"


def _names(db: Session, model: Any, ids: set[uuid.UUID | None], column: Any) -> dict[Any, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    return {
        row[0]: row[1] for row in db.execute(select(model.id, column).where(model.id.in_(wanted)))
    }


class TransferService:
    def __init__(self, db: Session, ctx: RequestContext, now: datetime) -> None:
        self.db = db
        self.ctx = ctx
        self.now = now
        self._site_permissions: dict[uuid.UUID, frozenset[str]] = {}

    # --- Lecture ------------------------------------------------------------------------------

    def _visible(self) -> Select[tuple[StockTransfer]]:
        """Transferts dont un site est visible (site sélectionné, sinon sites du membre) et
        dont les DEUX sites lui sont accessibles."""
        visible = visible_site_ids(self.ctx)
        accessible = self.ctx.capabilities.accessible_site_ids
        return select(StockTransfer).where(
            or_(
                StockTransfer.source_site_id.in_(visible),
                StockTransfer.destination_site_id.in_(visible),
            ),
            StockTransfer.source_site_id.in_(accessible),
            StockTransfer.destination_site_id.in_(accessible),
        )

    def search(
        self,
        params: PageParams,
        *,
        search: str | None = None,
        status: DocumentStatus | None = None,
        source_site_id: uuid.UUID | None = None,
        destination_site_id: uuid.UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[list[StockTransfer], int]:
        stmt = self._visible()
        conditions: list[Any] = [
            search_filter(search, StockTransfer.number),
            StockTransfer.status == status if status else None,
            StockTransfer.source_site_id == source_site_id if source_site_id else None,
            (
                StockTransfer.destination_site_id == destination_site_id
                if destination_site_id
                else None
            ),
            StockTransfer.operation_date >= date_from if date_from else None,
            StockTransfer.operation_date <= date_to if date_to else None,
        ]
        for condition in conditions:
            if condition is not None:
                stmt = stmt.where(condition)
        sortable = {
            "number": StockTransfer.number,
            "operation_date": StockTransfer.operation_date,
            "created_at": StockTransfer.created_at,
        }
        stmt = apply_sort(stmt, params.sort, sortable, "-number", StockTransfer.id)
        return paginate(self.db, stmt, params)

    def get(self, transfer_id: uuid.UUID, *, lock: bool = False) -> StockTransfer:
        stmt = self._visible().where(StockTransfer.id == transfer_id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        transfer = self.db.scalars(stmt).one_or_none()
        if transfer is None:
            raise NotFoundError("Transfert introuvable", code=NOT_FOUND)
        return transfer

    # --- Règles -------------------------------------------------------------------------------

    def _permissions_on(self, site_id: uuid.UUID) -> frozenset[str]:
        """Permissions du membre sur un site donné (rôles du tenant + rôles de ce site)."""
        if site_id not in self._site_permissions:
            # 1 site = 1 abonnement (ADR-0033) : rôles du site ET abonnement de ce site
            # (fonctionnalité ``stock.transfers`` exigée des deux côtés).
            try:
                capabilities = CapabilityService(self.db, get_registry()).resolve(
                    tenant=self.ctx.tenant,
                    membership=self.ctx.membership,
                    site_id=site_id,
                    now=self.now,
                )
            except SubscriptionMissingError:
                return frozenset()
            self._site_permissions[site_id] = capabilities.permissions
        return self._site_permissions[site_id]

    def _check_sites(self, source: uuid.UUID, destination: uuid.UUID, permission: str) -> None:
        """Deux sites distincts, actifs, accessibles ; le site sélectionné (X-Site-Id) est l'un
        des deux ; la permission est détenue sur chacun (rôles et abonnement du site)."""
        if source == destination:
            raise BusinessRuleError(
                "Le site destination doit être différent du site source",
                code="same_site_transfer",
            )
        accessible = self.ctx.capabilities.accessible_site_ids
        for site_id in (source, destination):
            # Site inactif, inconnu, d'un autre tenant ou hors du périmètre du membre.
            if site_id not in accessible:
                raise ForbiddenError(
                    "Accès à ce site refusé",
                    code="site_access_denied",
                    extra={"site_id": str(site_id)},
                )
        selected = self.ctx.site.id if self.ctx.site is not None else None
        if selected is not None and selected not in (source, destination):
            raise ForbiddenError(
                "Le transfert ne concerne pas le site sélectionné", code="site_mismatch"
            )
        # 1 site = 1 abonnement (ADR-0033) : la permission (et la fonctionnalité
        # ``stock.transfers``) est exigée de chaque site du transfert, selon SON abonnement ;
        # sans site sélectionné, les capacités consolidées ne suffisent pas.
        for site_id in (source, destination):
            if site_id != selected and permission not in self._permissions_on(site_id):
                raise ForbiddenError(
                    "Permission insuffisante sur l'autre site du transfert",
                    code="site_permission_denied",
                    extra={"site_id": str(site_id), "permission": permission},
                )

    def _operation_date(self, value: date | None) -> date:
        today = tenant_today(self.ctx, self.now)
        chosen = value or today
        if chosen > today:
            raise BusinessRuleError(
                "La date de l'opération ne peut pas être postérieure à aujourd'hui",
                code="future_operation_date",
            )
        return chosen

    def _require_draft(self, transfer: StockTransfer) -> None:
        if transfer.status is not DocumentStatus.DRAFT:
            raise ConflictError(
                "Opération impossible dans l'état actuel du transfert", code="document_not_draft"
            )

    def _snapshot(self, transfer: StockTransfer) -> dict[str, Any]:
        refs = get_article_refs(self.db, {line.article_id for line in transfer.lines})
        return {
            "source_site_id": str(transfer.source_site_id),
            "destination_site_id": str(transfer.destination_site_id),
            "operation_date": transfer.operation_date.isoformat(),
            "comment": transfer.comment,
            "lines": [
                {
                    "article_id": str(line.article_id),
                    "reference": refs[line.article_id].reference
                    if line.article_id in refs
                    else None,
                    "quantity": format(line.quantity, "f"),
                    **(
                        {
                            "packaging_id": str(line.packaging_id),
                            "packaging_name": line.packaging_name,
                            "base_quantity": format(line.base_quantity, "f"),
                        }
                        if line.packaging_id
                        else {}
                    ),
                    # Lot 3-H-B1 : choix de lots du brouillon (unité de base).
                    **(
                        {
                            "lots": [
                                {"lot_id": str(c.lot_id), "base_quantity": format(c.quantity, "f")}
                                for c in line.lots
                            ]
                        }
                        if line.lots
                        else {}
                    ),
                }
                for line in transfer.lines
            ],
        }

    def _audit(self, action: str, transfer: StockTransfer, data: dict[str, Any]) -> None:
        audit_action(
            self.db,
            self.ctx,
            f"{AUDIT_PREFIX}.{action}",
            entity_type=AUDIT_PREFIX,
            entity_id=transfer.id,
            site_id=transfer.source_site_id,
            data={"number": transfer.number, **data},
        )

    def _stock(self) -> StockService:
        return StockService(self.db, self.ctx.tenant_id, self.ctx.user.id, self.now)

    def _apply_input(self, transfer: StockTransfer, data: TransferInput) -> None:
        presented = present_lines(self.db, data.lines)
        # Recette, étape 1 (ADR-0046, D3) : article de l'assortiment ACTIF du site source ET du
        # site destination, dès le brouillon (aucun ajout automatique) ; la validation revérifie
        # sous verrou (``StockService``).
        article_ids = {line.article_id for line in data.lines}
        ensure_in_assortment(self.db, transfer.source_site_id, article_ids)
        ensure_in_assortment(self.db, data.destination_site_id, article_ids)
        # Lot 3-H-B1 : choix des lots contrôlés dès le brouillon (mêmes règles que les sorties).
        check_lot_choices(self.db, data.lines, presented)
        transfer.destination_site_id = data.destination_site_id
        transfer.operation_date = self._operation_date(data.operation_date)
        transfer.comment = data.comment
        # Présentation saisie et quantité de base (mécanisme commun, Lot 3-C) ; NUMERIC(18,3).
        transfer.lines = [
            StockTransferLine(
                tenant_id=self.ctx.tenant_id,
                line_no=index,
                article_id=shown.article_id,
                lots=[
                    StockTransferLineLot(
                        tenant_id=self.ctx.tenant_id,
                        article_id=shown.article_id,
                        lot_id=choice.lot_id,
                        position=position,
                        quantity=choice.quantity.quantize(QUANTITY_STEP),
                    )
                    for position, choice in enumerate(line.lots, start=1)
                ],
                **shown.columns(),
            )
            for index, (line, shown) in enumerate(zip(data.lines, presented, strict=True), start=1)
        ]

    # --- Cycle de vie -------------------------------------------------------------------------

    def create(self, data: TransferCreate) -> StockTransfer:
        source = data.source_site_id or (self.ctx.site.id if self.ctx.site else None)
        if source is None:
            raise BusinessRuleError("Choisissez le site source", code="site_required")
        self._check_sites(source, data.destination_site_id, "stock.transfer.create")
        transfer = StockTransfer(
            tenant_id=self.ctx.tenant_id,
            number=next_number(self.db, self.ctx.tenant_id, "stock_transfer", "TRF"),
            source_site_id=source,
            status=DocumentStatus.DRAFT,
            created_by=self.ctx.user.id,
        )
        self._apply_input(transfer, data)
        self.db.add(transfer)
        self.db.flush()
        self._audit(
            "created", transfer, {"status": transfer.status.value, **self._snapshot(transfer)}
        )
        return transfer

    def update(self, transfer_id: uuid.UUID, data: TransferInput) -> StockTransfer:
        """Brouillon seulement ; le site source est fixé à la création."""
        transfer = self.get(transfer_id, lock=True)
        self._require_draft(transfer)
        self._check_sites(
            transfer.source_site_id, data.destination_site_id, "stock.transfer.update"
        )
        before = self._snapshot(transfer)
        transfer.lines = []
        self.db.flush()  # remplacement des lignes d'un brouillon
        self._apply_input(transfer, data)
        self.db.flush()
        after = self._snapshot(transfer)
        if before != after:
            self._audit("updated", transfer, {"before": before, "after": after})
        return transfer

    def validate(self, transfer_id: uuid.UUID) -> StockTransfer:
        """Sortie du site source et entrée du site destination en une transaction : tout le
        stock source est contrôlé avant la moindre écriture ; tout échec annule l'ensemble.
        Lot 3-H-B1 : répartition par lot revérifiée par le moteur (lots du site source, non
        périmés, soldes sous verrou, somme exacte) ; un article non suivi garde sa paire unique."""
        transfer = self.get(transfer_id, lock=True)
        self._require_draft(transfer)
        if not transfer.lines:
            raise BusinessRuleError("Aucune ligne à valider", code="document_empty")
        self._check_sites(
            transfer.source_site_id, transfer.destination_site_id, "stock.transfer.validate"
        )
        revalidate_lines(self.db, transfer.lines)
        movements = self._stock().transfer_lots(
            transfer.source_site_id,
            transfer.destination_site_id,
            [
                TransferItem(
                    line_id=line.id,
                    article_id=line.article_id,
                    quantity=line.base_quantity,
                    packaging=packaging_snapshot(line),
                    picks=tuple(LotPick(c.lot_id, c.quantity) for c in line.lots),
                )
                for line in transfer.lines
            ],
            source_type=SOURCE_TYPE,
            source_id=transfer.id,
            source_number=transfer.number,
            today=tenant_today(self.ctx, self.now),
        )
        # Coût figé = CMUP du site source lu une fois (identique pour tous les mouvements d'une
        # ligne, sortie ET entrée) ; montant arrondi une seule fois : quantité totale × coût.
        costs = {
            m.source_line_id: m.unit_cost
            for m in movements
            if m.movement_type is MovementType.TRANSFER_OUT
        }
        for line in transfer.lines:
            line.unit_cost = costs[line.id]
            line.amount = round_money(line.base_quantity * (line.unit_cost or Decimal("0")))
        transfer.status = DocumentStatus.VALIDATED
        transfer.validated_at = self.now
        transfer.validated_by = self.ctx.user.id
        self.db.flush()
        self._audit(
            "validated",
            transfer,
            {
                "previous_status": DocumentStatus.DRAFT.value,
                "status": DocumentStatus.VALIDATED.value,
                **self._snapshot(transfer),
                "total": format(self._total(transfer) or Decimal("0"), "f"),
                **self._lots_audit(movements),
            },
        )
        return transfer

    def cancel(self, transfer_id: uuid.UUID, reason: str) -> StockTransfer:
        """Brouillon : abandon, sans effet sur le stock. Transfert validé : mouvements inverses
        ``CANCELLATION`` au coût du transfert — retrait du site destination, remise sur le site
        source — CMUP inchangés (STK-06) ; refus total si le stock destination ne suffit plus.
        Les mouvements d'origine ne sont jamais modifiés."""
        transfer = self.get(transfer_id, lock=True)
        previous = transfer.status
        if previous is DocumentStatus.CANCELLED:
            raise ConflictError("Transfert déjà annulé", code="transfer_already_cancelled")
        self._check_sites(
            transfer.source_site_id, transfer.destination_site_id, "stock.transfer.cancel"
        )
        restored: list[StockMovement] = []
        if previous is DocumentStatus.VALIDATED:
            stock = self._stock()
            outgoing = stock.movements_of(transfer.id, MovementType.TRANSFER_OUT)
            incoming = stock.movements_of(transfer.id, MovementType.TRANSFER_IN)
            comment = f"Annulation {transfer.number}"
            requests: list[tuple[uuid.UUID, MovementRequest]] = []
            for line in transfer.lines:
                # Retrait du site destination (−q), puis remise sur le site source (+q) : un
                # inverse par mouvement d'origine, MÊME lot, même quantité, même coût (Lot 3-H-B1 :
                # plusieurs par ligne répartie) ; refus total si un solde devenait négatif.
                for origin in incoming[line.id]:
                    requests.append(
                        (
                            transfer.destination_site_id,
                            MovementRequest(
                                article_id=line.article_id,
                                movement_type=MovementType.CANCELLATION,
                                quantity=-origin.quantity,
                                unit_cost=line.unit_cost,
                                source_type=SOURCE_TYPE,
                                source_id=transfer.id,
                                source_line_id=line.id,
                                source_number=transfer.number,
                                origin_movement_id=origin.id,
                                comment=comment,
                                packaging=inverse_packaging(
                                    origin,
                                    packaging_snapshot(line),
                                    single=len(incoming[line.id]) == 1,
                                ),
                                lot_id=origin.lot_id,
                            ),
                        )
                    )
                for origin in outgoing[line.id]:
                    requests.append(
                        (
                            transfer.source_site_id,
                            MovementRequest(
                                article_id=line.article_id,
                                movement_type=MovementType.CANCELLATION,
                                quantity=-origin.quantity,
                                unit_cost=line.unit_cost,
                                source_type=SOURCE_TYPE,
                                source_id=transfer.id,
                                source_line_id=line.id,
                                source_number=transfer.number,
                                origin_movement_id=origin.id,
                                comment=comment,
                                packaging=inverse_packaging(
                                    origin,
                                    packaging_snapshot(line),
                                    single=len(outgoing[line.id]) == 1,
                                ),
                                lot_id=origin.lot_id,
                            ),
                        )
                    )
            restored = stock.apply_many(requests)
        transfer.status = DocumentStatus.CANCELLED
        transfer.cancelled_at = self.now
        transfer.cancelled_by = self.ctx.user.id
        transfer.cancellation_reason = reason
        self.db.flush()
        self._audit(
            "cancelled",
            transfer,
            {
                "previous_status": previous.value,
                "status": DocumentStatus.CANCELLED.value,
                "reason": reason,
                "stock_restored": previous is DocumentStatus.VALIDATED,
                **self._snapshot(transfer),
                **self._lots_audit(restored),
            },
        )
        return transfer

    def _lots_audit(self, movements: Sequence[StockMovement]) -> dict[str, Any]:
        """Lots déplacés (validation) ou restaurés (annulation), pour l'audit : site, lot,
        quantité signée (la traçabilité reste le journal des mouvements)."""
        with_lot = [m for m in movements if m.lot_id is not None]
        if not with_lot:
            return {}
        refs = get_article_refs(self.db, {m.article_id for m in with_lot})
        infos = lot_infos(self.db, {m.lot_id for m in with_lot if m.lot_id})
        return {
            "lots": [
                {
                    "site_id": str(m.site_id),
                    "movement_type": m.movement_type.value,
                    "reference": refs[m.article_id].reference if m.article_id in refs else None,
                    "lot_id": str(m.lot_id),
                    "lot_number": infos[m.lot_id].number if m.lot_id in infos else None,
                    "base_quantity": format(m.quantity, "f"),
                }
                for m in with_lot
                if m.lot_id is not None
            ]
        }

    def available_lots(self, article_id: uuid.UUID, site_id: uuid.UUID | None) -> AvailableLotsOut:
        """Lots disponibles d'un article sur le SITE SOURCE d'un transfert (Lot 3-H-B1, D-8) :
        solde positif, péremption et état ; lots périmés signalés (``expired``), jamais
        transférables (D-1). Aucun coût. Le site (sélectionné ou fourni) doit être accessible et
        ``stock.transfer.create`` détenue sur lui (rôles et abonnement de CE site)."""
        selected = self.ctx.site.id if self.ctx.site is not None else None
        site = site_id or selected
        if site is None:
            raise BusinessRuleError("Choisissez un site", code="site_required")
        if site not in self.ctx.capabilities.accessible_site_ids:
            raise ForbiddenError("Accès à ce site refusé", code="site_access_denied")
        if site != selected and "stock.transfer.create" not in self._permissions_on(site):
            raise ForbiddenError(
                "Permission insuffisante sur ce site",
                code="site_permission_denied",
                extra={"site_id": str(site), "permission": "stock.transfer.create"},
            )
        return available_lots_out(self.db, self.ctx, self.now, site, article_id)

    # --- Sortie API -----------------------------------------------------------------------------

    @staticmethod
    def _total(transfer: StockTransfer) -> Decimal | None:
        amounts = [line.amount for line in transfer.lines]
        if not amounts or any(a is None for a in amounts):
            return None
        return round_money(sum((a for a in amounts if a is not None), Decimal("0")))

    def to_out(
        self, transfers: Sequence[StockTransfer], *, with_lines: bool = False
    ) -> list[TransferOut]:
        users: set[uuid.UUID | None] = {
            u for t in transfers for u in (t.created_by, t.validated_by, t.cancelled_by)
        }
        user_names = _names(self.db, User, users, User.full_name)
        sites: set[uuid.UUID | None] = {
            s for t in transfers for s in (t.source_site_id, t.destination_site_id)
        }
        site_names = _names(self.db, Site, sites, Site.name)
        refs: dict[uuid.UUID, ArticleRef] = (
            get_article_refs(self.db, {line.article_id for t in transfers for line in t.lines})
            if with_lines
            else {}
        )
        lots = self._line_lots(transfers) if with_lines else {}
        return [
            TransferOut(
                id=t.id,
                number=t.number,
                source_site_id=t.source_site_id,
                source_site_name=site_names.get(t.source_site_id, ""),
                destination_site_id=t.destination_site_id,
                destination_site_name=site_names.get(t.destination_site_id, ""),
                status=t.status,
                operation_date=t.operation_date,
                comment=t.comment,
                total_amount=self._total(t),
                line_count=len(t.lines),
                created_at=t.created_at,
                created_by_name=user_names.get(t.created_by) if t.created_by else None,
                validated_at=t.validated_at,
                validated_by_name=user_names.get(t.validated_by) if t.validated_by else None,
                cancelled_at=t.cancelled_at,
                cancelled_by_name=user_names.get(t.cancelled_by) if t.cancelled_by else None,
                cancellation_reason=t.cancellation_reason,
                lines=[_line_out(line, refs, lots.get(line.id)) for line in t.lines]
                if with_lines
                else [],
            )
            for t in transfers
        ]

    def _line_lots(self, transfers: Sequence[StockTransfer]) -> dict[uuid.UUID, list[LineLotOut]]:
        """Répartition par lot des lignes (Lot 3-H-B1) : brouillon = choix saisis ; transfert
        validé ou annulé = répartition réelle lue dans le journal (sorties du site source)."""
        expiry: ExpiryContext = expiry_context(self.db, self.ctx, self.now)
        drafts = [t for t in transfers if t.status is DocumentStatus.DRAFT]
        result: dict[uuid.UUID, list[LineLotOut]] = {}
        infos = lot_infos(
            self.db, {c.lot_id for t in drafts for line in t.lines for c in line.lots}
        )
        for t in drafts:
            for line in t.lines:
                if not line.lots:
                    continue
                result[line.id] = [
                    LineLotOut(
                        lot_id=c.lot_id,
                        lot_number=infos[c.lot_id].number if c.lot_id in infos else "?",
                        expiry_date=infos[c.lot_id].expiry_date if c.lot_id in infos else None,
                        state=expiry.state(infos[c.lot_id].expiry_date)
                        if c.lot_id in infos
                        else None,
                        quantity=c.quantity,
                    )
                    for c in line.lots
                ]
        done = {t.id for t in transfers if t.status is not DocumentStatus.DRAFT}
        for line_id, allocations in lot_allocations(
            self.db, done, MovementType.TRANSFER_OUT
        ).items():
            result[line_id] = [
                LineLotOut(
                    lot_id=a.lot_id,
                    lot_number=a.lot_number,
                    expiry_date=a.expiry_date,
                    state=expiry.state(a.expiry_date),
                    quantity=a.quantity,
                )
                for a in allocations
            ]
        return result


def _line_out(
    line: StockTransferLine,
    refs: dict[uuid.UUID, ArticleRef],
    lots: list[LineLotOut] | None = None,
) -> LineOut:
    ref = refs.get(line.article_id)
    return LineOut(
        id=line.id,
        line_no=line.line_no,
        article_id=line.article_id,
        article_reference=ref.reference if ref else "?",
        article_designation=ref.designation if ref else "?",
        unit=ref.unit if ref else "",
        quantity=line.quantity,
        unit_cost=line.unit_cost,
        amount=line.amount,
        packaging_id=line.packaging_id,
        packaging_name=line.packaging_name,
        packaging_conversion=line.packaging_conversion,
        base_quantity=line.base_quantity,
        lots=lots or [],
    )
