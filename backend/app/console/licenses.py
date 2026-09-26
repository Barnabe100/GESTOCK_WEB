"""Licences des sites, générées, révoquées et réémises par TechNova (Phase 3.3-B2, ADR-0034).

Chaîne : PLAN → SUBSCRIPTION (du site) → PAYMENT **CONFIRMED** → LICENCE → activation.

- **Génération** depuis un paiement confirmé (jamais sans) : période calculée par le serveur
  (lendemain de la couverture en cours, sinon aujourd'hui, dans le fuseau de l'entreprise ;
  durée = période de facturation de l'abonnement), plan, modules, fonctionnalités et limites
  figés, nombre de postes proposé = celui demandé à la souscription, confirmé ou ajusté par
  TechNova (arbitrage Q3). Le payload est signé par le **Signing Service** (la console ne
  détient aucune clé privée), la signature est vérifiée avec le trousseau public, puis la
  licence est enregistrée et l'abonnement du site aligné (``sync_subscription``), dans une
  seule transaction ; aucune licence n'est jamais importée depuis un navigateur.
- **Révocation** : ``ISSUED`` → ``REVOKED``, définitive, jamais restaurée (arbitrage Q5).
- **Réémission** : nouveau cycle explicite — l'ancienne licence est révoquée (si elle ne l'est
  pas déjà) et une **nouvelle** licence est émise (nouveau numéro, version + 1, même paiement,
  même période : aucun jour ajouté ni perdu ; postes conservés sauf changement explicite).

Verrous : ligne de l'abonnement du site, puis ligne de la licence (même ordre partout). Double
audit (plateforme + miroir de l'entreprise) dans la transaction de l'action.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, func, or_, select, text
from sqlalchemy.orm import Session, aliased

from app.console.audit import PlatformActor, record_tenant_action
from app.console.signing import SigningClient
from app.core.errors import ConflictError, NotFoundError, ServiceUnavailableError
from app.platform.audit.service import RequestMeta
from app.platform.catalog.models import Plan
from app.platform.identity.models import User
from app.platform.licensing.canonical import canonical_bytes
from app.platform.licensing.models import (
    LICENSE_NUMBER_SEQUENCE,
    License,
    LicenseState,
    LicenseStatus,
)
from app.platform.licensing.service import (
    local_day,
    local_midnight,
    next_valid_from,
    period_last_day,
    subscription_licenses,
    sync_subscription,
)
from app.platform.registry import ModuleRegistry
from app.platform.subscriptions.models import (
    Subscription,
    SubscriptionPayment,
    SubscriptionPaymentStatus,
    SubscriptionStatus,
)
from app.platform.tenancy.models import Site, Tenant, TenantStatus
from app.shared.ids import new_id
from app.shared.pagination import PageParams, apply_sort, escape_like, paginate_rows

NOT_FOUND = "license_not_found"
# Produits autorisés à exploiter la licence (le Web n'active jamais de navigateur : 3.3-B3).
COMPATIBILITY = {"products": ["stockmanager-desktop", "stockmanager-web"]}
# Abonnements qui ne peuvent plus recevoir de licence.
NOT_LICENSABLE = (SubscriptionStatus.CANCELLED,)

Issuer = aliased(User)
Revoker = aliased(User)


@dataclass(frozen=True)
class LicenseFilters:
    tenant_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    plan_code: str | None = None
    state: LicenseState | None = None
    search: str | None = None


@dataclass(frozen=True)
class Proposal:
    """Ce que la génération produirait (écran de confirmation de la console)."""

    payment: SubscriptionPayment
    subscription: Subscription
    tenant_name: str
    site_name: str | None
    timezone: str
    valid_from: date
    valid_until: date
    max_activations: int
    blocking: str | None
    existing_license_id: uuid.UUID | None


def state_condition(state: LicenseState, now: datetime) -> ColumnElement[bool]:
    issued = License.status == LicenseStatus.ISSUED
    if state is LicenseState.REVOKED:
        return License.status == LicenseStatus.REVOKED
    if state is LicenseState.NOT_YET_VALID:
        return and_(issued, License.starts_at > now)
    if state is LicenseState.EXPIRED:
        return and_(issued, License.ends_at <= now)
    return and_(issued, License.starts_at <= now, License.ends_at > now)


def license_document(license: License) -> dict[str, Any]:
    """Document ``.lic`` v1 tel que signé (reconstruit à l'identique des colonnes stockées)."""
    return {
        "format": "stockmanager-license",
        "version": 1,
        "key_id": license.key_id,
        "payload": license.payload,
        "signature": license.signature,
    }


class LicenseAdminService:
    def __init__(
        self,
        db: Session,
        registry: ModuleRegistry,
        now: datetime,
        signer: SigningClient | None = None,
    ) -> None:
        self.db = db
        self.registry = registry
        self.now = now
        self.signer = signer

    # --- Lecture -------------------------------------------------------------------------------

    def _base(self) -> Select[Any]:
        successor = aliased(License)
        return (
            select(
                License,
                Tenant.name.label("tenant_name"),
                Site.name.label("site_name"),
                Site.code.label("site_code"),
                Issuer.email.label("issued_by_email"),
                Revoker.email.label("revoked_by_email"),
                successor.id.label("superseded_by_id"),
            )
            .select_from(License)
            .join(Tenant, Tenant.id == License.tenant_id)
            .join(Site, Site.id == License.site_id)
            .outerjoin(Issuer, Issuer.id == License.issued_by)
            .outerjoin(Revoker, Revoker.id == License.revoked_by)
            .outerjoin(successor, successor.supersedes_id == License.id)
        )

    def search(self, filters: LicenseFilters, params: PageParams) -> tuple[list[Any], int]:
        stmt = self._base()
        if filters.tenant_id:
            stmt = stmt.where(License.tenant_id == filters.tenant_id)
        if filters.site_id:
            stmt = stmt.where(License.site_id == filters.site_id)
        if filters.plan_code:
            stmt = stmt.where(License.plan_code == filters.plan_code)
        if filters.state:
            stmt = stmt.where(state_condition(filters.state, self.now))
        if filters.search:
            pattern = f"%{escape_like(filters.search.strip())}%"
            stmt = stmt.where(
                or_(
                    License.license_number.ilike(pattern, escape="\\"),
                    Tenant.name.ilike(pattern, escape="\\"),
                )
            )
        stmt = apply_sort(
            stmt,
            params.sort,
            {
                "issued_at": License.issued_at,
                "valid_until": License.valid_until,
                "license_number": License.license_number,
            },
            default="-issued_at",
            tiebreaker=License.id,
        )
        return paginate_rows(self.db, stmt, params)

    def row(self, license_id: uuid.UUID) -> Any:
        found = self.db.execute(self._base().where(License.id == license_id)).one_or_none()
        if found is None:
            raise NotFoundError("Licence introuvable", code=NOT_FOUND)
        return found

    def licenses_of_tenant(self, tenant_id: uuid.UUID) -> list[License]:
        return list(
            self.db.scalars(
                select(License)
                .where(License.tenant_id == tenant_id)
                .order_by(License.starts_at, License.license_version)
            )
        )

    def document(self, license_id: uuid.UUID) -> tuple[License, dict[str, Any]]:
        license = self.row(license_id).License
        if license.status is LicenseStatus.REVOKED:
            raise ConflictError("Licence révoquée", code="license_revoked")
        return license, license_document(license)

    # --- Contexte d'une génération --------------------------------------------------------------

    def _payment(self, payment_id: uuid.UUID) -> SubscriptionPayment:
        payment = self.db.get(SubscriptionPayment, payment_id, populate_existing=True)
        if payment is None:
            raise NotFoundError("Paiement introuvable", code="subscription_payment_not_found")
        return payment

    def _tenant(self, tenant_id: uuid.UUID) -> Any:
        return self.db.execute(
            select(Tenant.id, Tenant.name, Tenant.status, Tenant.timezone).where(
                Tenant.id == tenant_id
            )
        ).one()

    def _lock_subscription(self, subscription_id: uuid.UUID) -> Subscription:
        subscription = self.db.scalars(
            select(Subscription)
            .where(Subscription.id == subscription_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if subscription is None:
            raise NotFoundError("Abonnement introuvable", code="subscription_missing")
        return subscription

    def _licenses_of_payment(self, payment_id: uuid.UUID) -> list[License]:
        return list(
            self.db.scalars(
                select(License)
                .where(License.payment_id == payment_id)
                .order_by(License.license_version)
                .execution_options(populate_existing=True)
            )
        )

    def _blocking(
        self, payment: SubscriptionPayment, subscription: Subscription, tenant: Any
    ) -> str | None:
        if payment.status is not SubscriptionPaymentStatus.CONFIRMED:
            return "payment_not_confirmed"
        if subscription.site_id is None:
            return "subscription_site_required"
        if tenant.status is not TenantStatus.ACTIVE:
            return "tenant_suspended"
        if subscription.status in NOT_LICENSABLE:
            return "subscription_not_licensable"
        if self._licenses_of_payment(payment.id):
            return "license_already_issued"
        return None

    def proposal(self, payment_id: uuid.UUID) -> Proposal:
        payment = self._payment(payment_id)
        subscription = self.db.get(Subscription, payment.subscription_id)
        assert subscription is not None
        tenant = self._tenant(payment.tenant_id)
        today = local_day(self.now, tenant.timezone)
        valid_from = next_valid_from(subscription_licenses(self.db, subscription.id), today)
        existing = self._licenses_of_payment(payment.id)
        # Colonnes autorisées au rôle de la console seulement (jamais l'entité complète).
        site_name = (
            self.db.scalar(select(Site.name).where(Site.id == subscription.site_id))
            if subscription.site_id
            else None
        )
        return Proposal(
            payment=payment,
            subscription=subscription,
            tenant_name=tenant.name,
            site_name=site_name,
            timezone=tenant.timezone,
            valid_from=valid_from,
            valid_until=period_last_day(valid_from, subscription.billing_period),
            max_activations=subscription.requested_activations,
            blocking=self._blocking(payment, subscription, tenant),
            existing_license_id=existing[-1].id if existing else None,
        )

    # --- Émission ------------------------------------------------------------------------------

    def _next_number(self) -> str:
        value = self.db.scalar(text(f"SELECT nextval('{LICENSE_NUMBER_SEQUENCE}')"))
        return f"LIC-{self.now.year:04d}-{int(value or 0):05d}"

    def _payload(
        self,
        *,
        license_id: uuid.UUID,
        number: str,
        version: int,
        supersedes: License | None,
        subscription: Subscription,
        payment_id: uuid.UUID,
        timezone: str,
        valid_from: date,
        valid_until: date,
        max_activations: int,
    ) -> dict[str, Any]:
        plan = self.db.get(Plan, subscription.plan_code)
        assert plan is not None and subscription.site_id is not None
        return {
            "license_id": str(license_id),
            "license_number": number,
            "license_version": version,
            "supersedes_id": str(supersedes.id) if supersedes else None,
            "tenant_id": str(subscription.tenant_id),
            "site_id": str(subscription.site_id),
            "subscription_id": str(subscription.id),
            "payment_id": str(payment_id),
            "plan": plan.code,
            "billing_period": subscription.billing_period.value,
            "issued_at": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timezone": timezone,
            "valid_from": valid_from.isoformat(),
            "valid_until": valid_until.isoformat(),
            "max_activations": max_activations,
            "modules": sorted({m.module_code for m in plan.modules}),
            "features": sorted(set(plan.features)),
            "limits": {
                code: (int(plan.limits[code]) if plan.limits.get(code) is not None else None)
                for code in sorted(self.registry.limit_codes())
            },
            "compatibility": COMPATIBILITY,
        }

    def _sign(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.signer is None:
            raise ServiceUnavailableError(
                "Service de signature non configuré", code="signing_service_unavailable"
            )
        return self.signer.sign(payload)

    def _issue(
        self,
        *,
        subscription: Subscription,
        payment_id: uuid.UUID,
        timezone: str,
        valid_from: date,
        valid_until: date,
        max_activations: int,
        actor: PlatformActor,
        supersedes: License | None = None,
    ) -> tuple[License, dict[str, Any]]:
        """Signe puis construit la licence (non encore ajoutée à la session) : la signature
        précède toute écriture ; un échec du Signing Service ne laisse aucune trace."""
        license_id = new_id()
        number = self._next_number()
        payload = self._payload(
            license_id=license_id,
            number=number,
            version=supersedes.license_version + 1 if supersedes else 1,
            supersedes=supersedes,
            subscription=subscription,
            payment_id=payment_id,
            timezone=timezone,
            valid_from=valid_from,
            valid_until=valid_until,
            max_activations=max_activations,
        )
        document = self._sign(payload)
        assert subscription.site_id is not None
        license = License(
            id=license_id,
            tenant_id=subscription.tenant_id,
            site_id=subscription.site_id,
            subscription_id=subscription.id,
            payment_id=payment_id,
            license_number=number,
            license_version=payload["license_version"],
            supersedes_id=supersedes.id if supersedes else None,
            plan_code=payload["plan"],
            billing_period=subscription.billing_period,
            valid_from=valid_from,
            valid_until=valid_until,
            timezone=timezone,
            starts_at=local_midnight(valid_from, timezone),
            ends_at=local_midnight(date.fromordinal(valid_until.toordinal() + 1), timezone),
            max_activations=max_activations,
            modules=payload["modules"],
            features=payload["features"],
            limits=payload["limits"],
            issued_at=self.now,
            issued_by=actor.user_id,
            key_id=document["key_id"],
            payload=payload,
            signature=document["signature"],
            payload_sha256=hashlib.sha256(canonical_bytes(payload)).hexdigest(),
            status=LicenseStatus.ISSUED,
        )
        return license, document

    @staticmethod
    def _period(subscription: Subscription) -> dict[str, Any]:
        return {
            "status": subscription.status.value,
            "current_period_start": subscription.current_period_start.isoformat(),
            "current_period_end": subscription.current_period_end.isoformat(),
        }

    @staticmethod
    def _license_data(license: License) -> dict[str, Any]:
        return {
            "license_number": license.license_number,
            "license_version": license.license_version,
            "site_id": license.site_id,
            "subscription_id": license.subscription_id,
            "payment_id": license.payment_id,
            "plan": license.plan_code,
            "valid_from": license.valid_from.isoformat(),
            "valid_until": license.valid_until.isoformat(),
            "max_activations": license.max_activations,
            "key_id": license.key_id,
            "payload_sha256": license.payload_sha256,
        }

    def _refuse(self, code: str, **extra: Any) -> ConflictError:
        messages = {
            "payment_not_confirmed": "Le paiement n'est pas confirmé",
            "subscription_site_required": "L'abonnement n'est rattaché à aucun site",
            "tenant_suspended": "L'entreprise est suspendue",
            "subscription_not_licensable": "Cet abonnement ne peut plus recevoir de licence",
            "license_already_issued": "Une licence a déjà été générée pour ce paiement",
        }
        return ConflictError(messages.get(code, code), code=code, extra=extra)

    def generate(
        self,
        payment_id: uuid.UUID,
        *,
        max_activations: int,
        reason: str,
        actor: PlatformActor,
        meta: RequestMeta,
    ) -> License:
        payment = self._payment(payment_id)
        subscription = self._lock_subscription(payment.subscription_id)
        payment = self._payment(payment_id)
        tenant = self._tenant(payment.tenant_id)
        blocking = self._blocking(payment, subscription, tenant)
        if blocking is not None:
            existing = self._licenses_of_payment(payment.id)
            extra = {"license_id": str(existing[-1].id)} if existing else {}
            raise self._refuse(blocking, **extra)
        licenses = subscription_licenses(self.db, subscription.id)
        valid_from = next_valid_from(licenses, local_day(self.now, tenant.timezone))
        license, _ = self._issue(
            subscription=subscription,
            payment_id=payment.id,
            timezone=tenant.timezone,
            valid_from=valid_from,
            valid_until=period_last_day(valid_from, subscription.billing_period),
            max_activations=max_activations,
            actor=actor,
        )
        before = self._period(subscription)
        self.db.add(license)
        self.db.flush()
        sync_subscription(
            subscription, [*licenses, license], timezone=tenant.timezone, now=self.now
        )
        self.db.flush()
        record_tenant_action(
            self.db,
            actor=actor,
            action="license.generated",
            tenant_id=license.tenant_id,
            target_type="license",
            target_id=license.id,
            before=before,
            after=self._period(subscription),
            reason=reason,
            data=self._license_data(license)
            | {"requested_activations": subscription.requested_activations},
            meta=meta,
        )
        return license

    def _lock_license(self, license_id: uuid.UUID) -> tuple[Subscription, License | None]:
        """Verrou de l'abonnement puis de la licence. La politique RLS de révocation n'expose au
        verrou que les licences ``ISSUED`` : ``None`` pour une licence déjà révoquée."""
        found = self.db.get(License, license_id)
        if found is None:
            raise NotFoundError("Licence introuvable", code=NOT_FOUND)
        subscription = self._lock_subscription(found.subscription_id)
        locked = self.db.scalars(
            select(License)
            .where(License.id == license_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if locked is None:
            self.db.refresh(found)
        return subscription, locked

    def _revoke_row(self, license: License, reason: str, actor: PlatformActor) -> None:
        license.status = LicenseStatus.REVOKED
        license.revoked_at = self.now
        license.revoked_by = actor.user_id
        license.revocation_reason = reason

    def revoke(
        self, license_id: uuid.UUID, reason: str, actor: PlatformActor, meta: RequestMeta
    ) -> License:
        subscription, license = self._lock_license(license_id)
        if license is None or license.status is not LicenseStatus.ISSUED:
            raise ConflictError("Licence déjà révoquée", code="license_already_revoked")
        tenant = self._tenant(license.tenant_id)
        before = self._period(subscription)
        self._revoke_row(license, reason, actor)
        self.db.flush()
        sync_subscription(
            subscription,
            subscription_licenses(self.db, subscription.id),
            timezone=tenant.timezone,
            now=self.now,
            revoked=license,
        )
        self.db.flush()
        record_tenant_action(
            self.db,
            actor=actor,
            action="license.revoked",
            tenant_id=license.tenant_id,
            target_type="license",
            target_id=license.id,
            before={"license_status": LicenseStatus.ISSUED.value} | before,
            after={"license_status": LicenseStatus.REVOKED.value} | self._period(subscription),
            reason=reason,
            data=self._license_data(license),
            meta=meta,
        )
        return license

    def reissue(
        self,
        license_id: uuid.UUID,
        *,
        reason: str,
        max_activations: int | None,
        actor: PlatformActor,
        meta: RequestMeta,
    ) -> License:
        subscription, locked = self._lock_license(license_id)
        old = locked or self.db.get(License, license_id, populate_existing=True)
        assert old is not None
        tenant = self._tenant(old.tenant_id)
        if tenant.status is not TenantStatus.ACTIVE:
            raise self._refuse("tenant_suspended")
        if self.db.scalar(
            select(func.count()).select_from(License).where(License.supersedes_id == old.id)
        ):
            raise ConflictError("Licence déjà réémise", code="license_already_reissued")
        others = [
            lic
            for lic in self._licenses_of_payment(old.payment_id)
            if lic.id != old.id and lic.status is LicenseStatus.ISSUED
        ]
        if others:
            raise self._refuse("license_already_issued", license_id=str(others[0].id))
        if old.valid_until < local_day(self.now, tenant.timezone):
            raise ConflictError(
                "Licence expirée : elle ne peut être réémise", code="license_expired"
            )
        new, _ = self._issue(
            subscription=subscription,
            payment_id=old.payment_id,
            timezone=old.timezone,
            valid_from=old.valid_from,
            valid_until=old.valid_until,
            max_activations=max_activations or old.max_activations,
            actor=actor,
            supersedes=old,
        )
        before = self._period(subscription)
        was_issued = old.status is LicenseStatus.ISSUED
        if was_issued:
            self._revoke_row(old, reason, actor)
            self.db.flush()
        self.db.add(new)
        self.db.flush()
        sync_subscription(
            subscription,
            subscription_licenses(self.db, subscription.id),
            timezone=tenant.timezone,
            now=self.now,
        )
        self.db.flush()
        record_tenant_action(
            self.db,
            actor=actor,
            action="license.reissued",
            tenant_id=new.tenant_id,
            target_type="license",
            target_id=new.id,
            before={"license_id": old.id, "license_number": old.license_number} | before,
            after={"license_id": new.id, "license_number": new.license_number}
            | self._period(subscription),
            reason=reason,
            data=self._license_data(new)
            | {
                "supersedes_id": old.id,
                "previous_max_activations": old.max_activations,
                "previous_revoked_now": was_issued,
            },
            meta=meta,
        )
        return new
