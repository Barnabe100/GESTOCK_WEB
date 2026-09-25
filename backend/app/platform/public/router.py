import unicodedata

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.platform.catalog.models import GeoCountry, Plan
from app.platform.context import DbSession, MetaDep, NowDep, RegistryDep, SettingsDep
from app.platform.identity.router import session_response
from app.platform.identity.schemas import SessionResponse
from app.platform.profiles.registry import BusinessProfileRegistry
from app.platform.registry import ModuleStatus
from app.platform.signup.schemas import (
    PublicPlan,
    PublicPlanPeriod,
    PublicPlansOut,
    PublicProfile,
    PublicProfilesOut,
    PublicSector,
    SignupRequest,
)
from app.platform.signup.service import SignupService, period_enabled, self_service
from app.platform.subscriptions.models import BillingPeriod

router = APIRouter(prefix="/public", tags=["public"])


class CountryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    currency: str
    calling_code: int | None
    timezone: str


def _collation_key(name: str) -> str:
    """Tri alphabétique sans accents (« Égypte » avec les E)."""
    return "".join(
        c for c in unicodedata.normalize("NFD", name.casefold()) if not unicodedata.combining(c)
    )


@router.get("/geo/countries", response_model=list[CountryOut])
def list_countries(db: DbSession, response: Response) -> list[CountryOut]:
    """Pays proposés à l'inscription (référentiel ISO 3166-1, pays actifs), avec la devise, le
    fuseau horaire et l'indicatif proposés par défaut. Données publiques, identiques pour
    tous : le frontend n'embarque aucune liste."""
    countries = db.scalars(select(GeoCountry).where(GeoCountry.is_active.is_(True))).all()
    response.headers["Cache-Control"] = "public, max-age=3600"
    ordered = sorted(countries, key=lambda c: _collation_key(c.name))
    return [CountryOut.model_validate(c) for c in ordered]


@router.get("/business-profiles", response_model=PublicProfilesOut)
def list_public_business_profiles(
    db: DbSession, registry: RegistryDep, response: Response
) -> PublicProfilesOut:
    """Secteurs et profils d'activité actifs (catalogue Phase 3.1), pour l'inscription."""
    profiles = BusinessProfileRegistry(db, registry)
    response.headers["Cache-Control"] = "public, max-age=3600"
    return PublicProfilesOut(
        sectors=[
            PublicSector(code=s.code, name=s.name, icon=s.icon) for s in profiles.list_sectors()
        ],
        profiles=[
            PublicProfile(code=p.code, name=p.name, description=p.description, sector=p.sector_code)
            for p in profiles.list_profiles()
        ],
    )


@router.get("/plans", response_model=PublicPlansOut)
def list_public_plans(
    db: DbSession, registry: RegistryDep, settings: SettingsDep
) -> PublicPlansOut:
    """Offres publiées par TechNova, avec leurs seules informations publiques. Un prix n'est
    renvoyé que si TechNova l'affiche ; une période fermée n'apparaît pas ; aucun prix n'est
    connu du frontend autrement."""
    plans = db.scalars(
        select(Plan)
        .where(Plan.is_active.is_(True), Plan.listed.is_(True))
        .order_by(Plan.display_order, Plan.sort_order, Plan.code)
    ).all()
    limit_codes = sorted(limit.code for manifest in registry.all() for limit in manifest.limits)

    def out(plan: Plan) -> PublicPlan:
        prices = {
            BillingPeriod.MONTHLY: plan.monthly_price,
            BillingPeriod.ANNUAL: plan.annual_price,
        }
        return PublicPlan(
            code=plan.code,
            name=plan.name,
            description=plan.commercial_description or plan.description,
            contact_required=plan.contact_required,
            self_service=self_service(plan),
            trial_days=plan.trial_days,
            price_displayed=plan.price_display_enabled,
            currency=plan.currency if plan.price_display_enabled else None,
            periods=[
                PublicPlanPeriod(
                    billing_period=period,
                    price=prices[period] if plan.price_display_enabled else None,
                )
                for period in BillingPeriod
                if period_enabled(plan, period)
            ],
            limits={code: plan.limits.get(code) for code in limit_codes},
            modules=sorted(
                m.module_code
                for m in plan.modules
                if m.module_code in registry
                and registry.get(m.module_code).status is ModuleStatus.AVAILABLE
            ),
        )

    return PublicPlansOut(contact_email=settings.sales_contact_email, plans=[out(p) for p in plans])


@router.post("/signup", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def signup(
    body: SignupRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    registry: RegistryDep,
    now: NowDep,
    meta: MetaDep,
) -> SessionResponse:
    """Crée un compte et une entreprise ; renvoie une session ouverte sur cette entreprise.
    Adresse du client : celle vue par l'application (voir SM_SIGNUP_RATE_LIMIT_* et la
    configuration du reverse proxy)."""
    client_ip = request.client.host if request.client else "unknown"
    issued = SignupService(db, settings, registry, now).signup(body, client_ip=client_ip, meta=meta)
    db.commit()
    return session_response(issued, response, settings)
