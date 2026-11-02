"""Recettes (palier R8, ADR-0053) : fiches techniques, versions, coût de revient et
disponibilité par site, fiche de cuisine.

Contrôles du serveur (jamais de l'interface) :

- plat : article du tenant, actif, NON géré en stock (D2) ; conditionnement DE L'article,
  actif ; une recette au plus par présentation (unicité en base : ``409 recipe_exists``) ;
- ingrédients (D3) : articles actifs, gérés en stock, en unité de base, un au plus une fois,
  jamais le plat ; quantité entière par portion pour un ingrédient en quantités entières (D4) ;
- versions immuables (D6) : une modification de composition crée la version n + 1 sous verrou
  de la recette ; l'historique reste consultable.

Coûts (``portion_cost``…) et marges : masqués sans ``catalog.article.cost_view`` (routeur).
Portée : recettes de l'entreprise ; coûts et disponibilité limités aux sites où le membre
consulte les recettes (module effectif, rôles du site). Ne valide pas la transaction.
"""

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.modules.catalog.api import (
    ArticleRef,
    PackagingRef,
    active_packagings,
    articles_view,
    get_article_refs,
    get_packaging_refs,
    is_whole,
    lock_packagings,
    lock_stock_managed,
)
from app.modules.restaurant_recipes.components import units_available
from app.modules.restaurant_recipes.engine import (
    COST_STEP,
    MONEY_STEP,
    Applied,
    Composition,
    applicable,
    compositions,
    consumed,
    round_quantity,
)
from app.modules.restaurant_recipes.models import (
    RECIPE_PRESENTATION_CONSTRAINT,
    Recipe,
    RecipeLine,
    RecipeVersion,
)
from app.modules.restaurant_recipes.permissions import RECIPE_VIEW
from app.modules.restaurant_recipes.profitability import profitability_rows
from app.modules.restaurant_recipes.schemas import (
    AvailabilityLineOut,
    CostLineOut,
    ProfitabilityPage,
    ProfitabilityRowOut,
    RecipeCreate,
    RecipeLineIn,
    RecipeLineOut,
    RecipeOut,
    RecipeSummaryOut,
    RecipeVersionIn,
    RecipeVersionOut,
    SheetLineOut,
    SheetOut,
    SiteAvailabilityOut,
    SiteCostOut,
)
from app.modules.restaurant_recipes.stock_view import site_assortment, site_stock
from app.modules.stock.api import tenant_today
from app.platform.audit.service import audit_action
from app.platform.context import RequestContext
from app.platform.costs import COST_VIEW
from app.platform.identity.models import User
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, apply_sort, paginate_rows, search_filter, text_sort
from app.shared.schemas import StatusFilter

RECIPE_NOT_FOUND = "recipe_not_found"
ZERO = Decimal("0")


def _refuse(message: str, code: str, **extra: Any) -> BusinessRuleError:
    return BusinessRuleError(message, code=code, extra=extra or None)


#: Période maximale d'un rapport de rentabilité (comme les rapports, ADR-0055).
MAX_REPORT_DAYS = 732


class RecipeService:
    def __init__(self, db: Session, ctx: RequestContext, now: datetime) -> None:
        self.db = db
        self.ctx = ctx
        self.now = now

    # --- Contrôles -------------------------------------------------------------------------

    def _check_dish(self, article_id: uuid.UUID, packaging_id: uuid.UUID | None) -> ArticleRef:
        """Plat (D2) sous verrou PARTAGÉ de l'article : son passage en « géré en stock »
        attend la fin de la transaction, puis voit la recette."""
        managed = lock_stock_managed(self.db, {article_id})
        refs = get_article_refs(self.db, {article_id})
        ref = refs.get(article_id)
        if ref is None:
            raise NotFoundError("Article introuvable", code="article_not_found")
        if not ref.is_active:
            raise _refuse("Article inactif", "article_inactive", articles=[ref.reference])
        if managed.get(article_id, True):
            raise _refuse(
                "Le plat d'une recette doit être un article non géré en stock (seuls ses "
                "ingrédients sortent du stock)",
                "recipe_article_stock_managed",
                articles=[ref.reference],
            )
        if packaging_id is not None:
            packaging = lock_packagings(self.db, {packaging_id}).get(packaging_id)
            if packaging is None or packaging.article_id != article_id:
                raise NotFoundError("Conditionnement introuvable", code="packaging_not_found")
            if not packaging.is_active:
                raise _refuse("Conditionnement inactif", "packaging_inactive")
        return ref

    def _check_lines(self, dish_id: uuid.UUID, data: RecipeVersionIn) -> None:
        ids = [line.article_id for line in data.lines]
        if len(set(ids)) != len(ids):
            raise _refuse(
                "Un ingrédient figure plusieurs fois dans la recette",
                "recipe_duplicate_ingredient",
            )
        if dish_id in ids:
            raise _refuse(
                "Un plat ne peut pas être son propre ingrédient", "recipe_self_ingredient"
            )
        # Verrou PARTAGÉ des ingrédients : « non géré » ne peut s'intercaler (D3).
        managed = lock_stock_managed(self.db, set(ids))
        refs = get_article_refs(self.db, set(ids))
        missing = [str(a) for a in ids if a not in refs]
        if missing:
            raise NotFoundError("Ingrédient introuvable", code="article_not_found")
        inactive = sorted(refs[a].reference for a in ids if not refs[a].is_active)
        if inactive:
            raise _refuse("Ingrédient inactif", "recipe_ingredient_inactive", articles=inactive)
        unmanaged = sorted(refs[a].reference for a in ids if not managed.get(a, True))
        if unmanaged:
            raise _refuse(
                "Un ingrédient doit être géré en stock",
                "recipe_ingredient_not_stock_managed",
                articles=unmanaged,
            )
        # D4 : ingrédient en quantités entières → quantité ENTIÈRE par portion.
        fractional = sorted(
            refs[line.article_id].reference
            for line in data.lines
            if not refs[line.article_id].decimal_quantity_allowed
            and not is_whole(line.quantity / data.yield_quantity)
        )
        if fractional:
            raise _refuse(
                "Cet ingrédient se compte en unités entières : la quantité par portion doit être "
                "entière",
                "recipe_whole_quantity_required",
                articles=fractional,
            )

    def _get(self, recipe_id: uuid.UUID, *, lock: bool = False) -> Recipe:
        stmt = select(Recipe).where(Recipe.id == recipe_id, Recipe.tenant_id == self.ctx.tenant_id)
        if lock:
            stmt = stmt.with_for_update()
        recipe = self.db.scalar(stmt)
        if recipe is None:
            raise NotFoundError("Recette introuvable", code=RECIPE_NOT_FOUND)
        return recipe

    def _audit(self, action: str, recipe: Recipe, data: dict[str, Any]) -> None:
        audit_action(
            self.db,
            self.ctx,
            f"recipe.{action}",
            entity_type="recipe",
            entity_id=recipe.id,
            data=data,
            site_id=None,
        )

    def _add_version(self, recipe: Recipe, number: int, data: RecipeVersionIn) -> RecipeVersion:
        version = RecipeVersion(
            tenant_id=self.ctx.tenant_id,
            recipe_id=recipe.id,
            number=number,
            yield_quantity=data.yield_quantity,
            instructions=data.instructions,
            created_by=self.ctx.user.id,
        )
        self.db.add(version)
        self.db.flush()
        self.db.add_all(
            RecipeLine(
                tenant_id=self.ctx.tenant_id,
                version_id=version.id,
                line_no=index,
                article_id=line.article_id,
                quantity=line.quantity,
                note=line.note,
            )
            for index, line in enumerate(data.lines, start=1)
        )
        recipe.current_version_id = version.id
        recipe.updated_at = self.now
        self.db.flush()
        return version

    @staticmethod
    def _composition_data(data: RecipeVersionIn) -> dict[str, Any]:
        return {
            "yield_quantity": format(data.yield_quantity, "f"),
            "instructions": data.instructions is not None,
            "lines": [_line_data(line) for line in data.lines],
        }

    # --- Écritures -------------------------------------------------------------------------

    def create(self, data: RecipeCreate) -> Recipe:
        self._check_dish(data.article_id, data.packaging_id)
        self._check_lines(data.article_id, data)
        recipe = Recipe(
            tenant_id=self.ctx.tenant_id,
            article_id=data.article_id,
            packaging_id=data.packaging_id,
            is_active=True,
            notes=data.notes,
            created_by=self.ctx.user.id,
        )
        self.db.add(recipe)
        try:
            with self.db.begin_nested():
                self.db.flush()
        except IntegrityError as error:
            if RECIPE_PRESENTATION_CONSTRAINT in str(error.orig):
                raise ConflictError(
                    "Cette présentation a déjà une recette", code="recipe_exists"
                ) from error
            raise
        version = self._add_version(recipe, 1, data)
        self._audit(
            "created",
            recipe,
            {
                "article_id": str(recipe.article_id),
                "packaging_id": str(recipe.packaging_id) if recipe.packaging_id else None,
                "version": version.number,
                **self._composition_data(data),
            },
        )
        return recipe

    def new_version(self, recipe_id: uuid.UUID, data: RecipeVersionIn) -> Recipe:
        """Nouvelle composition (D6) : version n + 1 sous verrou EXCLUSIF de la recette (une
        vente en cours de validation, qui la lit sous verrou partagé, se termine d'abord)."""
        recipe = self._get(recipe_id, lock=True)
        self._check_dish(recipe.article_id, None)
        self._check_lines(recipe.article_id, data)
        number = (
            self.db.scalar(
                select(func.max(RecipeVersion.number)).where(RecipeVersion.recipe_id == recipe.id)
            )
            or 0
        ) + 1
        previous = recipe.current_version_id
        version = self._add_version(recipe, number, data)
        self._audit(
            "version_created",
            recipe,
            {
                "version": version.number,
                "previous_version_id": str(previous) if previous else None,
                **self._composition_data(data),
            },
        )
        return recipe

    def update_notes(self, recipe_id: uuid.UUID, notes: str | None) -> Recipe:
        recipe = self._get(recipe_id, lock=True)
        if recipe.notes != notes:
            before = recipe.notes
            recipe.notes = notes
            recipe.updated_at = self.now
            self.db.flush()
            self._audit("updated", recipe, {"notes": {"before": before, "after": notes}})
        return recipe

    def set_active(self, recipe_id: uuid.UUID, active: bool) -> Recipe:
        recipe = self._get(recipe_id, lock=True)
        if recipe.is_active == active:
            return recipe
        if active:
            # Réactivation : plat toujours non géré, ingrédients toujours valables (D2, D3).
            self._check_dish(recipe.article_id, recipe.packaging_id)
            current = compositions(
                self.db,
                self.ctx.tenant_id,
                {(recipe.article_id, recipe.packaging_id)},
                active_only=False,
            ).get((recipe.article_id, recipe.packaging_id))
            if current is not None:
                self._check_lines(recipe.article_id, _version_input(current))
        recipe.is_active = active
        recipe.updated_at = self.now
        self.db.flush()
        self._audit("activated" if active else "deactivated", recipe, {"is_active": active})
        return recipe

    # --- Lectures --------------------------------------------------------------------------

    def _summary_query(self) -> tuple[Select[Any], Any]:
        articles = articles_view()
        counts = (
            select(RecipeLine.version_id, func.count().label("ingredient_count"))
            .group_by(RecipeLine.version_id)
            .subquery("recipe_line_counts")
        )
        return (
            select(
                Recipe.id,
                Recipe.article_id,
                articles.c.reference,
                articles.c.designation,
                articles.c.unit,
                Recipe.packaging_id,
                Recipe.is_active,
                RecipeVersion.number.label("current_number"),
                RecipeVersion.yield_quantity,
                func.coalesce(counts.c.ingredient_count, 0).label("ingredient_count"),
                Recipe.updated_at,
            )
            .join(
                articles,
                (articles.c.tenant_id == Recipe.tenant_id) & (articles.c.id == Recipe.article_id),
            )
            .outerjoin(
                RecipeVersion,
                (RecipeVersion.tenant_id == Recipe.tenant_id)
                & (RecipeVersion.id == Recipe.current_version_id),
            )
            .outerjoin(counts, counts.c.version_id == Recipe.current_version_id)
            .where(Recipe.tenant_id == self.ctx.tenant_id)
        ), articles

    def search(
        self,
        params: PageParams,
        search: str | None,
        status: StatusFilter,
        article_id: uuid.UUID | None,
    ) -> tuple[list[RecipeSummaryOut], int]:
        stmt, articles = self._summary_query()
        condition = search_filter(search, articles.c.reference, articles.c.designation)
        if condition is not None:
            stmt = stmt.where(condition)
        if status is not StatusFilter.ALL:
            stmt = stmt.where(Recipe.is_active.is_(status is StatusFilter.ACTIVE))
        if article_id is not None:
            stmt = stmt.where(Recipe.article_id == article_id)
        sortable = {
            "designation": text_sort(articles.c.designation),
            "reference": text_sort(articles.c.reference),
            "updated_at": Recipe.updated_at,
        }
        stmt = apply_sort(stmt, params.sort, sortable, "designation", Recipe.id)
        rows, total = paginate_rows(self.db, stmt, params)
        names = self._packaging_names({row.packaging_id for row in rows if row.packaging_id})
        return [
            RecipeSummaryOut(
                **row._mapping,
                packaging_name=names.get(row.packaging_id) if row.packaging_id else None,
            )
            for row in rows
        ], total

    def _packaging_names(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        return {pid: p.name for pid, p in self._packagings(ids).items()}

    def _packagings(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, PackagingRef]:
        return get_packaging_refs(self.db, ids)

    def _version_out(self, version: RecipeVersion) -> RecipeVersionOut:
        lines = list(
            self.db.scalars(
                select(RecipeLine)
                .where(RecipeLine.version_id == version.id)
                .order_by(RecipeLine.line_no)
            )
        )
        refs = get_article_refs(self.db, {line.article_id for line in lines})
        author = (
            self.db.scalar(select(User.full_name).where(User.id == version.created_by))
            if version.created_by
            else None
        )
        return RecipeVersionOut(
            id=version.id,
            number=version.number,
            yield_quantity=version.yield_quantity,
            instructions=version.instructions,
            created_at=version.created_at,
            created_by_name=author,
            lines=[
                RecipeLineOut(
                    article_id=line.article_id,
                    reference=refs[line.article_id].reference,
                    designation=refs[line.article_id].designation,
                    unit=refs[line.article_id].unit,
                    quantity=line.quantity,
                    note=line.note,
                    is_active=refs[line.article_id].is_active,
                    stock_managed=refs[line.article_id].stock_managed,
                )
                for line in lines
            ],
        )

    def get(self, recipe_id: uuid.UUID) -> RecipeOut:
        recipe = self._get(recipe_id)
        ref = get_article_refs(self.db, {recipe.article_id})[recipe.article_id]
        packaging = (
            self._packagings({recipe.packaging_id}).get(recipe.packaging_id)
            if recipe.packaging_id
            else None
        )
        current = (
            self.db.get(RecipeVersion, recipe.current_version_id)
            if recipe.current_version_id
            else None
        )
        count = self.db.scalar(select(func.count()).where(RecipeVersion.recipe_id == recipe.id))
        return RecipeOut(
            id=recipe.id,
            article_id=recipe.article_id,
            reference=ref.reference,
            designation=ref.designation,
            unit=ref.unit,
            article_active=ref.is_active,
            packaging_id=recipe.packaging_id,
            packaging_name=packaging.name if packaging is not None else None,
            packaging_conversion=packaging.conversion if packaging is not None else None,
            sale_price=(packaging.sale_price if packaging is not None else ref.sale_price),
            is_active=recipe.is_active,
            notes=recipe.notes,
            version_count=int(count or 0),
            current_version=self._version_out(current) if current is not None else None,
            created_at=recipe.created_at,
            updated_at=recipe.updated_at,
        )

    def versions(self, recipe_id: uuid.UUID) -> list[RecipeVersionOut]:
        recipe = self._get(recipe_id)
        return [
            self._version_out(version)
            for version in self.db.scalars(
                select(RecipeVersion)
                .where(RecipeVersion.recipe_id == recipe.id)
                .order_by(RecipeVersion.number.desc())
            )
        ]

    # --- Sites, coût de revient, disponibilité -----------------------------------------------

    def profitability(
        self,
        params: PageParams,
        date_from: date | None,
        date_to: date | None,
        site_id: uuid.UUID | None,
    ) -> ProfitabilityPage:
        """Rentabilité des plats (palier R10) sur les sites lisibles ; par défaut le mois en
        cours du tenant ; période de deux ans au plus."""
        today = tenant_today(self.ctx, self.now)
        start, end = date_from or today.replace(day=1), date_to or today
        if start > end:
            raise BusinessRuleError(
                "La date de début doit précéder la date de fin", code="report_period_invalid"
            )
        if (end - start).days + 1 > MAX_REPORT_DAYS:
            raise BusinessRuleError(
                "Période trop longue : deux ans au plus",
                code="report_period_too_long",
                extra={"max_days": MAX_REPORT_DAYS},
            )
        rows, total = profitability_rows(
            self.db,
            self.ctx.tenant.id,
            self.readable_site_ids(site_id),
            start,
            end,
            params,
            costs_visible=COST_VIEW in self.ctx.capabilities.permissions,
        )
        return ProfitabilityPage(
            date_from=start,
            date_to=end,
            items=[ProfitabilityRowOut(**row) for row in rows],
            total=total,
            limit=params.limit,
            offset=params.offset,
        )

    def readable_site_ids(self, site_id: uuid.UUID | None) -> list[uuid.UUID]:
        """Sites où le membre consulte les recettes (module effectif, rôles du site)."""
        visible = (
            {self.ctx.site.id}
            if self.ctx.site is not None
            else set(self.ctx.capabilities.accessible_site_ids)
        )
        if site_id is not None:
            visible &= {site_id}
        return sorted(s for s in visible if self.ctx.has_site_permission(s, RECIPE_VIEW))

    def _site_names(self, site_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not site_ids:
            return {}
        rows = self.db.execute(select(Site.id, Site.name).where(Site.id.in_(site_ids))).tuples()
        return {site_id: name for site_id, name in rows}

    def _current(self, recipe: Recipe) -> Composition:
        found = compositions(
            self.db,
            self.ctx.tenant_id,
            {(recipe.article_id, recipe.packaging_id)},
            active_only=False,
        )
        current = found.get((recipe.article_id, recipe.packaging_id))
        if current is None:
            raise NotFoundError("Recette introuvable", code=RECIPE_NOT_FOUND)
        return current

    def costs(self, recipe_id: uuid.UUID, site_id: uuid.UUID | None) -> list[SiteCostOut]:
        """Coût de revient de la version courante par site (D11) : CMUP COURANT du site."""
        recipe = self._get(recipe_id)
        current = self._current(recipe)
        sites = self.readable_site_ids(site_id)
        names = self._site_names(sites)
        ingredient_ids = {i.article_id for i in current.ingredients}
        refs = get_article_refs(self.db, ingredient_ids)
        levels = site_stock(self.db, self.ctx.tenant_id, set(sites), ingredient_ids)
        price = self.get(recipe_id).sale_price
        result: list[SiteCostOut] = []
        for site in sites:
            lines: list[CostLineOut] = []
            batch = ZERO
            complete = True
            for ingredient in current.ingredients:
                level = levels.get((site, ingredient.article_id))
                unit_cost = level.average_cost if level is not None else ZERO
                has_cost = unit_cost > 0
                complete = complete and has_cost
                line_cost = (ingredient.quantity * unit_cost).quantize(COST_STEP)
                batch += line_cost
                ref = refs[ingredient.article_id]
                lines.append(
                    CostLineOut(
                        article_id=ingredient.article_id,
                        reference=ref.reference,
                        designation=ref.designation,
                        unit=ref.unit,
                        quantity=ingredient.quantity,
                        unit_cost=unit_cost.quantize(COST_STEP),
                        line_cost=line_cost,
                        has_cost=has_cost,
                    )
                )
            portion = (batch / current.yield_quantity).quantize(COST_STEP)
            margin = (price - portion).quantize(MONEY_STEP) if price is not None else None
            rate = (
                (margin / price * 100).quantize(MONEY_STEP)
                if margin is not None and price is not None and price > 0
                else None
            )
            result.append(
                SiteCostOut(
                    site_id=site,
                    site_name=names.get(site, ""),
                    yield_quantity=current.yield_quantity,
                    batch_cost=batch.quantize(COST_STEP),
                    portion_cost=portion,
                    sale_price=price,
                    margin=margin,
                    margin_rate=rate,
                    complete=complete,
                    lines=lines,
                )
            )
        return result

    def availability(
        self, recipe_id: uuid.UUID, site_id: uuid.UUID | None
    ) -> list[SiteAvailabilityOut]:
        """Unités de la présentation réalisables par site (D12, indicatif)."""
        recipe = self._get(recipe_id)
        current = self._current(recipe)
        sites = self.readable_site_ids(site_id)
        names = self._site_names(sites)
        ingredient_ids = {i.article_id for i in current.ingredients}
        refs = get_article_refs(self.db, ingredient_ids)
        levels = site_stock(self.db, self.ctx.tenant_id, set(sites), ingredient_ids)
        stock = {key: level.quantity for key, level in levels.items()}
        assorted = site_assortment(self.db, self.ctx.tenant_id, set(sites), ingredient_ids)
        applied = Applied(composition=current, via_base=False)
        result: list[SiteAvailabilityOut] = []
        for site in sites:
            units, limiting = units_available(applied, site, Decimal("1"), stock, assorted)
            result.append(
                SiteAvailabilityOut(
                    site_id=site,
                    site_name=names.get(site, ""),
                    recipes_enabled=recipe.is_active,
                    units=units if recipe.is_active else 0,
                    limiting=sorted(refs[a].reference for a in limiting),
                    lines=[
                        AvailabilityLineOut(
                            article_id=ingredient.article_id,
                            reference=refs[ingredient.article_id].reference,
                            designation=refs[ingredient.article_id].designation,
                            unit=refs[ingredient.article_id].unit,
                            required=consumed(ingredient, Decimal("1"), current.yield_quantity),
                            available=stock.get((site, ingredient.article_id), ZERO),
                            in_assortment=(site, ingredient.article_id) in assorted,
                        )
                        for ingredient in current.ingredients
                    ],
                )
            )
        return result

    def sheet(
        self,
        article_id: uuid.UUID,
        packaging_id: uuid.UUID | None,
        quantity: Decimal,
    ) -> SheetOut:
        """Fiche technique d'une ligne (cuisine, D13) : ingrédients pour ``quantity`` unités de
        la présentation (même règle d'application que la vente, D5). Aucun coût."""
        key = (article_id, packaging_id)
        applied = applicable(self.db, self.ctx.tenant_id, {key}).get(key)
        if applied is None:
            raise NotFoundError("Aucune recette pour cette présentation", code=RECIPE_NOT_FOUND)
        composition = applied.composition
        conversion = Decimal("1")
        packaging_name: str | None = None
        if packaging_id is not None:
            packagings = {
                p.id: p
                for items in active_packagings(self.db, {article_id}, priced_only=False).values()
                for p in items
            }
            packaging = packagings.get(packaging_id)
            if packaging is not None:
                conversion, packaging_name = packaging.conversion, packaging.name
        portions = round_quantity(quantity * conversion if applied.via_base else quantity)
        ref = get_article_refs(self.db, {article_id})[article_id]
        refs = get_article_refs(self.db, {i.article_id for i in composition.ingredients})
        return SheetOut(
            recipe_id=composition.recipe_id,
            version_number=composition.version_number,
            designation=ref.designation,
            packaging_name=packaging_name,
            quantity=quantity,
            portions=portions,
            instructions=composition.instructions,
            lines=[
                SheetLineOut(
                    reference=refs[i.article_id].reference,
                    designation=refs[i.article_id].designation,
                    unit=refs[i.article_id].unit,
                    quantity=consumed(i, portions, composition.yield_quantity),
                    note=i.note,
                )
                for i in composition.ingredients
            ],
        )


def _line_data(line: RecipeLineIn) -> dict[str, Any]:
    return {"article_id": str(line.article_id), "quantity": format(line.quantity, "f")}


def _version_input(current: Composition) -> RecipeVersionIn:
    return RecipeVersionIn(
        yield_quantity=current.yield_quantity,
        instructions=current.instructions,
        lines=[
            RecipeLineIn(article_id=i.article_id, quantity=i.quantity, note=i.note)
            for i in current.ingredients
        ],
    )
