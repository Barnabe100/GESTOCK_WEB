"""Points QR d'un site (palier R7-A, ADR-0052) : création, lecture, libellé, activation,
désactivation et régénération du jeton.

Contrôles du serveur :

- point de TABLE : table EXISTANTE, ACTIVE, du MÊME site (port « tables » des commandes, verrou
  partagé de la table), module ``restaurant.tables`` effectif sur le site ; un seul point actif
  par table (index unique partiel : ``409`` en concurrence) ;
- point de COMPTOIR : libellé unique par site, insensible à la casse ;
- jeton : 256 bits (``secrets``), seule son EMPREINTE est enregistrée ; l'adresse et l'image du
  QR Code ne figurent que dans la réponse d'émission (création, régénération) — jamais relues,
  jamais journalisées, jamais dans l'audit ;
- régénération : confirmation explicite exigée par le schéma ; l'ancienne empreinte est
  remplacée dans la même transaction (révocation immédiate des QR imprimés) ;
- l'adresse vient de ``app.core.public_urls`` seulement : sans adresse publique configurée,
  aucune émission (``503 public_url_not_configured``, rien n'est écrit).

Ne valide pas la transaction (l'endpoint appelle ``commit``).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.core.config import Settings
from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.public_urls import PublicUrls
from app.modules.restaurant_orders.api import TableRef, table_directory
from app.modules.restaurant_qr.models import (
    POINT_LABEL_INDEX,
    POINT_TABLE_INDEX,
    TOKEN_HASH_INDEX,
    QrPoint,
    QrPointKind,
)
from app.modules.restaurant_qr.qrcode import qr_svg
from app.modules.restaurant_qr.schemas import PointCreate, PointIssued, PointOut
from app.modules.restaurant_qr.sites import (
    ensure_row_site,
    operation_site,
    readable_site_ids,
    site_modules,
)
from app.modules.restaurant_qr.tokens import new_secret, secret_hash
from app.platform.audit.service import audit_action
from app.platform.context import RequestContext
from app.platform.identity.models import User
from app.platform.registry import ModuleStatus, get_registry
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, apply_sort, paginate_rows, search_filter, text_sort
from app.shared.schemas import StatusFilter

POINT_NOT_FOUND = "qr_point_not_found"
TABLES_MODULE = "restaurant.tables"
ENTITY = "restaurant_qr_point"


class QrPointService:
    def __init__(self, db: Session, ctx: RequestContext, settings: Settings, now: datetime) -> None:
        self.db = db
        self.ctx = ctx
        self.urls = PublicUrls(settings)
        self.now = now

    # --- Commun -------------------------------------------------------------------------------

    def _flush(self) -> None:
        """Les unicités font foi en base : une écriture concurrente échoue ici (``409``)."""
        try:
            self.db.flush()
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == POINT_TABLE_INDEX:
                raise ConflictError(
                    "Cette table a déjà un point QR actif", code="qr_point_table_taken"
                ) from exc
            if constraint == POINT_LABEL_INDEX:
                raise ConflictError(
                    "Un point QR de ce nom existe déjà sur ce site", code="qr_point_label_taken"
                ) from exc
            if constraint == TOKEN_HASH_INDEX:  # 2^-256 : jamais en pratique, refusé si besoin
                raise ConflictError("Réessayez", code="qr_point_token_collision") from exc
            raise

    def _audit(self, action: str, point: QrPoint, data: dict[str, Any]) -> None:
        # Jamais le jeton, son empreinte ni l'adresse du QR Code.
        audit_action(
            self.db,
            self.ctx,
            f"{ENTITY}.{action}",
            entity_type=ENTITY,
            entity_id=point.id,
            site_id=point.site_id,
            data={"kind": point.kind.value, **data},
        )

    def _issue(self, point: QrPoint) -> str:
        """Nouveau jeton du point : seule l'empreinte est conservée."""
        token = new_secret()
        point.token_hash = secret_hash(token)
        point.token_generated_at = self.now
        point.token_generated_by = self.ctx.user.id
        return token

    def _issued(self, point: QrPoint, token: str) -> PointIssued:
        url = self.urls.qr_point(token)
        return PointIssued(point=self.point_out(point.id), url=url, svg=qr_svg(url))

    def _tables_effective(self, site_id: uuid.UUID) -> bool:
        registry = get_registry()
        return (
            TABLES_MODULE in site_modules(self.ctx, site_id)
            and TABLES_MODULE in registry
            and registry.get(TABLES_MODULE).status is ModuleStatus.AVAILABLE
        )

    def _lock_table(self, site_id: uuid.UUID, table_id: uuid.UUID) -> TableRef:
        """Table ACTIVE du site, prise en partage (sa désactivation attend)."""
        directory = table_directory(self.db, self.ctx)
        if directory is None or not self._tables_effective(site_id):
            raise BusinessRuleError(
                "Les tables ne sont pas activées sur ce site", code="qr_tables_unavailable"
            )
        return directory.lock_for_order(site_id, table_id)

    # --- Lecture ------------------------------------------------------------------------------

    def _query(self) -> Select[Any]:
        generator = aliased(User)
        return (
            select(QrPoint, Site.name.label("site_name"), generator.full_name.label("by_name"))
            .join(Site, (Site.tenant_id == QrPoint.tenant_id) & (Site.id == QrPoint.site_id))
            .outerjoin(generator, generator.id == QrPoint.token_generated_by)
            .where(QrPoint.tenant_id == self.ctx.tenant_id)
        )

    def _outs(self, rows: list[Any]) -> list[PointOut]:
        directory = table_directory(self.db, self.ctx)
        table_ids = {row[0].table_id for row in rows if row[0].table_id is not None}
        tables = directory.labels(table_ids) if directory is not None and table_ids else {}
        outs = []
        for point, site_name, by_name in rows:
            table = tables.get(point.table_id) if point.table_id is not None else None
            outs.append(
                PointOut(
                    id=point.id,
                    site_id=point.site_id,
                    site_name=site_name,
                    kind=point.kind,
                    table_id=point.table_id,
                    table_label=table.label if table is not None else None,
                    table_active=table.is_active if table is not None else None,
                    zone_name=table.zone_name if table is not None else None,
                    label=point.label,
                    display_name=point.label or (table.label if table is not None else ""),
                    is_active=point.is_active,
                    token_generated_at=point.token_generated_at,
                    token_generated_by_name=by_name,
                    created_at=point.created_at,
                    updated_at=point.updated_at,
                )
            )
        return outs

    def search(
        self,
        params: PageParams,
        search: str | None,
        status: StatusFilter,
        site_id: uuid.UUID | None,
        kind: QrPointKind | None,
    ) -> tuple[list[PointOut], int]:
        stmt = self._query().where(QrPoint.site_id.in_(readable_site_ids(self.ctx, site_id)))
        condition = search_filter(search, QrPoint.label)
        if condition is not None:
            stmt = stmt.where(condition)
        if kind is not None:
            stmt = stmt.where(QrPoint.kind == kind)
        if status is not StatusFilter.ALL:
            stmt = stmt.where(QrPoint.is_active.is_(status is StatusFilter.ACTIVE))
        sortable = {
            "created_at": QrPoint.created_at,
            "label": text_sort(QrPoint.label),
            "kind": QrPoint.kind,
            "site": text_sort(Site.name),
            "token_generated_at": QrPoint.token_generated_at,
        }
        stmt = apply_sort(stmt, params.sort, sortable, "-created_at", QrPoint.id)
        rows, total = paginate_rows(self.db, stmt, params)
        return self._outs([tuple(row) for row in rows]), total

    def point_out(self, point_id: uuid.UUID) -> PointOut:
        row = self.db.execute(self._query().where(QrPoint.id == point_id)).one()
        return self._outs([tuple(row)])[0]

    def _get(self, point_id: uuid.UUID, *, lock: bool = False) -> QrPoint:
        """Point d'un site lisible par le membre ; sinon introuvable (jamais révélé)."""
        stmt = select(QrPoint).where(
            QrPoint.tenant_id == self.ctx.tenant_id, QrPoint.id == point_id
        )
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        point = self.db.scalars(stmt).one_or_none()
        if point is None:
            raise NotFoundError("Point QR introuvable", code=POINT_NOT_FOUND)
        ensure_row_site(self.ctx, point.site_id, POINT_NOT_FOUND)
        return point

    def get(self, point_id: uuid.UUID) -> PointOut:
        return self.point_out(self._get(point_id).id)

    # --- Écriture -----------------------------------------------------------------------------

    def create(self, data: PointCreate) -> PointIssued:
        site_id = operation_site(self.ctx, data.site_id)
        # Adresse publique vérifiée AVANT toute écriture (aucun point sans QR Code imprimable).
        self.urls.qr_point("")
        table: TableRef | None = None
        if data.kind is QrPointKind.TABLE:
            assert data.table_id is not None
            table = self._lock_table(site_id, data.table_id)
        point = QrPoint(
            tenant_id=self.ctx.tenant_id,
            site_id=site_id,
            kind=data.kind,
            table_id=data.table_id,
            label=data.label,
            is_active=True,
            created_by=self.ctx.user.id,
        )
        token = self._issue(point)
        self.db.add(point)
        self._flush()
        self._audit(
            "created",
            point,
            {"label": point.label, "table": table.label if table is not None else None},
        )
        return self._issued(point, token)

    def update(self, point_id: uuid.UUID, label: str) -> PointOut:
        point = self._get(point_id, lock=True)
        if point.kind is not QrPointKind.COUNTER:
            raise BusinessRuleError(
                "Le libellé d'un point de table est celui de sa table",
                code="qr_point_label_not_editable",
            )
        if point.label == label:
            return self.point_out(point.id)
        before = point.label
        point.label = label
        self._flush()
        self._audit("updated", point, {"before": {"label": before}, "after": {"label": label}})
        return self.point_out(point.id)

    def set_active(self, point_id: uuid.UUID, active: bool) -> PointOut:
        point = self._get(point_id, lock=True)
        if point.is_active == active:
            return self.point_out(point.id)
        if active and point.table_id is not None:
            # Réactivation : la table doit être encore active (un seul point actif par table).
            self._lock_table(point.site_id, point.table_id)
        point.is_active = active
        self._flush()
        self._audit("activated" if active else "deactivated", point, {"label": point.label})
        return self.point_out(point.id)

    def regenerate(self, point_id: uuid.UUID) -> PointIssued:
        """Nouveau jeton : l'ancien cesse de fonctionner dès la validation de la transaction."""
        point = self._get(point_id, lock=True)
        self.urls.qr_point("")
        token = self._issue(point)
        self._flush()
        self._audit("regenerated", point, {"label": point.label})
        return self._issued(point, token)
