"""Caisse (Phase 2.9, ADR-0022) : caisses d'un site, sessions, mouvements.

```text
Tenant → Site → Caisse (cash_registers) → Session (cash_sessions) → Mouvements (cash_movements)
```

FK composites de bout en bout : un mouvement appartient au même tenant, au même site et à la
même caisse que sa session ; une session, au même site que sa caisse ; un encaissement de vente
référence un paiement du même tenant ET du même site (FK composite vers ``payments``).

Le solde n'est jamais stocké comme valeur mutable : il est la somme signée des mouvements. Le
solde théorique, le montant compté et l'écart sont figés à la clôture (instantané immuable).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.platform.models_base import IdMixin, TenantScopedMixin, TimestampMixin, str_enum

MONEY = Numeric(18, 2)


class CashSessionStatus(StrEnum):
    OPEN = "OPEN"  # reçoit fond initial, encaissements, entrées et sorties
    CLOSED = "CLOSED"  # comptée et clôturée : immuable, consultable


class CashMovementType(StrEnum):
    OPENING_FLOAT = "OPENING_FLOAT"  # fond initial (entrée), à l'ouverture
    SALE_CASH_IN = "SALE_CASH_IN"  # encaissement espèces d'une vente (paiement CASH)
    SALE_CASH_REVERSAL = "SALE_CASH_REVERSAL"  # annulation d'un paiement CASH (sortie)
    MANUAL_CASH_IN = "MANUAL_CASH_IN"  # entrée manuelle motivée
    MANUAL_CASH_OUT = "MANUAL_CASH_OUT"  # sortie manuelle motivée


# Sens de chaque type : le montant est toujours positif, le type fixe le signe.
INFLOWS = (
    CashMovementType.OPENING_FLOAT,
    CashMovementType.SALE_CASH_IN,
    CashMovementType.MANUAL_CASH_IN,
)
OUTFLOWS = (CashMovementType.SALE_CASH_REVERSAL, CashMovementType.MANUAL_CASH_OUT)
MANUAL = (CashMovementType.MANUAL_CASH_IN, CashMovementType.MANUAL_CASH_OUT)
SALE = (CashMovementType.SALE_CASH_IN, CashMovementType.SALE_CASH_REVERSAL)


class CashMovementCategory(StrEnum):
    """Nature d'un mouvement manuel (liste fixe, traduite dans l'interface ; exploitable par le
    futur reporting). Le motif détaillé reste un texte libre obligatoire."""

    CASH_ADDITION = "CASH_ADDITION"  # entrée : apport, alimentation de la caisse
    EXPENSE = "EXPENSE"  # sortie : petite dépense
    BANK_DEPOSIT = "BANK_DEPOSIT"  # sortie : remise en banque
    WITHDRAWAL = "WITHDRAWAL"  # sortie : retrait autorisé
    CORRECTION = "CORRECTION"  # entrée ou sortie : correction autorisée
    OTHER = "OTHER"  # entrée ou sortie


IN_CATEGORIES = (
    CashMovementCategory.CASH_ADDITION,
    CashMovementCategory.CORRECTION,
    CashMovementCategory.OTHER,
)
OUT_CATEGORIES = (
    CashMovementCategory.EXPENSE,
    CashMovementCategory.BANK_DEPOSIT,
    CashMovementCategory.WITHDRAWAL,
    CashMovementCategory.CORRECTION,
    CashMovementCategory.OTHER,
)


class CashRegister(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Caisse d'un site (ressource du site, jamais d'un utilisateur). Jamais supprimée :
    désactivée ; son site ne change pas (historique)."""

    __tablename__ = "cash_registers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code"),
        UniqueConstraint("tenant_id", "id"),
        # Cible de la FK composite des sessions : même tenant ET même site.
        UniqueConstraint("tenant_id", "id", "site_id"),
        ForeignKeyConstraint(
            ["tenant_id", "site_id"], ["sites.tenant_id", "sites.id"], ondelete="RESTRICT"
        ),
    )

    code: Mapped[str] = mapped_column(String(20), nullable=False)  # CAI-001 (séquence)
    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))


class CashSession(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Période d'exploitation d'une caisse, de l'ouverture (fond initial) à la clôture
    (comptage). Une seule session ouverte par caisse (index unique partiel)."""

    __tablename__ = "cash_sessions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number"),
        UniqueConstraint("tenant_id", "id"),
        # Cible de la FK composite des mouvements : même tenant, caisse et site.
        UniqueConstraint("tenant_id", "id", "cash_register_id", "site_id"),
        ForeignKeyConstraint(
            ["tenant_id", "cash_register_id", "site_id"],
            ["cash_registers.tenant_id", "cash_registers.id", "cash_registers.site_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("opening_float >= 0", name="opening_float_non_negative"),
        CheckConstraint(
            "status <> 'CLOSED' OR (closed_at IS NOT NULL AND theoretical_balance IS NOT NULL "
            "AND counted_balance IS NOT NULL AND variance IS NOT NULL)",
            name="closed_is_counted",
        ),
        CheckConstraint("counted_balance IS NULL OR counted_balance >= 0", name="counted_positive"),
        # L'écart est toujours « compté − théorique » (recalculé par le serveur).
        CheckConstraint(
            "variance IS NULL OR variance = counted_balance - theoretical_balance",
            name="variance_consistent",
        ),
        # Une seule session OUVERTE par caisse, garantie par PostgreSQL en dernier recours.
        Index(
            "uq_cash_sessions_one_open_per_register",
            "tenant_id",
            "cash_register_id",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
        ),
        Index("ix_cash_sessions_tenant_opened", "tenant_id", "opened_at"),
    )

    number: Mapped[str] = mapped_column(String(20), nullable=False)  # SES-000001
    cash_register_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    status: Mapped[CashSessionStatus] = mapped_column(
        str_enum(CashSessionStatus, "cash_session_status"), nullable=False
    )
    # Fond initial : figé à l'ouverture (mouvement OPENING_FLOAT), jamais modifié ensuite.
    opening_float: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opened_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    # Instantané de clôture (recalculé par le serveur sous verrou) : jamais modifié ensuite.
    theoretical_balance: Mapped[Decimal | None] = mapped_column(MONEY)
    counted_balance: Mapped[Decimal | None] = mapped_column(MONEY)
    variance: Mapped[Decimal | None] = mapped_column(MONEY)
    closing_note: Mapped[str | None] = mapped_column(String(500))


class CashMovement(IdMixin, TenantScopedMixin, Base):
    """Mouvement financier d'une session : append-only (ni modifié ni supprimé ; droits du rôle
    applicatif : SELECT, INSERT). Montant toujours positif ; le type fixe le sens.

    Dimensions conservées pour le futur reporting : tenant, site, caisse, session, utilisateur,
    date, type, catégorie, montant, source (vente / paiement)."""

    __tablename__ = "cash_movements"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        # Idempotence des mouvements manuels (double clic, nouvel envoi).
        UniqueConstraint("tenant_id", "idempotency_key"),
        # Un encaissement et au plus une annulation par paiement.
        UniqueConstraint("tenant_id", "payment_id", "movement_type"),
        ForeignKeyConstraint(
            ["tenant_id", "cash_session_id", "cash_register_id", "site_id"],
            [
                "cash_sessions.tenant_id",
                "cash_sessions.id",
                "cash_sessions.cash_register_id",
                "cash_sessions.site_id",
            ],
            ondelete="RESTRICT",
        ),
        # Paiement du même tenant ET du même site que la caisse (FK composite).
        ForeignKeyConstraint(
            ["tenant_id", "payment_id", "site_id"],
            ["payments.tenant_id", "payments.id", "payments.site_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "movement_type NOT IN ('SALE_CASH_IN', 'SALE_CASH_REVERSAL') OR payment_id IS NOT NULL",
            name="sale_has_payment",
        ),
        CheckConstraint(
            "movement_type NOT IN ('MANUAL_CASH_IN', 'MANUAL_CASH_OUT') "
            "OR (reason IS NOT NULL AND category IS NOT NULL)",
            name="manual_has_reason",
        ),
        Index("ix_cash_movements_tenant_session", "tenant_id", "cash_session_id"),
        Index("ix_cash_movements_tenant_occurred", "tenant_id", "occurred_at"),
    )

    cash_session_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    cash_register_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    movement_type: Mapped[CashMovementType] = mapped_column(
        str_enum(CashMovementType, "cash_movement_type"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    category: Mapped[CashMovementCategory | None] = mapped_column(
        str_enum(CashMovementCategory, "cash_movement_category")
    )
    reason: Mapped[str | None] = mapped_column(String(255))
    reference: Mapped[str | None] = mapped_column(String(100))
    # Origine (encaissement de vente) : document et numéro affiché, comme les mouvements de
    # stock ; le paiement est référencé par clé étrangère (traçabilité vente → client).
    source_type: Mapped[str | None] = mapped_column(String(30))
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source_number: Mapped[str | None] = mapped_column(String(30))
    payment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    idempotency_key: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
