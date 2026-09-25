import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.platform.models_base import IdMixin, TenantScopedMixin, TimestampMixin, str_enum

MONEY = Numeric(18, 2)
QUANTITY = Numeric(18, 3)


class SaleStatus(StrEnum):
    DRAFT = "DRAFT"  # modifiable, sans effet sur le stock
    VALIDATED = "VALIDATED"  # stock sorti (mouvements SALE), document historique immuable
    CANCELLED = "CANCELLED"  # abandonnée (brouillon) ou annulée (mouvements inverses)


class SaleChannel(StrEnum):
    """Canal de saisie de la vente (dimension de reporting) : même règles métier partout."""

    BACKOFFICE = "BACKOFFICE"  # écrans de gestion des ventes
    POS = "POS"  # point de vente (encaissement en une étape)


class Sale(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Vente comptant d'un site, client facultatif. Jamais supprimée."""

    __tablename__ = "sales"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number"),
        UniqueConstraint("tenant_id", "id"),
        # Cible de la FK composite des paiements : même tenant ET même site que la vente.
        UniqueConstraint("tenant_id", "id", "site_id"),
        # Encaissement en une étape (POS) : une seule vente par clé d'idempotence et par tenant.
        UniqueConstraint("tenant_id", "idempotency_key"),
        # Références du même tenant uniquement (FK composites).
        ForeignKeyConstraint(
            ["tenant_id", "site_id"], ["sites.tenant_id", "sites.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "customer_id"],
            ["customers.tenant_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("subtotal >= 0 AND total >= 0", name="amounts_non_negative"),
        CheckConstraint(
            "status <> 'VALIDATED' OR validated_at IS NOT NULL", name="validated_has_date"
        ),
        CheckConstraint(
            "status <> 'CANCELLED' OR (cancelled_at IS NOT NULL "
            "AND cancellation_reason IS NOT NULL)",
            name="cancelled_has_reason",
        ),
        Index("ix_sales_tenant_date", "tenant_id", "sale_date"),
    )

    number: Mapped[str] = mapped_column(String(20), nullable=False)
    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    status: Mapped[SaleStatus] = mapped_column(
        str_enum(SaleStatus, "sale_status"), default=SaleStatus.DRAFT, nullable=False
    )
    sale_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Recalculés par le serveur ; total = sous-total tant qu'il n'y a ni remise ni taxe.
    subtotal: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500))
    channel: Mapped[SaleChannel] = mapped_column(
        str_enum(SaleChannel, "sale_channel"),
        default=SaleChannel.BACKOFFICE,
        server_default=SaleChannel.BACKOFFICE.value,
        nullable=False,
    )
    # Clé fournie par le client pour un encaissement en une étape (double soumission).
    idempotency_key: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    cancellation_reason: Mapped[str | None] = mapped_column(String(500))

    lines: Mapped[list["SaleLine"]] = relationship(
        cascade="all, delete-orphan", order_by="SaleLine.line_no", lazy="selectin"
    )


class SaleLine(IdMixin, TenantScopedMixin, Base):
    """Ligne de vente : prix unitaire figé depuis le catalogue (historique exact)."""

    __tablename__ = "sale_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "sale_id"], ["sales.tenant_id", "sales.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "article_id"],
            ["catalog_articles.tenant_id", "catalog_articles.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("sale_id", "article_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
        CheckConstraint("line_total >= 0", name="line_total_non_negative"),
    )

    sale_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)


# --- Paiements (Phase 2.7, ADR-0020) ------------------------------------------------------------


class PaymentMethod(StrEnum):
    """Catégorie de moyen de paiement (code technique, indépendant de la langue). Le détail
    d'un fournisseur (Orange Money, Wave…) va dans ``provider``, sans nouveau code."""

    CASH = "CASH"
    MOBILE_MONEY = "MOBILE_MONEY"
    CARD = "CARD"
    BANK_TRANSFER = "BANK_TRANSFER"
    OTHER = "OTHER"


class PaymentStatus(StrEnum):
    PENDING = "PENDING"  # réservé aux encaissements asynchrones futurs (non créé en V1)
    COMPLETED = "COMPLETED"  # encaissé : compte dans le montant payé
    CANCELLED = "CANCELLED"  # annulé (erreur) : conservé dans l'historique, ne compte plus


class SalePaymentStatus(StrEnum):
    """État d'encaissement d'une vente validée, CALCULÉ (jamais stocké) à partir des paiements
    effectués ; indépendant du statut commercial de la vente."""

    UNPAID = "UNPAID"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"


class Payment(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Encaissement d'une vente validée. Jamais modifié après encaissement (montant, moyen) ni
    supprimé : une erreur se corrige par annulation (motif, auteur, date) puis nouveau
    paiement. Aucun effet sur le stock."""

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number"),
        UniqueConstraint("tenant_id", "id"),
        # Clé d'idempotence fournie par le client : un seul paiement par clé et par tenant.
        UniqueConstraint("tenant_id", "idempotency_key"),
        # Cible de la FK composite des mouvements de caisse : même tenant ET même site.
        UniqueConstraint("tenant_id", "id", "site_id"),
        # Même tenant et même site que la vente (FK composite sur (tenant, vente, site)).
        ForeignKeyConstraint(
            ["tenant_id", "sale_id", "site_id"],
            ["sales.tenant_id", "sales.id", "sales.site_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "site_id"], ["sites.tenant_id", "sites.id"], ondelete="RESTRICT"
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "status <> 'CANCELLED' OR (cancelled_at IS NOT NULL "
            "AND cancellation_reason IS NOT NULL)",
            name="cancelled_has_reason",
        ),
        Index("ix_payments_tenant_sale", "tenant_id", "sale_id"),
        Index("ix_payments_tenant_paid_at", "tenant_id", "paid_at"),
    )

    number: Mapped[str] = mapped_column(String(20), nullable=False)
    sale_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # Site de la vente (copié) : exploitable par la future caisse sans jointure.
    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    method: Mapped[PaymentMethod] = mapped_column(
        str_enum(PaymentMethod, "payment_method"), nullable=False
    )
    # Précision facultative du moyen (opérateur Mobile Money, réseau de carte…).
    provider: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[PaymentStatus] = mapped_column(
        str_enum(PaymentStatus, "payment_status"), nullable=False
    )
    reference: Mapped[str | None] = mapped_column(String(100))  # n° de transaction, de chèque…
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    cancellation_reason: Mapped[str | None] = mapped_column(String(500))
