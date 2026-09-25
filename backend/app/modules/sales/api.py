"""API publique du module Ventes pour les autres modules (créances, futurs caisse et POS), qui
n'importent jamais ses modèles directement (règle d'architecture 10). ``register_cash_ledger`` :
implémentation de la caisse des paiements espèces (module Caisse)."""

from app.modules.sales.cash_port import CashLedger, register_cash_ledger
from app.modules.sales.credit import balances_query, customer_exposure, open_receivables_query
from app.modules.sales.models import PaymentStatus, SaleChannel, SalePaymentStatus
from app.modules.sales.payment_service import PaymentService, payment_status
from app.modules.sales.schemas import CheckoutOut, PaymentOut, SaleCheckout
from app.modules.sales.service import SaleService

__all__ = [
    "CheckoutOut",
    "SaleChannel",
    "SaleCheckout",
    "SaleService",
    "CashLedger",
    "register_cash_ledger",
    "PaymentOut",
    "PaymentService",
    "PaymentStatus",
    "SalePaymentStatus",
    "balances_query",
    "customer_exposure",
    "open_receivables_query",
    "payment_status",
]
