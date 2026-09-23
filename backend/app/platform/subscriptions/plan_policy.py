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

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError
from app.platform.catalog.models import Plan
from app.platform.registry import ModuleRegistry


@dataclass(frozen=True)
class LimitUsage:
    limit: int | None  # None = illimité
    used: int


class PlanPolicy:
    def __init__(self, db: Session, plan: Plan, registry: ModuleRegistry) -> None:
        self.db = db
        self.plan = plan
        self.registry = registry

    def limit(self, code: str) -> int | None:
        if self.registry.limit(code) is None:
            raise ValueError(f"limite inconnue du registre : {code}")
        value = self.plan.limits.get(code)
        return int(value) if value is not None else None

    def usage(self, code: str) -> int:
        definition = self.registry.limit(code)
        if definition is None:
            raise ValueError(f"limite inconnue du registre : {code}")
        return definition.counter(self.db)

    def ensure_capacity(self, code: str, additional: int = 1) -> None:
        """Refuse l'opération si elle dépasserait la limite du plan."""
        limit = self.limit(code)
        if limit is None:
            return
        if self.usage(code) + additional > limit:
            raise BusinessRuleError(
                "La limite de votre abonnement est atteinte",
                code="plan_limit_reached",
                extra={"limit": code, "value": limit},
            )

    def snapshot(self) -> dict[str, LimitUsage]:
        return {
            code: LimitUsage(limit=self.limit(code), used=self.usage(code))
            for code in sorted(self.registry.limit_codes())
        }

    def features(self, effective_modules: set[str] | frozenset[str]) -> frozenset[str]:
        """Fonctionnalités du plan dont le module est effectif pour le tenant."""
        return frozenset(
            code
            for code in self.plan.features
            if self.registry.module_of_feature(code) in effective_modules
        )
