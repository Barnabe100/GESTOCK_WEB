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
    issued_at: datetime
    revoked_at: datetime | None


def license_summary(license: License | None, now: datetime) -> LicenseSummary | None:
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
        issued_at=license.issued_at,
        revoked_at=license.revoked_at,
    )
