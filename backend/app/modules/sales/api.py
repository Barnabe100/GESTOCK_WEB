"""API publique du module Ventes pour les autres modules (créances, futurs caisse et POS), qui
n'importent jamais ses modèles directement (règle d'architecture 10)."""

from app.modules.sales.credit import balances_query, customer_exposure, open_receivables_query
from app.modules.sales.models import PaymentStatus, SalePaymentStatus
from app.modules.sales.payment_service import PaymentService, payment_status
from app.modules.sales.schemas import PaymentOut

__all__ = [
    "PaymentOut",
    "PaymentService",
    "PaymentStatus",
    "SalePaymentStatus",
    "balances_query",
    "customer_exposure",
    "open_receivables_query",
    "payment_status",
]
