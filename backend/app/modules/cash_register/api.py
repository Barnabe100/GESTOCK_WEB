"""API publique du module Caisse pour les autres modules (ventes / paiements, futur POS), qui
n'importent jamais ses modèles directement (règle d'architecture 10)."""

from app.modules.cash_register.service import CashService

__all__ = ["CashService"]
