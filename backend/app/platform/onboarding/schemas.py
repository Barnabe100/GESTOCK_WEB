from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.platform.onboarding.definitions import OnboardingStatus


class OnboardingActionOut(BaseModel):
    route: str
    label: str  # clé i18n
    permission: str
    # Indisponible : ``subscription_restricted`` (statut de l'abonnement) ou
    # ``permission_denied`` (rôle) ; le backend refuse de toute façon sur l'écran ciblé.
    available: bool
    blocked_reason: str | None


class OnboardingStepOut(BaseModel):
    code: str
    order: int
    required: bool
    title: str  # clé i18n
    description: str  # clé i18n
    status: OnboardingStatus
    completed_at: datetime | None
    action: OnboardingActionOut | None


class OnboardingProgress(BaseModel):
    completed: int
    total: int
    percentage: int
    required_completed: int
    required_total: int


class OnboardingOut(BaseModel):
    # COMPLETED : toutes les étapes obligatoires terminées (≠ activation de l'abonnement).
    status: OnboardingStatus
    completed: bool
    progress: OnboardingProgress
    current_step: str | None
    next_action: OnboardingActionOut | None
    # Rappel : l'onboarding ne modifie jamais l'abonnement (ex. pending_activation).
    subscription_status: str
    steps: list[OnboardingStepOut]


class OnboardingStepUpdate(BaseModel):
    """Seule valeur acceptée : ``IN_PROGRESS`` (démarrer une étape). Une étape n'est jamais
    déclarée terminée par le client."""

    model_config = ConfigDict(extra="forbid")

    status: OnboardingStatus
