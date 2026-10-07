"""Assortiment par site (Recette, étape 1, ADR-0046, décisions D1 à D6).

CATALOGUE TENANT ≠ ASSORTIMENT SITE ≠ STOCK SITE : un article du catalogue global n'est proposé
sur un site que s'il figure dans son assortiment actif (``catalog_site_articles``) ; le stock
reste tenu par (site, article) dans ``stock_levels``, créé seulement par un mouvement réel.

- Ajout / réactivation (D4) : même ligne, ``added_at`` / ``added_by`` mis à jour, ``removed_*``
  vidés ; idempotent ; articles actifs du catalogue seulement.
- Retrait (D4) : désactivation, jamais de suppression ; refusé si l'article a, SUR CE SITE, un
  stock ou un solde de lot non nul, ou figure dans un document ouvert (port
  ``assortment_port``) ; tout ou rien pour un retrait multiple.
- Copie (D6) : ajout seulement depuis un autre site (catégorie facultative) ; aucun stock, seuil,
  emplacement ni lot copié.
- Permission ``catalog.assortment.manage`` (nature ``admin``, D5) contrôlée sur le site VISÉ :
  capacités de CE site (rôles et abonnement du site) ; un site ``pending_activation`` peut être
  préparé, sans permettre aucune opération métier.
- Ordre des verrous (D4) : article (partagé) → ligne d'assortiment (exclusif) ; les opérations
  prennent article → assortiment (partagé) → niveaux → lots.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from app.core.errors import BusinessRuleError, ConflictError, ForbiddenError, NotFoundError
from app.modules.catalog.api import barcode_search
from app.modules.catalog.assortment_port import RemovalBlockerKind, assortment_removal_blockers
from app.modules.catalog.models import Article, Category, SiteArticle
from app.modules.catalog.schemas import (
    ArticleSiteOut,
    AssortmentChangeOut,
    AssortmentRemovalOut,
    AssortmentState,
    AssortmentStatusFilter,
    SiteArticleOut,
)
from app.platform.audit.service import audit_action
from app.platform.capabilities.service import Capabilities
from app.platform.context import RequestContext
from app.platform.identity.models import User
from app.platform.tenancy.models import Site
from app.shared.clock import utcnow
from app.shared.pagination import PageParams, apply_sort, paginate_rows, search_filter, text_sort

MANAGE = "catalog.assortment.manage"
ARTICLE_VIEW = "catalog.article.view"


class AssortmentService:
    def __init__(self, db: Session, ctx: RequestContext, now: datetime | None = None) -> None:
        self.db = db
        self.ctx = ctx
        self.now = now or utcnow()

    # --- Contrôles de site (le serveur fait foi) ---------------------------------------------

    def _capabilities(self, site_id: uuid.UUID) -> Capabilities:
        """Capacités résolues pour CE site (rôles limités au site, abonnement du site)."""
        if site_id not in self.ctx.capabilities.accessible_site_ids:
            raise ForbiddenError("Accès à ce site refusé", code="site_access_denied")
        if self.ctx.site is not None and self.ctx.site.id == site_id:
            return self.ctx.capabilities
        return self.ctx.site_capabilities(site_id)

    def _require(self, site_id: uuid.UUID, code: str, *, selected: bool = True) -> None:
        """``code`` détenu sur ``site_id`` et autorisé par l'abonnement de ce site. ``selected`` :
        un site sélectionné (``X-Site-Id``) borne l'opération à ce site."""
        if selected and self.ctx.site is not None and self.ctx.site.id != site_id:
            raise ForbiddenError(
                "Le site ne correspond pas au site sélectionné", code="site_mismatch"
            )
        capabilities = self._capabilities(site_id)
        if code in capabilities.permissions:
            return
        extra = {"site_id": str(site_id)}
        if code in capabilities.restricted_permissions:
            raise ForbiddenError(
                "Action indisponible avec le statut de l'abonnement de ce site",
                code="subscription_restricted",
                extra=extra,
            )
        raise ForbiddenError(
            "Permission insuffisante sur ce site", code="permission_denied", extra=extra
        )

    def require_manage(self, site_id: uuid.UUID) -> None:
        self._require(site_id, MANAGE)

    def require_view(self, site_id: uuid.UUID) -> None:
        self._require(site_id, ARTICLE_VIEW)

    # --- Lecture ---------------------------------------------------------------------------

    def list_site_articles(
        self,
        site_id: uuid.UUID,
        params: PageParams,
        *,
        search: str | None = None,
        category_id: uuid.UUID | None = None,
        status: AssortmentStatusFilter = AssortmentStatusFilter.ACTIVE,
    ) -> tuple[list[SiteArticleOut], int]:
        self.require_view(site_id)
        added_by = aliased(User)
        removed_by = aliased(User)
        stmt = (
            select(
                SiteArticle.article_id,
                Article.reference,
                Article.designation,
                Article.category_id,
                Category.name.label("category_name"),
                Article.unit,
                Article.is_active.label("article_active"),
                Article.stock_managed,
                SiteArticle.is_active,
                SiteArticle.added_at,
                added_by.full_name.label("added_by_name"),
                SiteArticle.removed_at,
                removed_by.full_name.label("removed_by_name"),
            )
            .join(
                Article,
                (Article.id == SiteArticle.article_id)
                & (Article.tenant_id == SiteArticle.tenant_id),
            )
            .join(
                Category,
                (Category.id == Article.category_id) & (Category.tenant_id == Article.tenant_id),
            )
            .outerjoin(added_by, added_by.id == SiteArticle.added_by)
            .outerjoin(removed_by, removed_by.id == SiteArticle.removed_by)
            .where(SiteArticle.site_id == site_id)
        )
        conditions: list[ColumnElement[bool] | None] = [
            barcode_search(
                search,
                search_filter(search, Article.reference, Article.designation, Article.barcode),
                Article.id,
            ),
            Article.category_id == category_id if category_id else None,
            None
            if status is AssortmentStatusFilter.ALL
            else SiteArticle.is_active.is_(status is AssortmentStatusFilter.ACTIVE),
        ]
        for condition in conditions:
            if condition is not None:
                stmt = stmt.where(condition)
        sortable = {
            "reference": text_sort(Article.reference),
            "designation": text_sort(Article.designation),
            "category": text_sort(Category.name),
            "added_at": SiteArticle.added_at,
        }
        stmt = apply_sort(stmt, params.sort, sortable, "reference", SiteArticle.article_id)
        rows, total = paginate_rows(self.db, stmt, params)
        return [
            SiteArticleOut(
                article_id=r.article_id,
                reference=r.reference,
                designation=r.designation,
                category_id=r.category_id,
                category_name=r.category_name,
                unit=r.unit,
                article_active=r.article_active,
                stock_managed=r.stock_managed,
                state=AssortmentState.ACTIVE if r.is_active else AssortmentState.REMOVED,
                added_at=r.added_at,
                added_by_name=r.added_by_name,
                removed_at=r.removed_at,
                removed_by_name=r.removed_by_name,
            )
            for r in rows
        ], total

    def states(self, site_id: uuid.UUID, article_ids: set[uuid.UUID]) -> dict[uuid.UUID, Any]:
        """État d'assortiment de ces articles sur le site (liste du catalogue pour un site)."""
        self.require_view(site_id)
        rows = self.db.execute(
            select(SiteArticle.article_id, SiteArticle.is_active).where(
                SiteArticle.site_id == site_id, SiteArticle.article_id.in_(article_ids)
            )
        ).all()
        found = {
            r.article_id: AssortmentState.ACTIVE if r.is_active else AssortmentState.REMOVED
            for r in rows
        }
        return {a: found.get(a, AssortmentState.NONE) for a in article_ids}

    def article_sites(self, article_id: uuid.UUID) -> list[ArticleSiteOut]:
        """Situation de l'article sur les sites visibles du membre (site sélectionné, sinon ses
        sites) ; aucun stock ici (catalogue ≠ assortiment ≠ stock)."""
        if self.db.get(Article, article_id) is None:
            raise NotFoundError("Article introuvable", code="article_not_found")
        visible = (
            {self.ctx.site.id}
            if self.ctx.site is not None
            else set(self.ctx.capabilities.accessible_site_ids)
        )
        if not visible:
            return []
        sites = self.db.execute(
            select(Site.id, Site.name).where(Site.id.in_(visible), Site.is_active.is_(True))
        ).all()
        rows = {
            r.site_id: r
            for r in self.db.execute(
                select(
                    SiteArticle.site_id,
                    SiteArticle.is_active,
                    SiteArticle.added_at,
                    SiteArticle.removed_at,
                ).where(SiteArticle.article_id == article_id, SiteArticle.site_id.in_(visible))
            )
        }
        out = []
        for site in sorted(sites, key=lambda s: s.name.lower()):
            row = rows.get(site.id)
            out.append(
                ArticleSiteOut(
                    site_id=site.id,
                    site_name=site.name,
                    state=(
                        AssortmentState.NONE
                        if row is None
                        else AssortmentState.ACTIVE
                        if row.is_active
                        else AssortmentState.REMOVED
                    ),
                    added_at=row.added_at if row is not None else None,
                    removed_at=row.removed_at if row is not None else None,
                )
            )
        return out

    # --- Ajout / réactivation ----------------------------------------------------------------

    def add(self, site_id: uuid.UUID, article_ids: set[uuid.UUID]) -> AssortmentChangeOut:
        self.require_manage(site_id)
        articles = self._lock_articles(article_ids)
        inactive = sorted(a.reference for a in articles.values() if not a.is_active)
        if inactive:
            raise BusinessRuleError(
                "Article inactif", code="article_inactive", extra={"articles": inactive}
            )
        return self._add(site_id, articles)

    def _add(self, site_id: uuid.UUID, articles: dict[uuid.UUID, Article]) -> AssortmentChangeOut:
        """Ajout (articles déjà contrôlés et verrouillés) : nouvelle ligne, réactivation de la
        même ligne, ou inchangé. Insertion concurrente : ``ON CONFLICT DO NOTHING`` puis relecture
        sous verrou — jamais de doublon."""
        if not articles:
            return AssortmentChangeOut(added=0, reactivated=0, unchanged=0)
        ids = set(articles)
        rows = self._lock_rows(site_id, ids)
        missing = sorted(ids - set(rows))
        inserted: set[uuid.UUID] = set()
        if missing:
            inserted = set(
                self.db.scalars(
                    insert(SiteArticle)
                    .values(
                        [
                            {
                                "id": uuid.uuid4(),
                                "tenant_id": self.ctx.tenant_id,
                                "site_id": site_id,
                                "article_id": article_id,
                                "is_active": True,
                                "added_at": self.now,
                                "added_by": self.ctx.user.id,
                            }
                            for article_id in missing
                        ]
                    )
                    .on_conflict_do_nothing(index_elements=["tenant_id", "site_id", "article_id"])
                    .returning(SiteArticle.article_id)
                )
            )
            # Lignes insérées entre-temps par une autre transaction : relues sous verrou.
            if len(inserted) < len(missing):
                rows.update(self._lock_rows(site_id, set(missing) - inserted))
        reactivated = 0
        unchanged = 0
        for article_id in sorted(ids - inserted):
            row = rows[article_id]
            if row.is_active:
                unchanged += 1
                continue
            row.is_active = True
            row.added_at = self.now
            row.added_by = self.ctx.user.id
            row.removed_at = None
            row.removed_by = None
            reactivated += 1
            self._audit("site_assortment.added", site_id, articles[article_id], reactivated=True)
        for article_id in sorted(inserted):
            self._audit("site_assortment.added", site_id, articles[article_id], reactivated=False)
        self.db.flush()
        return AssortmentChangeOut(
            added=len(inserted), reactivated=reactivated, unchanged=unchanged
        )

    # --- Retrait -------------------------------------------------------------------------------

    def remove(self, site_id: uuid.UUID, article_ids: set[uuid.UUID]) -> AssortmentRemovalOut:
        """Retrait (D4) : désactivation, tout ou rien. Refus si stock ou lots non nuls sur ce
        site (``409 article_has_stock``) ou documents ouverts du site (``409
        article_in_open_documents``) ; la réponse détaille chaque article bloqué."""
        self.require_manage(site_id)
        articles = self._lock_articles(article_ids)
        rows = self._lock_rows(site_id, set(articles))
        active = {a for a, row in rows.items() if row.is_active}
        blockers = assortment_removal_blockers(self.db, self.ctx.tenant_id, site_id, active)
        if blockers:
            self._refuse_removal(site_id, articles, blockers)
        for article_id in sorted(active):
            row = rows[article_id]
            row.is_active = False
            row.removed_at = self.now
            row.removed_by = self.ctx.user.id
            self._audit("site_assortment.removed", site_id, articles[article_id])
        self.db.flush()
        return AssortmentRemovalOut(removed=len(active), unchanged=len(articles) - len(active))

    def _refuse_removal(
        self, site_id: uuid.UUID, articles: dict[uuid.UUID, Article], blockers: list[Any]
    ) -> None:
        by_article: dict[uuid.UUID, dict[str, Any]] = {}
        for blocker in blockers:
            entry = by_article.setdefault(
                blocker.article_id,
                {
                    "reference": articles[blocker.article_id].reference,
                    "stock": False,
                    "documents": [],
                },
            )
            if blocker.kind is RemovalBlockerKind.STOCK:
                entry["stock"] = True
            elif blocker.document and blocker.document not in entry["documents"]:
                entry["documents"].append(blocker.document)
        blocked = sorted(by_article.values(), key=lambda e: e["reference"])
        documents = sorted({d for e in blocked for d in e["documents"]})
        extra = {
            "site_id": str(site_id),
            "articles": [e["reference"] for e in blocked],
            "blocked": [
                {
                    "reference": e["reference"],
                    "reason": "stock" if e["stock"] else "open_document",
                    "documents": sorted(e["documents"])[:20],
                }
                for e in blocked
            ],
            "documents": documents[:20],
            "count": len(documents),
        }
        if any(e["stock"] for e in blocked):
            raise ConflictError(
                "Retrait impossible : l'article a encore du stock (ou un lot) sur ce site",
                code="article_has_stock",
                extra=extra,
            )
        raise ConflictError(
            "Retrait impossible : des documents ouverts de ce site contiennent l'article",
            code="article_in_open_documents",
            extra=extra,
        )

    # --- Copie (D6) ------------------------------------------------------------------------------

    def copy(
        self, site_id: uuid.UUID, source_site_id: uuid.UUID, category_id: uuid.UUID | None
    ) -> AssortmentChangeOut:
        """Ajoute à ``site_id`` les articles ACTIFS de l'assortiment actif de la source
        (catégorie facultative) : ajout seulement, lignes inactives réactivées, rien de retiré ;
        ni stock, ni seuils, ni emplacements, ni lots."""
        self.require_manage(site_id)
        if source_site_id == site_id:
            raise BusinessRuleError(
                "Choisissez un autre site comme source", code="assortment_copy_same_site"
            )
        # Lecture de la source : site accessible et catalogue consultable sur ce site (la source
        # n'est pas le site sélectionné : elle n'est que lue).
        self._require(source_site_id, ARTICLE_VIEW, selected=False)
        stmt = (
            select(SiteArticle.article_id)
            .join(
                Article,
                (Article.id == SiteArticle.article_id)
                & (Article.tenant_id == SiteArticle.tenant_id),
            )
            .where(
                SiteArticle.site_id == source_site_id,
                SiteArticle.is_active.is_(True),
                Article.is_active.is_(True),
            )
        )
        if category_id is not None:
            stmt = stmt.where(Article.category_id == category_id)
        article_ids = set(self.db.scalars(stmt))
        result = self._add(site_id, self._lock_articles(article_ids)) if article_ids else None
        result = result or AssortmentChangeOut(added=0, reactivated=0, unchanged=0)
        audit_action(
            self.db,
            self.ctx,
            "site_assortment.copied",
            entity_type="site",
            entity_id=site_id,
            site_id=site_id,
            data={
                "source_site_id": str(source_site_id),
                "category_id": str(category_id) if category_id else None,
                **result.model_dump(),
            },
        )
        return result

    # --- Création d'un article avec ses sites (D6) ----------------------------------------------

    def add_new_article(self, article: Article, site_ids: set[uuid.UUID]) -> None:
        """Associations d'un article créé à l'instant (permissions déjà contrôlées par
        ``require_manage`` avant la création : tout ou rien)."""
        for site_id in sorted(site_ids):
            self._add(site_id, {article.id: article})

    # --- Outils ------------------------------------------------------------------------------

    def _lock_articles(self, article_ids: set[uuid.UUID]) -> dict[uuid.UUID, Article]:
        """Articles du tenant (RLS), sous verrou PARTAGÉ, dans l'ordre des identifiants (premier
        maillon de l'ordre global des verrous) ; inconnus : ``404 article_not_found``."""
        if not article_ids:
            return {}
        found = {
            a.id: a
            for a in self.db.scalars(
                select(Article)
                .where(Article.id.in_(article_ids))
                .order_by(Article.id)
                .with_for_update(read=True, of=Article)
            )
        }
        if set(found) != article_ids:
            raise NotFoundError("Article introuvable", code="article_not_found")
        return found

    def _lock_rows(self, site_id: uuid.UUID, article_ids: set[uuid.UUID]) -> dict[uuid.UUID, Any]:
        if not article_ids:
            return {}
        return {
            row.article_id: row
            for row in self.db.scalars(
                select(SiteArticle)
                .where(SiteArticle.site_id == site_id, SiteArticle.article_id.in_(article_ids))
                .order_by(SiteArticle.article_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        }

    def _audit(
        self, action: str, site_id: uuid.UUID, article: Article, *, reactivated: bool | None = None
    ) -> None:
        data: dict[str, Any] = {
            "site_id": str(site_id),
            "reference": article.reference,
            "designation": article.designation,
        }
        if reactivated is not None:
            data["reactivated"] = reactivated
        audit_action(
            self.db,
            self.ctx,
            action,
            entity_type="article",
            entity_id=article.id,
            site_id=site_id,
            data=data,
        )
