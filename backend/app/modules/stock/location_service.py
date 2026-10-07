"""Emplacements physiques par site (Lot 3-F, ADR-0044).

Un emplacement (rayon, étagère, réserve…) appartient TOUJOURS à un site ; un article a AU PLUS
un emplacement courant par site, facultatif. Information de localisation seulement : le stock
reste tenu par (site, article) par ``StockService``, sans quantité par emplacement, et l'absence
d'emplacement ne bloque aucune opération. Aucun instantané dans les documents : seul
l'emplacement courant est affiché ; chaque changement est audité.

Portée : lecture sur les sites visibles du membre (``filter_site_ids``), écriture sur un site
accessible et autorisé par son abonnement (``operation_site``) — un emplacement d'un site non
visible est introuvable. Isolation entre entreprises : RLS + FK composites.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.modules.catalog.api import ensure_in_assortment, get_article_refs
from app.modules.stock.models import StockArticleLocation, StockLocation
from app.modules.stock.sites import filter_site_ids, operation_site, visible_site_ids
from app.platform.audit.service import audit_action
from app.platform.context import RequestContext
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, apply_sort, paginate_rows, search_filter, text_sort
from app.shared.schemas import StatusFilter

NAME_TAKEN = ("stock_location_name_taken", "Un emplacement de ce nom existe déjà sur ce site")


@dataclass(frozen=True)
class LocationRow:
    id: uuid.UUID
    site_id: uuid.UUID
    site_name: str
    name: str
    is_active: bool
    article_count: int
    created_at: datetime
    updated_at: datetime


def locations_view() -> Any:
    """Vue en lecture : emplacement COURANT de chaque article par site (``site_id``,
    ``article_id``, ``location_id``, ``location_name``, ``location_active``), pour les
    jointures d'autres modules (inventaires) et des niveaux. Filtrage par tenant : RLS et
    condition de l'appelant."""
    return (
        select(
            StockArticleLocation.tenant_id,
            StockArticleLocation.site_id,
            StockArticleLocation.article_id,
            StockLocation.id.label("location_id"),
            StockLocation.name.label("location_name"),
            StockLocation.is_active.label("location_active"),
        )
        .join(
            StockLocation,
            (StockLocation.tenant_id == StockArticleLocation.tenant_id)
            & (StockLocation.id == StockArticleLocation.location_id),
        )
        .subquery("article_locations_view")
    )


def current_locations(
    db: Session, site_id: uuid.UUID, article_ids: set[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, bool]]:
    """Emplacement courant (nom, actif) des articles sur un site — affichage indicatif des
    lignes d'entrée et de sortie (décision D6 ; jamais d'instantané, D8)."""
    if not article_ids:
        return {}
    view = locations_view()
    rows = db.execute(
        select(view.c.article_id, view.c.location_name, view.c.location_active).where(
            view.c.site_id == site_id, view.c.article_id.in_(article_ids)
        )
    )
    return {row.article_id: (row.location_name, row.location_active) for row in rows}


class LocationService:
    """Ne valide pas la transaction (ADR-0008) ; écritures d'audit dans la même transaction."""

    def __init__(self, db: Session, ctx: RequestContext) -> None:
        self.db = db
        self.ctx = ctx

    # --- Lecture ------------------------------------------------------------------------------

    def _query(self) -> Select[Any]:
        counts = (
            select(
                StockArticleLocation.location_id,
                func.count().label("article_count"),
            )
            .group_by(StockArticleLocation.location_id)
            .subquery("counts")
        )
        return (
            select(
                StockLocation.id,
                StockLocation.site_id,
                Site.name.label("site_name"),
                StockLocation.name,
                StockLocation.is_active,
                func.coalesce(counts.c.article_count, 0).label("article_count"),
                StockLocation.created_at,
                StockLocation.updated_at,
            )
            .join(
                Site,
                (Site.tenant_id == StockLocation.tenant_id) & (Site.id == StockLocation.site_id),
            )
            .outerjoin(counts, counts.c.location_id == StockLocation.id)
            .where(StockLocation.tenant_id == self.ctx.tenant_id)
        )

    def search(
        self,
        params: PageParams,
        search: str | None,
        status: StatusFilter,
        site_id: uuid.UUID | None,
    ) -> tuple[list[LocationRow], int]:
        stmt = self._query().where(StockLocation.site_id.in_(filter_site_ids(self.ctx, site_id)))
        condition = search_filter(search, StockLocation.name)
        if condition is not None:
            stmt = stmt.where(condition)
        if status is not StatusFilter.ALL:
            stmt = stmt.where(StockLocation.is_active.is_(status is StatusFilter.ACTIVE))
        sortable = {
            "name": text_sort(StockLocation.name),
            "site": text_sort(Site.name),
            "article_count": func.coalesce(stmt.selected_columns.article_count, 0),
            "created_at": StockLocation.created_at,
        }
        stmt = apply_sort(stmt, params.sort, sortable, "name", StockLocation.id)
        rows, total = paginate_rows(self.db, stmt, params)
        return [LocationRow(**row._mapping) for row in rows], total

    def row(self, location_id: uuid.UUID) -> LocationRow:
        row = self.db.execute(self._query().where(StockLocation.id == location_id)).one()
        return LocationRow(**row._mapping)

    def _get(self, location_id: uuid.UUID, *, lock: bool = False) -> StockLocation:
        """Emplacement d'un site VISIBLE du membre ; sinon introuvable (jamais révélé)."""
        stmt = select(StockLocation).where(StockLocation.id == location_id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        location = self.db.scalars(stmt).one_or_none()
        if location is None or location.site_id not in visible_site_ids(self.ctx):
            raise NotFoundError("Emplacement introuvable", code="stock_location_not_found")
        return location

    # --- Écriture -----------------------------------------------------------------------------

    def _flush(self) -> None:
        try:
            self.db.flush()
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == "uq_stock_locations_site_name":
                raise ConflictError(NAME_TAKEN[1], code=NAME_TAKEN[0]) from exc
            raise

    def _ensure_name_free(
        self, site_id: uuid.UUID, name: str, exclude: uuid.UUID | None = None
    ) -> None:
        stmt = select(StockLocation.id).where(
            StockLocation.site_id == site_id,
            func.lower(StockLocation.name) == name.lower(),
        )
        if exclude is not None:
            stmt = stmt.where(StockLocation.id != exclude)
        if self.db.scalar(stmt) is not None:
            raise ConflictError(NAME_TAKEN[1], code=NAME_TAKEN[0])

    def _audit(self, action: str, location: StockLocation, data: dict[str, Any]) -> None:
        audit_action(
            self.db,
            self.ctx,
            f"stock_location.{action}",
            entity_type="stock_location",
            entity_id=location.id,
            site_id=location.site_id,
            data=data,
        )

    def create(self, site_id: uuid.UUID | None, name: str) -> StockLocation:
        site = operation_site(self.ctx, site_id)
        self._ensure_name_free(site, name)
        location = StockLocation(tenant_id=self.ctx.tenant_id, site_id=site, name=name)
        self.db.add(location)
        self._flush()
        self._audit("created", location, {"name": name})
        return location

    def rename(self, location_id: uuid.UUID, name: str) -> StockLocation:
        location = self._get(location_id, lock=True)
        operation_site(self.ctx, location.site_id)
        if location.name == name:
            return location
        self._ensure_name_free(location.site_id, name, exclude=location.id)
        before = location.name
        location.name = name
        self._flush()
        self._audit("renamed", location, {"name": {"before": before, "after": name}})
        return location

    def set_active(self, location_id: uuid.UUID, active: bool) -> StockLocation:
        """Désactivation : plus affectable ; les affectations existantes restent (courantes)."""
        location = self._get(location_id, lock=True)
        operation_site(self.ctx, location.site_id)
        if location.is_active != active:
            location.is_active = active
            self._flush()
            self._audit("activated" if active else "deactivated", location, {"name": location.name})
        return location

    def assign(
        self, site_id: uuid.UUID, article_id: uuid.UUID, location_id: uuid.UUID | None
    ) -> None:
        """Emplacement courant d'un article sur un site (``None`` : non rangé). Emplacement du
        MÊME site et actif ; article géré en stock. Audit avant / après (noms)."""
        site = operation_site(self.ctx, site_id)
        ref = get_article_refs(self.db, {article_id}).get(article_id)
        if ref is None:
            raise NotFoundError("Article introuvable", code="article_not_found")
        if not ref.stock_managed:
            raise BusinessRuleError(
                "Article non géré en stock : aucun emplacement",
                code="article_not_stock_managed",
                extra={"articles": [ref.reference]},
            )
        # Recette, étape 1 (ADR-0046) : AFFECTER est réservé à l'assortiment ACTIF du site
        # (verrou partagé : un retrait concurrent attend) ; hors assortiment, l'emplacement
        # existant est conservé mais inerte. DÉSAFFECTER (``None``) reste permis : nettoyage de
        # configuration, sans effet sur le stock (décision du palier 2).
        if location_id is not None:
            ensure_in_assortment(self.db, site, {article_id}, lock=True)
        after: StockLocation | None = None
        if location_id is not None:
            # Verrou partagé : une désactivation concurrente attend la fin de l'affectation.
            after = self.db.scalars(
                select(StockLocation)
                .where(StockLocation.id == location_id)
                .with_for_update(read=True)
            ).one_or_none()
            if after is None or after.site_id not in visible_site_ids(self.ctx):
                raise NotFoundError("Emplacement introuvable", code="stock_location_not_found")
            if after.site_id != site:
                raise BusinessRuleError(
                    "Cet emplacement appartient à un autre site",
                    code="stock_location_other_site",
                )
        before = self._locked_assignment(site, article_id)
        if after is not None and before is not None and before.location_id == after.id:
            return  # inchangé (y compris un emplacement courant devenu inactif, conservé)
        if after is not None and not after.is_active:
            # Un emplacement inactif n'est plus affectable (l'affectation existante reste).
            raise BusinessRuleError("Cet emplacement est inactif", code="stock_location_inactive")
        if before is None and after is not None:
            # Première affectation ; une affectation concurrente du même article sur le même
            # site crée la ligne entre-temps : elle est alors reprise et verrouillée.
            created = self.db.execute(
                insert(StockArticleLocation)
                .values(
                    id=uuid.uuid4(),
                    tenant_id=self.ctx.tenant_id,
                    site_id=site,
                    article_id=article_id,
                    location_id=after.id,
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "site_id", "article_id"])
                .returning(StockArticleLocation.id)
            ).scalar_one_or_none()
            if created is not None:
                self._audit_assignment(ref.reference, site, article_id, None, after.name)
                return
            before = self._locked_assignment(site, article_id)
        if before is None:
            return  # déjà non rangé
        if after is not None and before.location_id == after.id:
            return  # inchangé
        before_name = self.db.scalar(
            select(StockLocation.name).where(StockLocation.id == before.location_id)
        )
        if after is None:
            self.db.delete(before)
        else:
            before.location_id = after.id
        self.db.flush()
        self._audit_assignment(
            ref.reference, site, article_id, before_name, after.name if after else None
        )

    def _locked_assignment(
        self, site_id: uuid.UUID, article_id: uuid.UUID
    ) -> StockArticleLocation | None:
        return self.db.scalars(
            select(StockArticleLocation)
            .where(
                StockArticleLocation.site_id == site_id,
                StockArticleLocation.article_id == article_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()

    def _audit_assignment(
        self,
        reference: str,
        site_id: uuid.UUID,
        article_id: uuid.UUID,
        before: str | None,
        after: str | None,
    ) -> None:
        audit_action(
            self.db,
            self.ctx,
            "stock_location.assigned",
            entity_type="article",
            entity_id=article_id,
            site_id=site_id,
            data={"reference": reference, "location": {"before": before, "after": after}},
        )
