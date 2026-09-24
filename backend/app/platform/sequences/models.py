import uuid

from sqlalchemy import BigInteger, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TenantFiltered


class DocumentSequence(TenantFiltered, Base):
    """Compteur par (tenant, type de document)."""

    __tablename__ = "document_sequences"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), primary_key=True
    )
    sequence_key: Mapped[str] = mapped_column(String(50), primary_key=True)
    next_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
