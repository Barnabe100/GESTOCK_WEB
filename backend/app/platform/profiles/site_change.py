"""Changement du profil d'activité d'UN site (palier D, ADR-0048).

Un changement de profil est une **reconfiguration du site**, jamais une réinitialisation :
aucune donnée n'est supprimée ni transformée (ventes, stock, mouvements, lots, inventaires,
caisse, assortiment, audit). Seuls changent ``sites.business_profile_code`` du site ciblé et
ses lignes ``site_modules`` ; le profil d'origine du tenant et les autres sites ne bougent pas.

Niveau calculé par le serveur (aperçu, puis de nouveau sous verrou au changement réel) :

- **BLOCKED** : une opération en cours deviendrait impossible — travail ``active`` d'un module
  effectif avant et plus après (session de caisse ouverte vers un profil sans caisse). Refusé,
  aucune confirmation ne le force ;
- **STRONG** : le site a des données commerciales (historique) ou des documents ouverts
  compatibles ; exige la confirmation exacte ``CHANGER DE PROFIL``. L'historique seul ne
  bloque jamais ;
- **SIMPLE** : site vide ou seulement configuré.

Activations (« plus petit changement ») : module conservé par les deux profils → choix
conservé ; retiré du profil → désactivé (ligne conservée) ; ajouté au profil et inclus dans
l'abonnement du site → ``default_enabled`` du nouveau profil ; ajouté hors abonnement → jamais
activé ; un module dont une dépendance est désactivée par ce changement est désactivé à son
tour. Aucune activation implicite, aucune ligne supprimée, aucun autre site lu.

Concurrence : verrou exclusif du site, puis de ses ``site_modules``, puis des verrous déclarés
par les modules qui cessent d'être effectifs (``site_footprint``). L'aperçu porte une empreinte
recalculée sous verrou : un état devenu différent → ``409 profile_preview_outdated``.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, ForbiddenError, NotFoundError
from app.platform.audit.service import record_audit
from app.platform.capabilities.service import CapabilityService
from app.platform.catalog.models import BusinessProfile
from app.platform.context import RequestContext
from app.platform.footprint import COUNT_CAP, SiteFootprint, module_footprint
from app.platform.licensing.service import current_terms
from app.platform.registry import ModuleRegistry
from app.platform.subscriptions.service import site_subscription
from app.platform.tenancy.models import Site, SiteModule
from app.platform.tenancy.site_modules import lock_site, run_site_setup

PROFILE_MANAGE = "organization.profile.manage"
CONFIRMATION_TEXT = "CHANGER DE PROFIL"


class ChangeLevel(StrEnum):
    SIMPLE = "SIMPLE"
    STRONG = "STRONG"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class ModuleState:
    in_profile: bool
    in_plan: bool
    activated: bool
    effective: bool


@dataclass(frozen=True)
class ModuleChange:
    code: str
    status: str
    change: str  # added | removed | kept
    action: str  # enable | disable | none
    reason: str | None
    before: ModuleState
    after: ModuleState


@dataclass(frozen=True)
class FootprintItem:
    kind: str
    module: str
    count: int
    blocking: bool = False

    @property
    def capped(self) -> bool:
        return self.count > COUNT_CAP


@dataclass
class ProfileChangePlan:
    site: Site
    current: BusinessProfile
    target: BusinessProfile
    plan_code: str
    plan_modules: frozenset[str]
    level: ChangeLevel
    modules: list[ModuleChange]
    history: list[FootprintItem]
    open_operations: list[FootprintItem]
    blockers: list[FootprintItem]
    configuration: list[FootprintItem]
    fingerprint: str
    # Activations écrites par le changement : code → (ancienne valeur ou None, nouvelle).
    writes: dict[str, tuple[bool | None, bool]] = field(default_factory=dict)

    @property
    def modules_not_in_plan(self) -> list[str]:
        return sorted(m.code for m in self.modules if m.after.in_profile and not m.after.in_plan)

    def codes(self, *, change: str | None = None, action: str | None = None) -> list[str]:
        return [
            m.code
            for m in self.modules
            if (change is None or m.change == change) and (action is None or m.action == action)
        ]


class ProfileChangeBlockedError(ConflictError):
    """BLOCKED au changement réel : la route journalise le refus dans sa propre transaction."""

    def __init__(self, plan: ProfileChangePlan) -> None:
        super().__init__(
            "Une opération en cours deviendrait impossible avec ce profil : changement refusé",
            code="profile_change_blocked",
            extra={"blockers": [_item_json(b) for b in plan.blockers]},
        )
        self.refusal = {
            "site_id": str(plan.site.id),
            "current_profile": plan.current.code,
            "requested_profile": plan.target.code,
            "level": ChangeLevel.BLOCKED.value,
            "blockers": {f"{b.module}.{b.kind}": b.count for b in plan.blockers},
            "changed": False,
        }


def _item_json(item: FootprintItem) -> dict[str, Any]:
    return {"kind": item.kind, "module": item.module, "count": item.count}


class SiteProfileChangeService:
    def __init__(self, db: Session, ctx: RequestContext, registry: ModuleRegistry) -> None:
        self.db = db
        self.ctx = ctx
        self.registry = registry
        self.capabilities = CapabilityService(db, registry)

    # --- Contrôles ----------------------------------------------------------------------------

    def _site(self, site_id: uuid.UUID) -> Site:
        """Site du tenant (RLS : un autre tenant est introuvable), accessible, actif, avec un
        abonnement, et ``organization.profile.manage`` détenue SUR CE SITE (rôles de
        l'entreprise ou du site, statut de l'abonnement du site). La portée vient de l'URL."""
        site = self.db.get(Site, site_id)
        if site is None:
            raise NotFoundError("Site introuvable", code="site_not_found")
        # Un site désactivé n'est pas accessible (règle existante) ; une désactivation
        # concurrente est revue sous verrou au changement (``409 site_inactive``).
        if site.id not in self.ctx.capabilities.accessible_site_ids or not site.is_active:
            raise ForbiddenError("Accès à ce site refusé", code="site_access_denied")
        if site_subscription(self.db, site.id) is None:
            raise BusinessRuleError("Aucun abonnement", code="subscription_missing")
        capabilities = self.ctx.site_capabilities(site.id)
        if PROFILE_MANAGE not in capabilities.permissions:
            extra = {"site_id": str(site.id)}
            if PROFILE_MANAGE in capabilities.restricted_permissions:
                raise ForbiddenError(
                    "Action indisponible avec le statut de l'abonnement de ce site",
                    code="subscription_restricted",
                    extra=extra,
                )
            raise ForbiddenError(
                "Permission insuffisante sur ce site", code="permission_denied", extra=extra
            )
        return site

    def _target(self, code: str) -> BusinessProfile:
        profile = self.db.get(BusinessProfile, code)
        if profile is None or not profile.is_active:
            raise BusinessRuleError(f"Profil inconnu : {code}", code="unknown_profile")
        return profile

    # --- Calcul -------------------------------------------------------------------------------

    def _plan(self, site: Site, target: BusinessProfile, *, lock: bool) -> ProfileChangePlan:
        current = self.capabilities.site_profile(site.id)
        if current.code == target.code:
            raise BusinessRuleError("Le site a déjà ce profil d'activité", code="profile_unchanged")
        subscription = site_subscription(self.db, site.id)
        if subscription is None:
            raise BusinessRuleError("Aucun abonnement", code="subscription_missing")
        terms = current_terms(self.db, subscription)
        plan_modules = frozenset(terms.modules)
        core = self.registry.core_codes()

        stmt = select(SiteModule).where(SiteModule.site_id == site.id)
        if lock:
            stmt = stmt.with_for_update()
        rows = {r.module_code: r for r in self.db.scalars(stmt)}
        before = {code: row.enabled for code, row in rows.items()}
        after = dict(before)
        reasons: dict[str, str] = {}

        old_in = {m.module_code for m in current.modules}
        defaults = {m.module_code: m.default_enabled for m in target.modules}
        new_in = set(defaults)
        for code in old_in - new_in:
            if after.get(code):
                after[code] = False
                reasons[code] = "removed_from_profile"
        for code in sorted(new_in - old_in):
            if code in core or code not in self.registry:
                continue
            if code in plan_modules:
                after[code] = defaults[code]
                reasons[code] = "default" if defaults[code] else "optional"
            elif after.get(code):
                after[code] = False
                reasons[code] = "not_in_plan"
        # Dépendances : un module activé dont une dépendance est désactivée PAR CE CHANGEMENT
        # est désactivé à son tour (jamais d'activation implicite).
        disabled = {c for c, v in after.items() if before.get(c) and not v}
        changed = True
        while changed:
            changed = False
            for code, enabled in list(after.items()):
                if not enabled or code not in self.registry:
                    continue
                if any(dep in disabled for dep in self.registry.get(code).depends_on):
                    after[code] = False
                    reasons[code] = "dependency"
                    disabled.add(code)
                    changed = True

        activated_before = {c for c, v in before.items() if v}
        activated_after = {c for c, v in after.items() if v}
        effective_before = core | self.registry.resolve_dependencies(
            self.capabilities.offered_modules(current, terms) & activated_before
        )
        effective_after = core | self.registry.resolve_dependencies(
            self.capabilities.offered_modules(target, terms) & activated_after
        )

        modules: list[ModuleChange] = []
        for manifest in self.registry.all():
            code = manifest.code
            if manifest.core or (code not in old_in and code not in new_in):
                continue
            was, now = bool(before.get(code)), bool(after.get(code))
            action = "enable" if now and not was else "disable" if was and not now else "none"
            reason = reasons.get(code)
            if reason is None and code in new_in and code not in plan_modules:
                reason = "not_in_plan"
            modules.append(
                ModuleChange(
                    code=code,
                    status=manifest.status.value,
                    change=(
                        "kept"
                        if code in old_in and code in new_in
                        else "removed"
                        if code in old_in
                        else "added"
                    ),
                    action=action,
                    reason=reason,
                    before=ModuleState(
                        in_profile=code in old_in,
                        in_plan=code in plan_modules,
                        activated=was,
                        effective=code in effective_before,
                    ),
                    after=ModuleState(
                        in_profile=code in new_in,
                        in_plan=code in plan_modules,
                        activated=now,
                        effective=code in effective_after,
                    ),
                )
            )

        # Empreinte du site : chaque module compte ses propres tables. Les modules qui cessent
        # d'être effectifs verrouillent ce qui empêche un nouveau travail en cours.
        stopping = (effective_before - effective_after) - core
        history: list[FootprintItem] = []
        open_ops: list[FootprintItem] = []
        blockers: list[FootprintItem] = []
        configuration: list[FootprintItem] = []
        documents_open = False
        for manifest in self.registry.all():
            if manifest.site_footprint is None:
                continue
            code = manifest.code
            fp: SiteFootprint = module_footprint(
                self.registry, self.db, code, site.id, lock=lock and code in stopping
            )
            history += [FootprintItem(k, code, n) for k, n in fp.history.items()]
            open_ops += [FootprintItem(k, code, n) for k, n in fp.open.items()]
            documents_open = documents_open or bool(fp.open)
            configuration += [FootprintItem(k, code, n) for k, n in fp.info.items()]
            for kind, count in fp.active.items():
                if code in stopping:
                    blockers.append(FootprintItem(kind, code, count, blocking=True))
                else:
                    # Travail en cours qui reste possible : affiché, sans élever le niveau.
                    open_ops.append(FootprintItem(kind, code, count))
        # STRONG : données commerciales ou documents ouverts ; un travail en cours resté
        # possible (``active`` d'un module conservé) n'élève pas le niveau à lui seul.
        strong = bool(history) or documents_open
        level = (
            ChangeLevel.BLOCKED
            if blockers
            else ChangeLevel.STRONG
            if strong
            else ChangeLevel.SIMPLE
        )
        writes = {
            code: (before.get(code), value)
            for code, value in after.items()
            if before.get(code) is None or before[code] != value
        }
        fingerprint = _fingerprint(
            site=site,
            current=current.code,
            target=target.code,
            level=level,
            plan_modules=plan_modules,
            modules=modules,
            writes=writes,
            blockers=blockers,
        )
        return ProfileChangePlan(
            site=site,
            current=current,
            target=target,
            plan_code=subscription.plan_code,
            plan_modules=plan_modules,
            level=level,
            modules=modules,
            history=history,
            open_operations=open_ops,
            blockers=blockers,
            configuration=configuration,
            fingerprint=fingerprint,
            writes=writes,
        )

    # --- API ----------------------------------------------------------------------------------

    def preview(self, site_id: uuid.UUID, code: str) -> ProfileChangePlan:
        """Aperçu : aucune écriture, aucun verrou, aucun audit."""
        site = self._site(site_id)
        return self._plan(site, self._target(code), lock=False)

    def change(
        self,
        site_id: uuid.UUID,
        code: str,
        fingerprint: str,
        confirmation: str | None,
    ) -> ProfileChangePlan:
        site = self._site(site_id)
        target = self._target(code)
        locked = lock_site(self.db, site.id)
        if locked is None or not locked.is_active:
            raise ConflictError("Ce site est désactivé", code="site_inactive")
        if self.capabilities.site_profile(site.id).code == target.code:
            # Changement concurrent déjà appliqué (ou rejeu) : l'aperçu ne vaut plus.
            raise ConflictError(
                "Le profil du site a changé depuis l'aperçu", code="profile_preview_outdated"
            )
        plan = self._plan(locked, target, lock=True)
        if plan.fingerprint != fingerprint:
            raise ConflictError(
                "La situation du site a changé depuis l'aperçu : relancez l'aperçu",
                code="profile_preview_outdated",
            )
        if plan.level is ChangeLevel.BLOCKED:
            raise ProfileChangeBlockedError(plan)
        if plan.level is ChangeLevel.STRONG:
            if not confirmation:
                raise BusinessRuleError(
                    f"Confirmez en saisissant « {CONFIRMATION_TEXT} »",
                    code="profile_change_confirmation_required",
                )
            if confirmation != CONFIRMATION_TEXT:
                raise BusinessRuleError(
                    f"Saisissez exactement « {CONFIRMATION_TEXT} »",
                    code="profile_change_confirmation_invalid",
                )
        self._apply(plan, confirmed=plan.level is ChangeLevel.STRONG)
        return plan

    def _apply(self, plan: ProfileChangePlan, *, confirmed: bool) -> None:
        site = plan.site
        site.business_profile_code = plan.target.code
        rows = {
            r.module_code: r
            for r in self.db.scalars(select(SiteModule).where(SiteModule.site_id == site.id))
        }
        for code, (_previous, enabled) in sorted(plan.writes.items()):
            row = rows.get(code)
            if row is None:
                self.db.add(
                    SiteModule(
                        tenant_id=self.ctx.tenant_id,
                        site_id=site.id,
                        module_code=code,
                        enabled=enabled,
                    )
                )
            else:
                row.enabled = enabled
        self.db.flush()
        # Palier R2-A : les modules activés par le changement sont initialisés sur le site
        # (``site_setup``, idempotent) ; ce qui existe déjà est conservé, jamais écrasé.
        run_site_setup(
            self.db,
            self.registry,
            self.ctx.tenant_id,
            site.id,
            plan.target,
            {code for code, (_previous, enabled) in plan.writes.items() if enabled},
        )
        reasons = {m.code: m.reason for m in plan.modules}
        record_audit(
            self.db,
            action="site.profile_changed",
            tenant_id=self.ctx.tenant_id,
            user_id=self.ctx.user.id,
            site_id=site.id,
            entity_type="site",
            entity_id=site.id,
            data={
                "site_id": str(site.id),
                "previous_profile": plan.current.code,
                "profile": plan.target.code,
                "previous_sector": plan.current.sector_code,
                "sector": plan.target.sector_code,
                "level": plan.level.value,
                "confirmed": confirmed,
                "fingerprint": plan.fingerprint,
                "plan": plan.plan_code,
                "modules": {
                    "added": plan.codes(change="added"),
                    "removed": plan.codes(change="removed"),
                    "kept": plan.codes(change="kept"),
                    "not_in_plan": plan.modules_not_in_plan,
                },
                "site_modules": [
                    {
                        "code": code,
                        "previous": previous,
                        "enabled": enabled,
                        "reason": reasons.get(code),
                    }
                    for code, (previous, enabled) in sorted(plan.writes.items())
                ],
                "history": {f"{i.module}.{i.kind}": i.count for i in plan.history},
                "open_operations": {f"{i.module}.{i.kind}": i.count for i in plan.open_operations},
            },
            meta=self.ctx.meta,
        )

    def record_refusal(self, refusal: dict[str, Any]) -> None:
        """Journal d'un refus BLOCKED (écrit par la route dans sa propre transaction) : il ne
        prétend jamais qu'un changement a eu lieu."""
        record_audit(
            self.db,
            action="site.profile_change_refused",
            tenant_id=self.ctx.tenant_id,
            user_id=self.ctx.user.id,
            site_id=uuid.UUID(refusal["site_id"]),
            entity_type="site",
            entity_id=uuid.UUID(refusal["site_id"]),
            data=refusal,
            meta=self.ctx.meta,
        )


def _fingerprint(
    *,
    site: Site,
    current: str,
    target: str,
    level: ChangeLevel,
    plan_modules: frozenset[str],
    modules: list[ModuleChange],
    writes: dict[str, tuple[bool | None, bool]],
    blockers: list[FootprintItem],
) -> str:
    """Empreinte des éléments décisifs de l'aperçu (sans les compteurs d'historique : une
    nouvelle vente ne périme pas un aperçu déjà STRONG, un site devenu STRONG le périme). Elle
    n'autorise rien : tout est recalculé sous verrou."""
    payload = {
        "site": str(site.id),
        "from": current,
        "to": target,
        "level": level.value,
        "plan": sorted(plan_modules),
        "modules": [[m.code, m.change, m.action, m.after.effective] for m in modules],
        "writes": sorted([c, prev, now] for c, (prev, now) in writes.items()),
        "blockers": sorted(f"{b.module}.{b.kind}" for b in blockers),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()
