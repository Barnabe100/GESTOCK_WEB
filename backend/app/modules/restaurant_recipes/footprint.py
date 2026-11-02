"""Empreinte des recettes sur un site (palier D) : les recettes sont communes à l'entreprise ;
seules les consommations d'ingrédients des ventes du site sont un historique (jamais
bloquant). Lecture seule."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.restaurant_recipes.models import RecipeConsumption
from app.platform.footprint import SiteFootprint, capped_count


def recipes_footprint(session: Session, site_id: uuid.UUID, lock: bool) -> SiteFootprint:
    return SiteFootprint.of(
        history={
            "recipe_consumptions": capped_count(
                session,
                select(RecipeConsumption.id).where(RecipeConsumption.site_id == site_id),
            )
        }
    )
