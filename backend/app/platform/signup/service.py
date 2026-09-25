"""Inscription publique : un nouvel utilisateur crée une nouvelle entreprise dont il devient
propriétaire et administrateur principal.

- Réutilise ``TenantProvisioningService`` (une seule transaction : tout ou rien) sans site
  initial (étape d'onboarding ``first_site``) ; mot de passe choisi par l'utilisateur.
- Abonnement : essai de ``trial_days`` jours si TechNova l'a configuré, sinon
  ``pending_activation`` (aucun paiement confirmé, aucune licence : accès administratif
  seulement). Jamais ``active`` : l'activation viendra d'une licence après paiement confirmé.
- Plans : seulement publiés, actifs, sans contact commercial et sur une période ouverte.
- Limite de fréquence par adresse IP ; e-mail déjà connu : refus générique
  ``signup_unavailable`` (après toutes les autres validations, temps de hachage égalisé).
"""

import secrets
import unicodedata
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import BusinessRuleError, ConflictError, ForbiddenError
from app.core.security import hash_password
from app.platform.audit.service import RequestMeta
from app.platform.catalog.loader import role_templates
from app.platform.catalog.models import Plan
from app.platform.identity.models import User
from app.platform.identity.passwords import normalize_email, validate_new_password
from app.platform.identity.service import AuthService, IssuedSession
from app.platform.provisioning.service import (
    ProvisionTenantCommand,
    SubscriptionStart,
    TenantProvisioningService,
)
from app.platform.ratelimit.service import RateLimiter
from app.platform.registry import ModuleRegistry
from app.platform.signup.schemas import SignupRequest
from app.platform.subscriptions.models import BillingPeriod

RATE_LIMIT_BUCKET = "signup"
UNAVAILABLE = (
    "Inscription impossible avec ces informations. Si vous avez déjà un compte, connectez-vous."
)


def period_enabled(plan: Plan, period: BillingPeriod) -> bool:
    return (
        plan.monthly_price_enabled if period is BillingPeriod.MONTHLY else plan.annual_price_enabled
    )


def self_service(plan: Plan) -> bool:
    """Plan souscriptible depuis l'inscription publique."""
    return (
        plan.is_active
        and plan.listed
        and not plan.contact_required
        and (plan.monthly_price_enabled or plan.annual_price_enabled)
    )


def slug_base(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = "".join(c if c.isalnum() else "-" for c in ascii_name.lower()).split("-")
    return "-".join(w for w in words if w)[:40].strip("-") or "entreprise"


class SignupService:
    def __init__(
        self, db: Session, settings: Settings, registry: ModuleRegistry, now: datetime
    ) -> None:
        self.db = db
        self.settings = settings
        self.registry = registry
        self.now = now

    def signup(self, data: SignupRequest, *, client_ip: str, meta: RequestMeta) -> IssuedSession:
        if not self.settings.signup_enabled:
            raise ForbiddenError("Inscription fermée", code="signup_closed")
        # Tentative comptée et validée avant tout traitement : un échec reste compté.
        RateLimiter(self.db, self.now).hit(
            RATE_LIMIT_BUCKET,
            client_ip,
            limit=self.settings.signup_rate_limit_attempts,
            window=timedelta(minutes=self.settings.signup_rate_limit_window_minutes),
        )
        self.db.commit()

        plan = self.db.get(Plan, data.plan_code)
        if plan is None or not self_service(plan) or not period_enabled(plan, data.billing_period):
            raise BusinessRuleError("Offre indisponible", code="plan_not_available")
        email = normalize_email(str(data.account.email))
        validate_new_password(data.account.password, email=email, settings=self.settings)

        command = ProvisionTenantCommand(
            name=data.company.name,
            slug=f"{slug_base(data.company.name)}-{secrets.token_hex(4)}",
            profile_code=data.business_profile,
            plan_code=plan.code,
            billing_period=data.billing_period,
            owner_email=email,
            owner_full_name=data.account.full_name,
            country_code=data.company.country_code,
            owner_password=data.account.password,
            trial_days=plan.trial_days or None,
            subscription_start=SubscriptionStart.PENDING_ACTIVATION,
            owner_password_is_final=True,
            create_first_site=False,
            company=data.company.model_dump(
                exclude={"name", "country_code", "currency"}, exclude_none=True
            ),
            currency=data.company.currency,
        )
        provisioning = TenantProvisioningService(
            self.db, self.settings, self.registry, role_templates(), self.now
        )
        # Profil, pays, devise… validés par le provisioning AVANT le contrôle de l'e-mail.
        provisioning.check(command)
        if self.db.scalars(select(User.id).where(User.email == email)).first() is not None:
            hash_password(data.account.password)  # temps de réponse comparable
            raise BusinessRuleError(UNAVAILABLE, code="signup_unavailable")
        try:
            result = provisioning.provision(command, actor="signup", meta=meta)
            self.db.flush()
        except IntegrityError as exc:
            # Inscription concurrente avec le même e-mail : même réponse générique.
            self.db.rollback()
            raise BusinessRuleError(UNAVAILABLE, code="signup_unavailable") from exc
        except ConflictError as exc:  # identifiant court déjà pris (hasard)
            self.db.rollback()
            raise BusinessRuleError(UNAVAILABLE, code="signup_unavailable") from exc
        self.db.commit()
        return AuthService(self.db, self.settings, self.now).login(
            email, data.account.password, result.tenant_id, meta
        )
