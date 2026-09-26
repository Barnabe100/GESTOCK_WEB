"""Politique du plan commercial du tenant : **seul** point d'application des limites et des
fonctionnalités de plan.

Plan (données, ``catalog/data/plans.toml``)
  → limites        : valeurs plafonds (``max_sites``…) ; le comptage est déclaré par le module
                     propriétaire de la limite (``LimitDef`` dans son manifeste) ;
  → modules        : modules inclus (résolution des capacités) ;
  → fonctionnalités: options activées (``FeatureDef`` des modules) ;
  → politiques     : natures d'accès selon le statut d'abonnement (ADR-0011).

Le code métier n'appelle que ``ensure_capacity("<limite>")`` ou ``has_feature(...)`` : aucune
valeur ni règle d'offre commerciale n'y est écrite.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError
from app.platform.catalog.models import Plan
from app.platform.registry import ModuleRegistry


@dataclass(frozen=True)
class LimitUsage:
    limit: int | None  # None = illimité
    used: int


@dataclass(frozen=True)
class PlanTerms:
    """Conditions d'un abonnement : modules, fonctionnalités et limites. Celles de sa **licence
    en vigueur** (figées à l'émission, ADR-0034) ou, sans licence en vigueur, celles du plan."""

    plan_code: str
    modules: frozenset[str]
    features: frozenset[str]
    limits: Mapping[str, int | None] = field(default_factory=dict)
    license_id: uuid.UUID | None = None

    @classmethod
    def of_plan(cls, plan: Plan) -> "PlanTerms":
        return cls(
            plan_code=plan.code,
            modules=frozenset(m.module_code for m in plan.modules),
            features=frozenset(plan.features),
            limits=dict(plan.limits),
        )


class PlanPolicy:
    def __init__(self, db: Session, terms: Plan | PlanTerms, registry: ModuleRegistry) -> None:
        self.db = db
        self.terms = terms if isinstance(terms, PlanTerms) else PlanTerms.of_plan(terms)
        self.registry = registry

    def limit(self, code: str) -> int | None:
        if self.registry.limit(code) is None:
            raise ValueError(f"limite inconnue du registre : {code}")
        value = self.terms.limits.get(code)
        return int(value) if value is not None else None

    def usage(self, code: str, site_id: uuid.UUID | None = None) -> int:
        definition = self.registry.limit(code)
        if definition is None:
            raise ValueError(f"limite inconnue du registre : {code}")
        return definition.counter(self.db, site_id)

    def ensure_capacity(
        self, code: str, additional: int = 1, site_id: uuid.UUID | None = None
    ) -> None:
        """Refuse l'opération si elle dépasserait la limite du plan (de l'abonnement du site
        ``site_id``)."""
        limit = self.limit(code)
        if limit is None:
            return
        if self.usage(code, site_id) + additional > limit:
            extra: dict[str, object] = {"limit": code, "value": limit}
            if site_id is not None:
                extra["site_id"] = str(site_id)
            raise BusinessRuleError(
                "La limite de votre abonnement est atteinte",
                code="plan_limit_reached",
                extra=extra,
            )

    def snapshot(self, site_id: uuid.UUID | None = None) -> dict[str, LimitUsage]:
        return {
            code: LimitUsage(limit=self.limit(code), used=self.usage(code, site_id))
            for code in sorted(self.registry.limit_codes())
        }

    def features(self, effective_modules: set[str] | frozenset[str]) -> frozenset[str]:
        """Fonctionnalités (du plan ou de la licence) dont le module est effectif."""
        return frozenset(
            code
            for code in self.terms.features
            if self.registry.module_of_feature(code) in effective_modules
        )
