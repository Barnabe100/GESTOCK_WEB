import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.platform.models_base import IdMixin, TenantScopedMixin, TimestampMixin, str_enum
from app.platform.onboarding.definitions import OnboardingStatus


class OnboardingStep(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """État persistant d'une étape d'onboarding d'un tenant.

    Créée au premier accès (ou à l'inscription), jamais supprimée ; le statut ne fait
    qu'avancer et ``COMPLETED`` est définitif (contrôle du service + déclencheur en base).
    """

    __tablename__ = "onboarding_steps"
    __table_args__ = (
        UniqueConstraint("tenant_id", "step_code"),
        CheckConstraint(
            "(status = 'COMPLETED') = (completed_at IS NOT NULL)", name="completion_consistent"
        ),
    )

    step_code: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[OnboardingStatus] = mapped_column(
        str_enum(OnboardingStatus, "onboarding_step_status"),
        nullable=False,
        default=OnboardingStatus.NOT_STARTED,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Utilisateur dont l'action a complété l'étape ; nul si la complétion a été constatée par
    # une évaluation (ex. à la consultation) ou par une opération TechNova.
    completed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    # Traçabilité : origine de la complétion (``trigger``), démarrage manuel…
    details: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, nullable=False)
