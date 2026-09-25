"""Identité documentaire de l'entreprise (Phase 3.2-C, ADR-0027).

Le ``Tenant`` est la **source unique de vérité** de l'identité de l'entreprise : aucun autre
stockage (pas de table ``company_identity``, aucune copie dans les documents). Ce module
classe ses informations (obligatoires / recommandées / facultatives) et construit l'en-tête
qu'utiliseront les futurs reçus, factures et documents — aujourd'hui l'aperçu de la page
Entreprise. Une information absente n'est jamais remplacée par « N/A » : sa ligne est omise.
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.platform.catalog.models import GeoCountry
from app.platform.tenancy.models import Tenant

# Obligatoires (marquées « * », refusées vides par l'API ; étape d'onboarding ``company``).
REQUIRED_COMPANY_FIELDS = ("name", "country_code", "currency")
# Recommandées : utiles aux documents, jamais bloquantes (étape ``configuration``).
RECOMMENDED_COMPANY_FIELDS = (
    "trade_name",
    "logo_url",
    "phone",
    "email",
    "address",
    "city",
    "region",
    "tax_id",
    "trade_register",
)
# Facultatives : hors en-tête documentaire.
OPTIONAL_COMPANY_FIELDS = ("website", "description")


def filled(value: object) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


@dataclass(frozen=True)
class IdentityLine:
    """Ligne d'en-tête : ``kind`` stable (le libellé éventuel — « Tél. », « IFU »… — est
    traduit par l'interface ou le futur moteur de rendu), ``value`` telle qu'enregistrée."""

    kind: str
    value: str


@dataclass(frozen=True)
class DocumentIdentity:
    name: str
    trade_name: str | None
    # Futur stockage de fichiers : il alimentera ``logo_url`` sans changer ce contrat.
    logo_url: str | None
    # Coordonnées, dans l'ordre d'affichage : téléphone, e-mail, adresse, localité.
    contact: list[IdentityLine] = field(default_factory=list)
    # Identifiants légaux : IFU (``tax_id``), RCCM (``trade_register``).
    identifiers: list[IdentityLine] = field(default_factory=list)
    # Informations recommandées non renseignées (aide à la saisie ; jamais imprimées).
    missing_recommended: list[str] = field(default_factory=list)


def _lines(pairs: list[tuple[str, str | None]]) -> list[IdentityLine]:
    return [IdentityLine(kind, value.strip()) for kind, value in pairs if value and value.strip()]


def document_identity(db: Session, tenant: Tenant) -> DocumentIdentity:
    """En-tête documentaire construit uniquement à partir des données disponibles."""
    country = db.get(GeoCountry, tenant.country_code) if tenant.country_code else None
    locality = ", ".join(
        part.strip()
        for part in (tenant.city, tenant.region, country.name if country else None)
        if part and part.strip()
    )
    return DocumentIdentity(
        name=tenant.name,
        trade_name=tenant.trade_name if filled(tenant.trade_name) else None,
        logo_url=tenant.logo_url if filled(tenant.logo_url) else None,
        contact=_lines(
            [
                ("phone", tenant.phone),
                ("email", tenant.email),
                ("address", tenant.address),
                ("locality", locality),
            ]
        ),
        identifiers=_lines([("tax_id", tenant.tax_id), ("trade_register", tenant.trade_register)]),
        missing_recommended=[
            f for f in RECOMMENDED_COMPANY_FIELDS if not filled(getattr(tenant, f))
        ],
    )
