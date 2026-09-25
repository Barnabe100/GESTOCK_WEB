from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.platform.models_base import IdMixin


class RateLimitHit(IdMixin, Base):
    """Tentative comptée pour une limite de fréquence (ex. inscription par adresse IP).

    Table globale (aucune donnée de tenant) : la clé est **hachée** (jamais d'adresse IP en
    clair) ; les tentatives sorties de la fenêtre sont purgées à chaque passage."""

    __tablename__ = "rate_limit_hits"
    __table_args__ = (
        Index("ix_rate_limit_hits_bucket_key_at", "bucket", "key_hash", "created_at"),
    )

    bucket: Mapped[str] = mapped_column(String(50), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
