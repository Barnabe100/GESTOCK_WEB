"""API publique du module Caisse (futur POS, reporting). Les paiements des ventes utilisent la
caisse via le port ``sales.cash_port`` (enregistré par le manifeste), jamais par import."""

from app.modules.cash_register.service import CashService

__all__ = ["CashService"]
