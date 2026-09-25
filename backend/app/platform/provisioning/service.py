import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import set_db_context
from app.core.errors import BusinessRuleError, ConflictError
from app.core.security import hash_password
from app.platform.access.models import MembershipRole, Role, TenantMembership
from app.platform.audit.service import RequestMeta, record_audit
from app.platform.catalog.loader import RoleTemplate
from app.platform.catalog.models import BusinessProfile, GeoCountry, Plan
from app.platform.identity.models import User
from app.platform.identity.passwords import normalize_email, validate_new_password
from app.platform.registry import ModuleRegistry
from app.platform.subscriptions.models import BillingPeriod, Subscription, SubscriptionStatus
from app.platform.subscriptions.service import period_end
from app.platform.tenancy.models import Site, SiteKind, Tenant, TenantModule
from app.shared.ids import new_id

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class SubscriptionStart(StrEnum):
    """Démarrage de l'abonnement sans essai : ``IMMEDIATE`` (actif, CLI TechNova) ou
    ``PENDING_ACTIVATION`` (inscription publique : activation après paiement et licence)."""

    IMMEDIATE = "immediate"
    PENDING_ACTIVATION = "pending_activation"


# Informations d'entreprise acceptées à la création (colonnes facultatives de ``tenants``).
COMPANY_FIELDS = (
    "trade_name",
    "email",
    "phone",
    "address",
    "city",
    "region",
    "website",
    "tax_id",
    "trade_register",
    "description",
    "logo_url",
)


@dataclass(frozen=True)
class ProvisionTenantCommand:
    name: str
    slug: str
    profile_code: str
    plan_code: str
    billing_period: BillingPeriod
    owner_email: str
    owner_full_name: str
    # Pays (ISO 3166-1 alpha-2, actif dans le référentiel) : obligatoire pour tout nouveau
    # tenant ; fournit la devise et le fuseau horaire par défaut.
    country_code: str
    # Obligatoire si l'utilisateur n'existe pas encore ; ignoré sinon (jamais écrasé).
    owner_password: str | None = None
    # Renseigné : abonnement en essai pour N jours ; sinon actif pour une période de facturation.
    trial_days: int | None = None
    # Sans essai : actif (CLI) ou en attente d'activation (inscription publique).
    subscription_start: SubscriptionStart = SubscriptionStart.IMMEDIATE
    # Mot de passe choisi par le propriétaire lui-même (inscription) : aucun changement imposé.
    owner_password_is_final: bool = False
    # Site initial créé d'office (CLI) ou laissé à l'onboarding (inscription : étape first_site).
    create_first_site: bool = True
    # Informations d'entreprise (clés de COMPANY_FIELDS), déjà validées par l'appelant.
    company: dict[str, str | None] = field(default_factory=dict)
    first_site_name: str = "Site principal"
    first_site_code: str = "PRINCIPAL"
    first_site_kind: SiteKind = SiteKind.STORE
    # Nuls : devise et fuseau horaire par défaut du pays.
    currency: str | None = None
    locale: str = "fr"
    timezone: str | None = None


@dataclass(frozen=True)
class ProvisionResult:
    tenant_id: uuid.UUID
    # Nul si le site initial est laissé à l'onboarding.
    site_id: uuid.UUID | None
    subscription_id: uuid.UUID
    owner_user_id: uuid.UUID
    owner_created: bool
    enabled_modules: list[str] = field(default_factory=list)


def subscription_price(
    plan: Plan, billing_period: BillingPeriod
) -> tuple[Decimal | None, str | None]:
    """Prix de la période souscrite, à figer dans l'abonnement : celui du plan si TechNova a
    ouvert cette période (prix et devise), sinon aucun."""
    if billing_period is BillingPeriod.MONTHLY and plan.monthly_price_enabled:
        return plan.monthly_price, plan.currency
    if billing_period is BillingPeriod.ANNUAL and plan.annual_price_enabled:
        return plan.annual_price, plan.currency
    return None, None


class _Checked(NamedTuple):
    profile: BusinessProfile
    plan: Plan
    country: GeoCountry
    currency: str
    timezone: str


class TenantProvisioningService:
    """Crée un tenant complet : tenant, abonnement, modules, rôles, premier site, propriétaire.

    N'effectue pas de commit : l'appelant contrôle la transaction. Fonctionne avec le rôle
    applicatif standard (sans BYPASSRLS) en se plaçant dans le contexte RLS du nouveau tenant.
    """

    def __init__(
        self,
        session: Session,
        settings: Settings,
        registry: ModuleRegistry,
        role_templates: dict[str, RoleTemplate],
        now: datetime,
    ) -> None:
        self.db = session
        self.settings = settings
        self.registry = registry
        self.role_templates = role_templates
        self.now = now

    def check(self, cmd: ProvisionTenantCommand) -> "_Checked":
        """Valide la commande sans rien écrire (profil, plan, pays, devise, fuseau…)."""
        self._validate(cmd)
        # Profil actif (donc classé : secteur et profil UX, contrainte en base) dans un secteur
        # actif. Ses modules proposés (déjà résolus depuis le profil UX) sont initialisés
        # ensuite dans les limites du plan : défaut ≠ effectif.
        profile = self.db.get(BusinessProfile, cmd.profile_code)
        if (
            profile is None
            or not profile.is_active
            or profile.sector is None
            or not profile.sector.is_active
        ):
            raise BusinessRuleError(f"Profil inconnu : {cmd.profile_code}", code="unknown_profile")
        plan = self.db.get(Plan, cmd.plan_code)
        if plan is None or not plan.is_active:
            raise BusinessRuleError(f"Plan inconnu : {cmd.plan_code}", code="unknown_plan")
        country, currency, timezone = self._locale_defaults(cmd)
        return _Checked(profile, plan, country, currency, timezone)

    def provision(
        self, cmd: ProvisionTenantCommand, *, actor: str, meta: RequestMeta | None = None
    ) -> ProvisionResult:
        profile, plan, country, currency, timezone = self.check(cmd)

        tenant_id = new_id()
        set_db_context(self.db, tenant_id=tenant_id, user_id=None)

        tenant = Tenant(
            id=tenant_id,
            name=cmd.name.strip(),
            slug=cmd.slug,
            business_profile_code=profile.code,
            country_code=country.code,
            currency=currency,
            locale=cmd.locale,
            timezone=timezone,
            **{key: cmd.company.get(key) for key in COMPANY_FIELDS},
        )
        self.db.add(tenant)
        try:
            self.db.flush()
        except IntegrityError as exc:
            raise ConflictError(
                f"Le slug '{cmd.slug}' est déjà utilisé", code="slug_taken"
            ) from exc

        subscription = self._create_subscription(tenant_id, plan, cmd)
        enabled = self._init_modules(tenant_id, profile, plan)
        admin_role = self._create_roles(tenant_id)
        site: Site | None = None
        if cmd.create_first_site:
            site = Site(
                tenant_id=tenant_id,
                name=cmd.first_site_name,
                code=cmd.first_site_code,
                kind=cmd.first_site_kind,
            )
            self.db.add(site)
        owner, created = self._get_or_create_owner(cmd)
        # Propriétaire (propriété protégée, ``is_owner``) ET administrateur principal (rôle
        # RBAC protégé) : deux notions distinctes ; d'autres administrateurs peuvent exister.
        membership = TenantMembership(
            tenant_id=tenant_id, user_id=owner.id, is_owner=True, all_sites=True
        )
        self.db.add(membership)
        self.db.flush()
        self.db.add(
            MembershipRole(tenant_id=tenant_id, membership_id=membership.id, role_id=admin_role.id)
        )
        self.db.flush()
        for manifest in self.registry.all():
            if manifest.tenant_setup is not None:
                manifest.tenant_setup(self.db, tenant_id)
        self.db.flush()

        record_audit(
            self.db,
            action="tenant.provisioned",
            tenant_id=tenant_id,
            user_id=None,
            entity_type="tenant",
            entity_id=tenant_id,
            data={
                "actor": actor,
                "profile": profile.code,
                "country": tenant.country_code,
                "sector": profile.sector_code,
                "ux_profile": profile.ux_profile_code,
                "plan": plan.code,
                "billing_period": cmd.billing_period.value,
                "owner_email": owner.email,
                "owner_created": created,
                "subscription_status": subscription.status.value,
                "first_site": site is not None,
            },
            meta=meta,
        )
        return ProvisionResult(
            tenant_id=tenant_id,
            site_id=site.id if site else None,
            subscription_id=subscription.id,
            owner_user_id=owner.id,
            owner_created=created,
            enabled_modules=sorted(enabled),
        )

    def _locale_defaults(self, cmd: ProvisionTenantCommand) -> tuple[GeoCountry, str, str]:
        """Pays du référentiel (actif) ; devise et fuseau horaire : ceux demandés, sinon ceux
        du pays. Une devise doit exister dans le référentiel (aucune table de devises)."""
        country = self.db.get(GeoCountry, cmd.country_code.strip().upper())
        if country is None or not country.is_active:
            raise BusinessRuleError(f"Pays inconnu : {cmd.country_code}", code="unknown_country")
        currency = cmd.currency or country.currency
        known = set(self.db.scalars(select(GeoCountry.currency).distinct()))
        if currency is None or currency not in known:
            raise BusinessRuleError("Devise invalide (code ISO 4217)", code="invalid_currency")
        timezone = cmd.timezone or country.timezone
        try:
            if timezone is None:
                raise ValueError(timezone)
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise BusinessRuleError("Fuseau horaire inconnu", code="invalid_timezone") from exc
        return country, currency, timezone

    def _validate(self, cmd: ProvisionTenantCommand) -> None:
        if not cmd.name.strip():
            raise BusinessRuleError("Le nom est obligatoire", code="name_required")
        if not SLUG_RE.match(cmd.slug):
            raise BusinessRuleError(
                "Slug invalide (minuscules, chiffres, tirets ; 63 caractères max)",
                code="invalid_slug",
            )
        if cmd.currency is not None and not CURRENCY_RE.match(cmd.currency):
            raise BusinessRuleError("Devise invalide (code ISO 4217)", code="invalid_currency")
        if cmd.trial_days is not None and cmd.trial_days < 1:
            raise BusinessRuleError("Durée d'essai invalide", code="invalid_trial_days")

    def _create_subscription(
        self, tenant_id: uuid.UUID, plan: Plan, cmd: ProvisionTenantCommand
    ) -> Subscription:
        if cmd.trial_days:
            status = SubscriptionStatus.TRIAL
            end = self.now + timedelta(days=cmd.trial_days)
        elif cmd.subscription_start is SubscriptionStart.PENDING_ACTIVATION:
            # Aucune période payée : elle commencera à l'activation (licence).
            status = SubscriptionStatus.PENDING_ACTIVATION
            end = self.now
        else:
            status = SubscriptionStatus.ACTIVE
            end = period_end(self.now, cmd.billing_period)
        price, currency = subscription_price(plan, cmd.billing_period)
        subscription = Subscription(
            tenant_id=tenant_id,
            plan_code=plan.code,
            billing_period=cmd.billing_period,
            status=status,
            started_at=self.now,
            current_period_start=self.now,
            current_period_end=end,
            price_at_subscription=price,
            currency_at_subscription=currency,
        )
        self.db.add(subscription)
        return subscription

    def _init_modules(self, tenant_id: uuid.UUID, profile: BusinessProfile, plan: Plan) -> set[str]:
        plan_modules = {m.module_code for m in plan.modules}
        enabled: set[str] = set()
        for link in profile.modules:
            if link.module_code not in plan_modules:
                continue
            self.db.add(
                TenantModule(
                    tenant_id=tenant_id,
                    module_code=link.module_code,
                    enabled=link.default_enabled,
                )
            )
            if link.default_enabled:
                enabled.add(link.module_code)
        return enabled

    def _create_roles(self, tenant_id: uuid.UUID) -> Role:
        """Rôles système (permissions résolues à l'exécution depuis leur modèle). Renvoie le
        rôle protégé d'administration du tenant (le catalogue en garantit l'existence)."""
        admin: Role | None = None
        for template in self.role_templates.values():
            role = Role(
                tenant_id=tenant_id,
                name=template.name,
                description=template.description,
                template_code=template.code,
                is_system=True,
            )
            self.db.add(role)
            if template.protected and admin is None:
                admin = role
        if admin is None:
            raise BusinessRuleError("Aucun rôle d'administration", code="admin_role_missing")
        self.db.flush()
        return admin

    def _get_or_create_owner(self, cmd: ProvisionTenantCommand) -> tuple[User, bool]:
        try:
            email = normalize_email(
                validate_email(cmd.owner_email, check_deliverability=False).normalized
            )
        except EmailNotValidError as exc:
            raise BusinessRuleError("Email du propriétaire invalide", code="invalid_email") from exc
        existing = self.db.scalars(select(User).where(User.email == email)).one_or_none()
        if existing is not None:
            return existing, False
        if not cmd.owner_password:
            raise BusinessRuleError(
                "Mot de passe provisoire requis pour un nouvel utilisateur",
                code="password_required",
            )
        validate_new_password(cmd.owner_password, email=email, settings=self.settings)
        user = User(
            email=email,
            full_name=cmd.owner_full_name.strip() or email,
            password_hash=hash_password(cmd.owner_password),
            must_change_password=not cmd.owner_password_is_final,
        )
        self.db.add(user)
        self.db.flush()
        return user, True
