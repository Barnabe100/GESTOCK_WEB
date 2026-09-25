"""Onboarding persistant d'un tenant (Phase 3.2-B, ADR-0026).

- Les étapes sont déclarées par les manifestes des modules (registre) ; seules celles des
  modules effectifs, et applicables, sont proposées.
- Les lignes ``onboarding_steps`` sont créées au premier accès (ou à l'inscription), de façon
  idempotente (``ON CONFLICT DO NOTHING``) ; aucune migration n'écrit d'état.
- ``refresh`` évalue chaque règle sur les données réelles et fait **seulement avancer** le
  statut persistant (``NOT_STARTED`` → ``IN_PROGRESS`` → ``COMPLETED``) ; ``COMPLETED`` est
  définitif. La complétion est horodatée, attribuée si l'acteur est connu, et auditée.
- Le client ne peut jamais déclarer une étape terminée : seule la transition manuelle
  ``NOT_STARTED`` → ``IN_PROGRESS`` (« je commence cette étape ») est acceptée.
- Onboarding ≠ activation : terminer l'onboarding ne modifie jamais l'abonnement.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Table, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, NotFoundError
from app.platform.audit.service import RequestMeta, record_audit
from app.platform.context import RequestContext
from app.platform.onboarding.definitions import OnboardingEnv, OnboardingStatus, OnboardingStepDef
from app.platform.onboarding.models import OnboardingStep
from app.platform.onboarding.schemas import (
    OnboardingActionOut,
    OnboardingOut,
    OnboardingProgress,
    OnboardingStepOut,
)
from app.platform.registry import ModuleRegistry
from app.platform.subscriptions.plan_policy import PlanPolicy
from app.platform.subscriptions.service import current_plan
from app.platform.tenancy.models import Tenant
from app.shared.clock import utcnow
from app.shared.ids import new_id

ONBOARDING_TABLE: Table = OnboardingStep.__table__  # type: ignore[assignment]


@dataclass(frozen=True)
class Actor:
    """Origine d'une évaluation : utilisateur dont l'action a pu compléter une étape (nul :
    constat par évaluation), déclencheur tracé, métadonnées de requête."""

    user_id: uuid.UUID | None
    trigger: str
    meta: RequestMeta | None = None


class OnboardingService:
    def __init__(self, db: Session, registry: ModuleRegistry) -> None:
        self.db = db
        self.registry = registry

    # --- Évaluation ------------------------------------------------------------------------

    def env(self, tenant: Tenant, modules: frozenset[str]) -> OnboardingEnv:
        policy = PlanPolicy(self.db, current_plan(self.db), self.registry)
        return OnboardingEnv(db=self.db, tenant=tenant, modules=modules, limit=policy.limit)

    def steps(self, env: OnboardingEnv) -> list[OnboardingStepDef]:
        return [
            s
            for s in self.registry.onboarding_steps(env.modules)
            if s.applicable is None or s.applicable(env)
        ]

    def _rows(self, *, lock: bool = False) -> dict[str, OnboardingStep]:
        query = select(OnboardingStep).execution_options(populate_existing=True)
        if lock:
            query = query.order_by(OnboardingStep.step_code).with_for_update()
        return {row.step_code: row for row in self.db.scalars(query)}

    def refresh(
        self, env: OnboardingEnv, actor: Actor, now: datetime | None = None
    ) -> tuple[list[OnboardingStepDef], dict[str, OnboardingStep]]:
        """Crée les étapes manquantes puis applique les progrès constatés (jamais de
        régression). Idempotent ; sûr en concurrence (insertion sans conflit, verrou des
        lignes avant toute progression)."""
        steps = self.steps(env)
        rows = self._rows()
        missing = [s.code for s in steps if s.code not in rows]
        if missing:
            self.db.execute(
                # Table (et non classe ORM) : la colonne s'appelle ``metadata``.
                insert(ONBOARDING_TABLE)
                .values(
                    [
                        {
                            "id": new_id(),
                            "tenant_id": env.tenant.id,
                            "step_code": code,
                            "status": OnboardingStatus.NOT_STARTED.value,
                            "metadata": {},
                        }
                        for code in missing
                    ]
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "step_code"])
            )
            rows = self._rows()
        observed = {s.code: s.evaluate(env) for s in steps}
        if any(observed[s.code].rank > rows[s.code].status.rank for s in steps):
            rows = self._rows(lock=True)
            moment = now or utcnow()
            for s in steps:
                self._advance(rows[s.code], s, observed[s.code], actor, moment)
            self.db.flush()
        return steps, rows

    def _advance(
        self,
        row: OnboardingStep,
        step: OnboardingStepDef,
        target: OnboardingStatus,
        actor: Actor,
        now: datetime,
    ) -> None:
        if target.rank <= row.status.rank:
            return  # jamais de régression ; COMPLETED est définitif
        row.status = target
        if target is OnboardingStatus.COMPLETED:
            row.completed_at = now
            row.completed_by = actor.user_id
            row.details = {**row.details, "trigger": actor.trigger}
            record_audit(
                self.db,
                action="onboarding.step.completed",
                tenant_id=row.tenant_id,
                user_id=actor.user_id,
                entity_type="onboarding_step",
                entity_id=step.code,
                data={"step": step.code, "required": step.required, "trigger": actor.trigger},
                meta=actor.meta,
            )

    # --- Contexte de requête ------------------------------------------------------------

    def _context_env(self, ctx: RequestContext) -> OnboardingEnv:
        return self.env(ctx.tenant, ctx.capabilities.modules)

    def refresh_for(self, ctx: RequestContext, trigger: str) -> None:
        """Après une action pouvant satisfaire une étape (site créé, entreprise modifiée…) :
        l'utilisateur agissant est l'auteur de la complétion."""
        self.refresh(self._context_env(ctx), Actor(ctx.user.id, trigger, ctx.meta))

    def overview(self, ctx: RequestContext) -> OnboardingOut:
        # Constat à la consultation : complétion non attribuée (l'auteur est inconnu).
        steps, rows = self.refresh(self._context_env(ctx), Actor(None, "evaluation", ctx.meta))
        return self._out(ctx, steps, rows)

    def start(self, ctx: RequestContext, code: str, status: OnboardingStatus) -> OnboardingOut:
        """Seule transition manuelle : ``NOT_STARTED`` → ``IN_PROGRESS``. Une étape déjà en
        cours ou terminée reste inchangée (idempotent, jamais de régression)."""
        if status is not OnboardingStatus.IN_PROGRESS:
            raise BusinessRuleError(
                "Une étape est terminée automatiquement lorsque sa condition est remplie",
                code="onboarding_transition_not_allowed",
            )
        steps, rows = self.refresh(self._context_env(ctx), Actor(ctx.user.id, "manual", ctx.meta))
        step = next((s for s in steps if s.code == code), None)
        if step is None:
            raise NotFoundError("Étape introuvable", code="onboarding_step_not_found")
        locked = self._rows(lock=True)[code]
        if locked.status is OnboardingStatus.NOT_STARTED:
            locked.status = OnboardingStatus.IN_PROGRESS
            locked.details = {
                **locked.details,
                "started_by": str(ctx.user.id),
                "started_at": utcnow().isoformat(),
            }
            record_audit(
                self.db,
                action="onboarding.step.started",
                tenant_id=ctx.tenant_id,
                user_id=ctx.user.id,
                entity_type="onboarding_step",
                entity_id=code,
                data={"step": code},
                meta=ctx.meta,
            )
            self.db.flush()
        return self._out(ctx, steps, self._rows())

    # --- Présentation ----------------------------------------------------------------------

    def _action(self, ctx: RequestContext, step: OnboardingStepDef) -> OnboardingActionOut | None:
        if step.action is None:
            return None
        permission = step.action.permission
        blocked: str | None = None
        if not ctx.has_permission(permission):
            blocked = (
                "subscription_restricted"
                if permission in ctx.capabilities.restricted_permissions
                else "permission_denied"
            )
        return OnboardingActionOut(
            route=step.action.route,
            label=step.action.label,
            permission=permission,
            available=blocked is None,
            blocked_reason=blocked,
        )

    def _out(
        self,
        ctx: RequestContext,
        steps: list[OnboardingStepDef],
        rows: dict[str, OnboardingStep],
    ) -> OnboardingOut:
        items = [
            OnboardingStepOut(
                code=s.code,
                order=s.order,
                required=s.required,
                title=s.title,
                description=s.description,
                status=rows[s.code].status,
                completed_at=rows[s.code].completed_at,
                action=self._action(ctx, s),
            )
            for s in steps
        ]
        return build_overview(items, ctx.capabilities.subscription_status.value)


def build_overview(items: list[OnboardingStepOut], subscription_status: str) -> OnboardingOut:
    """Règles de progression (déterministes, uniques : le frontend ne recalcule rien).

    - pourcentage = étapes ``COMPLETED`` / étapes proposées (arrondi à l'entier inférieur :
      100 % seulement si tout est terminé) ;
    - onboarding **terminé** = toutes les étapes obligatoires ``COMPLETED`` (les étapes
      recommandées non faites ne bloquent pas, mais ne comptent pas comme progression) ;
    - étape actuelle = première obligatoire non terminée, sinon première recommandée non
      terminée, sinon aucune.
    """
    done = [i for i in items if i.status is OnboardingStatus.COMPLETED]
    required = [i for i in items if i.required]
    required_done = [i for i in required if i.status is OnboardingStatus.COMPLETED]
    finished = len(required_done) == len(required)
    if finished:
        status = OnboardingStatus.COMPLETED
    elif any(i.status is not OnboardingStatus.NOT_STARTED for i in items):
        status = OnboardingStatus.IN_PROGRESS
    else:
        status = OnboardingStatus.NOT_STARTED
    pending = [i for i in items if i.status is not OnboardingStatus.COMPLETED]
    current = next((i for i in pending if i.required), None) or next(iter(pending), None)
    total = len(items)
    return OnboardingOut(
        status=status,
        completed=finished,
        progress=OnboardingProgress(
            completed=len(done),
            total=total,
            percentage=(len(done) * 100 // total) if total else 100,
            required_completed=len(required_done),
            required_total=len(required),
        ),
        current_step=current.code if current else None,
        next_action=current.action if current else None,
        subscription_status=subscription_status,
        steps=items,
    )
