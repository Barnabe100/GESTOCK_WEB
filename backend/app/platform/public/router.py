import unicodedata

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.platform.catalog.models import GeoCountry
from app.platform.context import DbSession

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
