"""Vocabulaire TECHNIQUE des moyens de réception (R6.5, ADR-0051).

Ces codes décrivent un COMPORTEMENT, jamais un pays, un opérateur ni une devise :

- ``RECEIVING_KINDS`` : types de moyens (``CASH`` rend la monnaie et passe par la caisse) ; même
  liste que ``app.modules.sales.models.PaymentMethod`` (vérifié par les tests) ;
- ``IDENTIFIER_TYPES`` : nature d'un identifiant de réception. ``PHONE`` est normalisé avec
  l'indicatif du pays du moyen ; les autres sont des textes libres, contrôlés seulement par une
  règle déclarée dans le catalogue des opérateurs ; ``CUSTOM`` exige un libellé.

Les opérateurs et établissements financiers sont des DONNÉES (``payment_providers.toml``,
opérateurs personnalisés des entreprises) : aucun nom d'opérateur dans le code.
"""

RECEIVING_KINDS: tuple[str, ...] = ("CASH", "MOBILE_MONEY", "CARD", "BANK_TRANSFER", "OTHER")

IDENTIFIER_TYPES: tuple[str, ...] = (
    "PHONE",
    "MERCHANT_CODE",
    "AGENT_CODE",
    "ACCOUNT_NUMBER",
    "CUSTOM",
)

# Langues des traductions acceptées dans le catalogue (codes BCP 47 simples : « fr », « en »…).
LANGUAGE_RE = r"^[a-z]{2,3}(-[A-Z]{2})?$"
# Code stable d'un opérateur du catalogue.
PROVIDER_CODE_RE = r"^[a-z0-9][a-z0-9_.-]{1,49}$"
