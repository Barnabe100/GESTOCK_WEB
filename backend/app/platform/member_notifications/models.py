import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.platform.models_base import IdMixin, TenantScopedMixin

# Durée de conservation (ADR-0049 D8) : au-delà, la notification n'est plus lisible ni utile ; la
# purge (rôle SQL dédié) la supprime. La même durée borne la politique RLS du rôle de purge
# (migration 0045) : le rôle ne peut ni lire ni supprimer une notification plus récente.
RETENTION_DAYS = 30


class MemberNotification(IdMixin, TenantScopedMixin, Base):
    """Avis adressé à UN membre sur UN site (ex. « Commande n° 12 prête »).

    Créé par l'action qui le provoque (dans sa transaction), jamais par le client ; une seule
    notification par destinataire et par évènement source (clé anti-doublon). Lisible et
    modifiable (``read_at`` seulement) par son seul destinataire (RLS restrictive sur
    ``app.user_id``) ; jamais modifiée autrement : la résolution (« traitée ») est calculée à la
    lecture par le module qui l'a émise. ``payload`` : contexte d'affichage, sans donnée
    sensible (ni montant, ni client, ni coût)."""

    __tablename__ = "member_notifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "site_id"], ["sites.tenant_id", "sites.id"], ondelete="RESTRICT"
        ),
        # Clé anti-doublon (D8) : une notification par destinataire et par évènement.
        UniqueConstraint(
            "tenant_id",
            "recipient_user_id",
            "source_type",
            "source_id",
            name="uq_member_notifications_source",
        ),
        Index(
            "ix_member_notifications_recipient",
            "tenant_id",
            "recipient_user_id",
            "created_at",
        ),
        Index("ix_member_notifications_created_at", "created_at"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    # Type de l'avis (ex. ``restaurant.order.ready``) : choisit le texte et la résolution.
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    # Évènement qui l'a provoqué (ex. ``restaurant_order_event``, identifiant de l'évènement).
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # Entité concernée (lien de l'interface ; ex. ``restaurant_order``).
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
