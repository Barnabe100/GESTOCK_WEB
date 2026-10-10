import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TenantFiltered
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
    # Règlement d'une commande de restauration (ADR-0049, palier R2-D) : vente créée par le
    # serveur depuis la commande (origine immuable), jamais saisie directement.
    RESTAURANT = "RESTAURANT"


# Origines d'une vente (ADR-0049, R2-D) : posées par le serveur à la création, immuables
# (déclencheur), jamais acceptées en entrée de l'API ; ``None`` pour une vente ordinaire.
ORIGIN_RESTAURANT_ORDER = "restaurant_order"
ACTIVE_ORIGIN_INDEX = "uq_sales_active_origin"


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
        # Numéro attribué à la validation (Lot 1) : jamais de vente validée (même annulée ensuite)
        # sans numéro ; un brouillon abandonné n'en a pas.
        CheckConstraint("validated_at IS NULL OR number IS NOT NULL", name="validated_has_number"),
        # Exception de limite de crédit : autorisateur, date, justification et montant ensemble.
        CheckConstraint(
            "(credit_override_by IS NULL) = (credit_override_at IS NULL) "
            "AND (credit_override_by IS NULL) = (credit_override_reason IS NULL) "
            "AND (credit_override_by IS NULL) = (credit_override_amount IS NULL)",
            name="credit_override_complete",
        ),
        # Lot 3-H-A (O-1) : dérogation à la vente d'un lot périmé — auteur, date, motif.
        CheckConstraint(
            "(expired_lot_override_by IS NULL) = (expired_lot_override_at IS NULL) "
            "AND (expired_lot_override_by IS NULL) = (expired_lot_override_reason IS NULL)",
            name="expired_lot_override_complete",
        ),
        Index("ix_sales_tenant_date", "tenant_id", "sale_date"),
        # Origine (R2-D) : complète ou absente ; une vente du canal Restauration a toujours une
        # origine ; au plus UNE vente active (non annulée) par origine.
        CheckConstraint("(origin_type IS NULL) = (origin_id IS NULL)", name="origin_complete"),
        CheckConstraint(
            "channel <> 'RESTAURANT' OR origin_type IS NOT NULL", name="restaurant_has_origin"
        ),
        Index(
            ACTIVE_ORIGIN_INDEX,
            "tenant_id",
            "origin_type",
            "origin_id",
            unique=True,
            postgresql_where=text("origin_id IS NOT NULL AND status <> 'CANCELLED'"),
        ),
    )

    # ``VENT-{SITE}-{ANNÉE}-{SÉQUENCE}`` attribué à la VALIDATION (Lot 1, ADR-0037) ; nul pour un
    # brouillon.
    number: Mapped[str | None] = mapped_column(String(64))
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
    # Vente à crédit : reste dû à la validation (client obligatoire, ``sales.sale.credit_create``).
    is_credit: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # Dépassement exceptionnel de la limite de crédit (``sales.sale.credit_override``).
    credit_override_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    credit_override_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credit_override_reason: Mapped[str | None] = mapped_column(String(500))
    credit_override_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    # Dérogation explicite à la vente d'un lot périmé (``sales.sale.expired_lot_override``,
    # Lot 3-H-A, O-1) : lots et quantités dans l'audit et le journal des mouvements.
    expired_lot_override_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    expired_lot_override_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_lot_override_reason: Mapped[str | None] = mapped_column(String(500))
    # Origine de la vente (R2-D) : document dont elle est issue (ex. commande de restauration),
    # posée par le serveur ; seule une vente ayant une origine est dispensée du contrôle
    # ``sale_prices_changed`` (prix figés sur l'origine).
    origin_type: Mapped[str | None] = mapped_column(String(32))
    origin_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

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
        ForeignKeyConstraint(
            ["tenant_id", "packaging_id"],
            ["catalog_packagings.tenant_id", "catalog_packagings.id"],
            ondelete="RESTRICT",
        ),
        # Lot 3-B : un article une fois par présentation (unité de base OU conditionnement).
        Index(
            "uq_sale_lines_sale_article_base",
            "sale_id",
            "article_id",
            unique=True,
            postgresql_where=text("packaging_id IS NULL"),
        ),
        Index(
            "uq_sale_lines_sale_packaging",
            "sale_id",
            "packaging_id",
            unique=True,
            postgresql_where=text("packaging_id IS NOT NULL"),
        ),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
        CheckConstraint("line_total >= 0", name="line_total_non_negative"),
        # Instantané du conditionnement complet ou absent ; quantité de base = quantité ×
        # conversion (unité de base : conversion 1), sans arrondi.
        CheckConstraint(
            "(packaging_id IS NULL) = (packaging_name IS NULL) "
            "AND (packaging_id IS NULL) = (packaging_conversion IS NULL)",
            name="packaging_snapshot_complete",
        ),
        CheckConstraint(
            "packaging_conversion IS NULL OR packaging_conversion > 0",
            name="packaging_conversion_positive",
        ),
        CheckConstraint(
            "base_quantity = quantity * COALESCE(packaging_conversion, 1)",
            name="base_quantity_consistent",
        ),
    )

    sale_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # Quantité vendue dans la présentation choisie (unité de base ou conditionnement).
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    # Prix unitaire de la présentation (article ou conditionnement), figé.
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    # Lot 3-B (ADR-0040) : conditionnement vendu et son instantané (nom, conversion), figés —
    # une vente historique reste fidèle quoi qu'il advienne du conditionnement.
    packaging_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    packaging_name: Mapped[str | None] = mapped_column(String(50))
    packaging_conversion: Mapped[Decimal | None] = mapped_column(QUANTITY)
    # Quantité en unité de base : celle qui sort du stock (et y revient à l'annulation).
    base_quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)


# --- Paiements (Phase 2.7, ADR-0020) ------------------------------------------------------------


class PaymentMethod(StrEnum):
    """Catégorie de moyen de paiement (code technique, indépendant de la langue). Le détail
    d'un fournisseur (Orange Money, Wave…) va dans ``provider``, sans nouveau code."""

    CASH = "CASH"
    MOBILE_MONEY = "MOBILE_MONEY"
    CARD = "CARD"
    BANK_TRANSFER = "BANK_TRANSFER"
    OTHER = "OTHER"


class PaymentIntegration(StrEnum):
    """Mode de saisie d'un moyen de paiement : manuel (montant + référence) ; ``API`` réservé à
    une intégration future (Orange Money, cartes…), sans changer le modèle du paiement."""

    MANUAL = "MANUAL"
    API = "API"


class CreditStatus(StrEnum):
    """Situation d'une vente à crédit, CALCULÉE (jamais stockée)."""

    OPEN = "OPEN"  # rien payé
    PARTIAL = "PARTIAL"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


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
        ForeignKeyConstraint(
            ["tenant_id", "payment_method_id"],
            ["payment_methods.tenant_id", "payment_methods.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        # Espèces : montant reçu ≥ montant imputé, monnaie = reçu − imputé ; ailleurs : rien.
        CheckConstraint(
            "(amount_received IS NULL AND change_given IS NULL) OR (method = 'CASH' "
            "AND amount_received >= amount AND change_given = amount_received - amount)",
            name="cash_change_consistent",
        ),
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
    # Type du moyen (comportement : monnaie et caisse pour ``CASH``), figé au paiement.
    method: Mapped[PaymentMethod] = mapped_column(
        str_enum(PaymentMethod, "payment_method"), nullable=False
    )
    # Moyen configuré (Lot 1) et son libellé figé au paiement : renommer ou désactiver le moyen
    # ne change jamais l'historique. Paiements antérieurs : moyen par défaut du même type.
    payment_method_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    method_label: Mapped[str] = mapped_column(String(60), nullable=False)
    # Précision facultative saisie autrefois (opérateur Mobile Money…), conservée telle quelle.
    provider: Mapped[str | None] = mapped_column(String(50))
    # Espèces : montant remis par le client et monnaie rendue (calculée par le serveur).
    amount_received: Mapped[Decimal | None] = mapped_column(MONEY)
    change_given: Mapped[Decimal | None] = mapped_column(MONEY)
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


class ConfiguredPaymentMethod(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Moyen de paiement configuré par l'entreprise (Lot 1, ADR-0037) : libellé libre (« Orange
    Money », « Wave »…), **type** qui gouverne le comportement (``CASH`` : monnaie et caisse),
    saisie manuelle (intégration API future), référence obligatoire ou non. Jamais supprimé :
    désactivé. Disponible sur tous les sites, sauf désactivation pour un site."""

    __tablename__ = "payment_methods"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "label"),
        CheckConstraint("length(btrim(label)) > 0", name="label_not_blank"),
    )

    label: Mapped[str] = mapped_column(String(60), nullable=False)
    kind: Mapped[PaymentMethod] = mapped_column(
        str_enum(PaymentMethod, "payment_method_kind"), nullable=False
    )
    integration_mode: Mapped[PaymentIntegration] = mapped_column(
        str_enum(PaymentIntegration, "payment_integration"),
        default=PaymentIntegration.MANUAL,
        server_default=PaymentIntegration.MANUAL.value,
        nullable=False,
    )
    reference_required: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))


class PaymentMethodSite(TenantFiltered, Base):
    """Disponibilité d'un moyen de paiement sur un site (ligne absente : disponible)."""

    __tablename__ = "payment_method_sites"
    __table_args__ = (
        PrimaryKeyConstraint("payment_method_id", "site_id"),
        ForeignKeyConstraint(
            ["tenant_id", "payment_method_id"],
            ["payment_methods.tenant_id", "payment_methods.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "site_id"], ["sites.tenant_id", "sites.id"], ondelete="RESTRICT"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    payment_method_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
