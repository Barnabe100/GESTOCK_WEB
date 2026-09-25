"""Étape d'onboarding du catalogue (recommandée) : premiers articles."""

from sqlalchemy import exists, select

from app.modules.catalog.models import Article, Category
from app.platform.onboarding.definitions import OnboardingEnv, OnboardingStatus, status_from
from app.platform.onboarding.steps import step


def evaluate_catalogue(env: OnboardingEnv) -> OnboardingStatus:
    """Au moins un article actif → terminée ; seulement des catégories → en cours."""
    has_article = env.db.scalar(select(exists().where(Article.is_active.is_(True))))
    has_category = bool(has_article) or env.db.scalar(select(exists().select_from(Category)))
    return status_from(bool(has_article), bool(has_category))


CATALOG_STEPS = (
    step(
        "catalogue",
        60,
        required=False,
        evaluate=evaluate_catalogue,
        action=("/catalog/articles?create=1", "catalog.article.create"),
    ),
)
