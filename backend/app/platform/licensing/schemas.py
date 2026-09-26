import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.platform.licensing.models import License


class LicenseSummary(BaseModel):
    """Licence d'un site vue par l'entreprise (lecture seule ; elle ne peut ni la générer, ni
    en modifier le contenu). ``state`` est calculé : ``NOT_YET_VALID``, ``ACTIVE``, ``EXPIRED``
    ou ``REVOKED``."""

    id: uuid.UUID
    license_number: str
    license_version: int
    state: str
    plan_code: str
    valid_from: date
    valid_until: date
    max_activations: int
    # Postes (3.3-B3) : actifs sur l'abonnement du site, et places restantes sous cette licence
    # (jamais négatif : un dépassement après réduction du quota est toléré, jamais aggravé).
    activations_used: int
    activations_available: int
    issued_at: datetime
    revoked_at: datetime | None


def license_summary(
    license: License | None, now: datetime, activations_used: int = 0
) -> LicenseSummary | None:
    if license is None:
        return None
    return LicenseSummary(
        id=license.id,
        license_number=license.license_number,
        license_version=license.license_version,
        state=license.state(now).value,
        plan_code=license.plan_code,
        valid_from=license.valid_from,
        valid_until=license.valid_until,
        max_activations=license.max_activations,
        activations_used=activations_used,
        activations_available=max(license.max_activations - activations_used, 0),
        issued_at=license.issued_at,
        revoked_at=license.revoked_at,
    )
