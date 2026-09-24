import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.modules.sales.api import PaymentOut, SalePaymentStatus
from app.shared.schemas import Money


class ReceivableOut(BaseModel):
    """Créance ouverte : vente validée dont le reste dû est strictement positif (calculée)."""

    sale_id: uuid.UUID
    sale_number: str
    sale_date: date
    validated_at: datetime | None
    site_id: uuid.UUID
    site_name: str
    # Vente sans client : reste dû sans débiteur identifié (aucune exposition client).
    customer_id: uuid.UUID | None
    customer_code: str | None
    customer_name: str | None
    customer_is_active: bool | None
    total: Money
    paid_amount: Money
    remaining_amount: Money
    payment_status: SalePaymentStatus


class ReceivableSummary(BaseModel):
    """Indicateurs des créances ouvertes (mêmes filtres que la liste). Pas d'indicateur de
    retard : aucune notion d'échéance n'existe (ADR-0021)."""

    total_receivables: Money
    receivables_count: int
    debtor_customers_count: int


class ReceivableDetail(ReceivableOut):
    """Solde d'une vente validée et historique complet de ses paiements (annulés compris ;
    seuls les paiements effectués réduisent le solde). ``is_open`` : reste dû > 0."""

    is_open: bool
    payments: list[PaymentOut]


class CreditExposureOut(BaseModel):
    """Exposition crédit d'un client.

    - ``credit_limit`` nul : limite **non configurée** (``limit_configured`` faux) ;
      ``available_credit`` est alors nul (aucun montant fabriqué).
    - ``consolidated`` : exposition de tous les sites du tenant (celle du contrôle de la
      limite). Faux pour un membre limité à certains sites : l'exposition ne couvre que ses
      sites et le crédit disponible (consolidé) n'est pas communiqué.
    """

    customer_id: uuid.UUID
    customer_code: str
    customer_name: str
    customer_is_active: bool
    credit_limit: Money | None
    limit_configured: bool
    current_exposure: Money
    available_credit: Money | None
    over_limit: bool | None
    open_receivables_count: int
    consolidated: bool
