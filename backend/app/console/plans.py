"""Offres et tarifs : paramètres commerciaux des plans, gérés par TechNova dans la console.

Deux responsabilités strictement séparées (ADR-0031) :

- **structure** (modules, fonctionnalités, limites, délai de grâce, nom, activation) : catalogue
  versionné ``plans.toml``, synchronisé par ``catalog sync`` ; **lecture seule** ici (le rôle
  SQL de la console n'a d'ailleurs le droit de modifier que les colonnes commerciales) ;
- **paramètres commerciaux** (publication, prix, périodes, devise, affichage du prix, contact,
  description, ordre, essai) : en base, modifiés ici avec une raison obligatoire et un audit
  avant/après ; jamais écrasés par ``catalog sync`` ; lus par ``GET /public/plans`` et par
  l'inscription. Une souscription garde le prix figé à sa création
  (``price_at_subscription``) : aucune modification n'est rétroactive.
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.console.audit import PlatformActor, record_platform_audit
from app.console.schemas import (
    PlanCommercialUpdate,
    PlanModuleOut,
    PlanPermissionOut,
    PlanStructureOut,
)
from app.core.errors import BusinessRuleError, NotFoundError
from app.platform.audit.service import RequestMeta
from app.platform.catalog.models import GeoCountry, Plan
from app.platform.registry import ModuleRegistry, ModuleStatus

COMMERCIAL_FIELDS = (
    "listed",
    "price_display_enabled",
    "monthly_price",
    "monthly_price_enabled",
    "annual_price",
    "annual_price_enabled",
    "currency",
    "contact_required",
    "commercial_description",
    "display_order",
    "trial_days",
)
CENT = Decimal("0.01")
# (période, champ « proposée », champ prix)
PERIODS = (
    ("monthly", "monthly_price_enabled", "monthly_price"),
    ("annual", "annual_price_enabled", "annual_price"),
)


def known_currencies(db: Session) -> set[str]:
    """Devises du référentiel des pays actifs (aucune table de devises)."""
    return {
        c
        for c in db.scalars(
            select(GeoCountry.currency).where(
                GeoCountry.is_active.is_(True), GeoCountry.currency.is_not(None)
            )
        )
        if c
    }


class PlanCommercialService:
    def __init__(self, db: Session, registry: ModuleRegistry) -> None:
        self.db = db
        self.registry = registry

    def list(self) -> list[Plan]:
        return list(
            self.db.scalars(
                select(Plan).order_by(
                    Plan.is_active.desc(), Plan.display_order, Plan.sort_order, Plan.code
                )
            )
        )

    def get(self, code: str, *, for_update: bool = False) -> Plan:
        query = select(Plan).where(Plan.code == code)
        if for_update:
            query = query.with_for_update()
        plan = self.db.scalars(query).one_or_none()
        if plan is None:
            raise NotFoundError("Plan introuvable", code="plan_not_found")
        return plan

    def structure(self, plan: Plan) -> PlanStructureOut:
        """Structure technique : modules inclus (plus les modules du socle), fonctionnalités,
        limites et permissions que le plan rend possibles (mêmes règles que les capacités)."""
        included = {m.module_code for m in plan.modules}
        core = self.registry.core_codes()
        modules = [
            PlanModuleOut(
                code=code,
                status=self.registry.get(code).status if code in self.registry else None,
                core=code in core,
            )
            for code in sorted(included | core)
        ]
        available = {
            m.code for m in modules if m.status is ModuleStatus.AVAILABLE and m.code in included
        } | core
        permissions = self.registry.available_permissions(available, plan.features)
        return PlanStructureOut(
            modules=modules,
            features=sorted(plan.features),
            limits={code: plan.limits.get(code) for code in sorted(self.registry.limit_codes())},
            grace_days=plan.grace_days,
            permissions=[
                PlanPermissionOut(
                    code=code,
                    module=self.registry.module_of_permission(code) or "",
                    access=perm.access,
                    feature=perm.feature,
                )
                for code, perm in sorted(permissions.items())
            ],
        )

    # --- Modification ------------------------------------------------------------------------

    def update_commercial(
        self, code: str, data: PlanCommercialUpdate, actor: PlatformActor, meta: RequestMeta
    ) -> Plan:
        plan = self.get(code, for_update=True)
        requested: dict[str, Any] = data.model_dump(exclude_unset=True, exclude={"reason"})
        for _, _, price_field in PERIODS:
            if requested.get(price_field) is not None:
                requested[price_field] = Decimal(requested[price_field]).quantize(CENT)
        if "commercial_description" in requested:
            description = (requested["commercial_description"] or "").strip()
            requested["commercial_description"] = description or None
        state = {field: getattr(plan, field) for field in COMMERCIAL_FIELDS} | requested
        self._validate(plan, state)

        changed = [
            f for f in COMMERCIAL_FIELDS if f in requested and requested[f] != getattr(plan, f)
        ]
        if not changed:
            raise BusinessRuleError("Aucune modification", code="no_changes")
        before = {f: getattr(plan, f) for f in changed}
        for field in changed:
            setattr(plan, field, requested[field])
        self.db.flush()  # contraintes CHECK de la base : seconde barrière

        if "listed" in changed:
            action = "plan.published" if plan.listed else "plan.unpublished"
        else:
            action = "plan.commercial.updated"
        record_platform_audit(
            self.db,
            actor=actor,
            action=action,
            target_type="plan",
            target_id=plan.code,
            before=before,
            after={f: getattr(plan, f) for f in changed},
            reason=data.reason,
            data={"fields": changed},
            meta=meta,
        )
        return plan

    def _validate(self, plan: Plan, state: dict[str, Any]) -> None:
        """Cohérence des paramètres commerciaux (le frontend n'est jamais la seule barrière)."""
        currency = state["currency"]
        if currency is not None and currency not in known_currencies(self.db):
            raise BusinessRuleError(
                "Devise inconnue du référentiel des pays", code="unknown_currency"
            )
        for period, enabled_field, price_field in PERIODS:
            if state[enabled_field] and state[price_field] is None:
                raise BusinessRuleError(
                    "Une période proposée doit avoir un prix (0 : gratuit)",
                    code="price_required",
                    extra={"period": period},
                )
        has_price = any(state[price_field] is not None for _, _, price_field in PERIODS)
        if has_price and currency is None:
            raise BusinessRuleError("Un prix exige une devise", code="currency_required")
        open_period = any(state[enabled_field] for _, enabled_field, _ in PERIODS)
        if state["price_display_enabled"] and not open_period:
            raise BusinessRuleError(
                "Afficher le prix exige au moins une période proposée",
                code="price_display_without_period",
            )
        if state["listed"]:
            if not plan.is_active:
                raise BusinessRuleError(
                    "Un plan retiré du catalogue ne peut pas être publié", code="plan_inactive"
                )
            if not state["contact_required"] and not open_period:
                raise BusinessRuleError(
                    "Un plan publié doit proposer une période ou exiger un contact commercial",
                    code="plan_not_subscribable",
                )
