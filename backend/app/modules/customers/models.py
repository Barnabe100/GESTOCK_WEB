from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.platform.models_base import IdMixin, TenantScopedMixin, TimestampMixin, str_enum

MONEY = Numeric(18, 2)

# Colonnes de la recherche « contient » (caisse / POS) : index trigrammes (pg_trgm), utilisés
# par ILIKE '%…%' sans parcours complet de la table quand le volume grandit.
SEARCH_COLUMNS = ("code", "name", "legal_name", "phone", "phone2", "email")


class CustomerType(StrEnum):
    INDIVIDUAL = "INDIVIDUAL"
    BUSINESS = "BUSINESS"


class Customer(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Client du tenant (pas de site : les futures ventes portent le leur). Jamais supprimé :
    désactivé. Aucun solde stocké : le montant dû sera calculé par le futur module Créances."""

    __tablename__ = "customers"
    __table_args__ = (
        # Référence CLI-000001 attribuée par la séquence du tenant (platform/sequences).
        UniqueConstraint("tenant_id", "code"),
        # Cible des futures FK composites (ventes, créances…).
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint("credit_limit IS NULL OR credit_limit >= 0", name="credit_limit_positive"),
        *(
            Index(
                f"ix_customers_{column}_trgm",
                column,
                postgresql_using="gin",
                postgresql_ops={column: "gin_trgm_ops"},
            )
            for column in SEARCH_COLUMNS
        ),
    )

    code: Mapped[str] = mapped_column(String(20), nullable=False)
    customer_type: Mapped[CustomerType] = mapped_column(
        str_enum(CustomerType, "customer_type"), nullable=False
    )
    # Nom affiché : nom et prénom d'un particulier, nom usuel / enseigne d'une entreprise.
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    # Identifiant fiscal (ex. IFU) : factures et créances futures.
    tax_id: Mapped[str | None] = mapped_column(String(50))
    # Téléphones normalisés (chiffres, « + » initial) : recherche fiable en caisse.
    phone: Mapped[str | None] = mapped_column(String(30))
    phone2: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(150))
    address: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(String(1000))
    # Plafond de crédit (préparation des ventes à crédit) ; nul = non défini. Aucune règle ne
    # l'applique dans cette phase.
    credit_limit: Mapped[Decimal | None] = mapped_column(MONEY)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
