"""Moyens de paiement configurables (Lot 1, ADR-0037).

Chaque entreprise configure ses moyens (« Espèces », « Mobile Money », « Virement »…). Le **type**
(``CASH``, ``MOBILE_MONEY``, ``CARD``, ``BANK_TRANSFER``, ``OTHER``) gouverne le comportement —
jamais le libellé : seul ``CASH`` rend la monnaie et passe par la caisse du site. Saisie manuelle
(montant, référence) ; le mode ``API`` est réservé à une intégration future qui ne changera pas
le modèle du paiement. Un moyen est disponible sur tous les sites, sauf désactivation pour un
site ; il n'est jamais supprimé (désactivé), et son type ne change jamais (historique).

R6.5 (ADR-0051) : un moyen porte un opérateur (catalogue TechNova OU opérateur personnalisé OU
aucun — le catalogue n'est jamais requis), un pays (référentiel ; aucun = international) et une
devise ISO 4217. Il n'encaisse une vente que dans SA devise (celle des ventes de l'entreprise) :
aucune conversion. Pays et devise par défaut : ceux de l'entreprise (référentiel des pays).
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.modules.sales.models import (
    ConfiguredPaymentMethod,
    PaymentIntegration,
    PaymentMethod,
    PaymentMethodSite,
)
from app.modules.sales.schemas import (
    PaymentMethodCreate,
    PaymentMethodOut,
    PaymentMethodUpdate,
)
from app.platform.audit.service import audit_action
from app.platform.catalog.models import GeoCountry, PaymentProvider
from app.platform.catalog.payment_providers import get_provider, localized
from app.platform.context import RequestContext
from app.platform.tenancy.models import Tenant

# Données de départ d'une nouvelle entreprise (modifiables ensuite) : type, libellé, ordre.
DEFAULT_PAYMENT_METHODS: tuple[tuple[PaymentMethod, str, int], ...] = (
    (PaymentMethod.CASH, "Espèces", 10),
    (PaymentMethod.MOBILE_MONEY, "Mobile Money", 20),
    (PaymentMethod.CARD, "Carte bancaire", 30),
    (PaymentMethod.BANK_TRANSFER, "Virement", 40),
    (PaymentMethod.OTHER, "Autre", 50),
)
NOT_FOUND = "payment_method_not_found"


def ensure_default_payment_methods(db: Session, tenant_id: uuid.UUID) -> None:
    """Moyens par défaut d'une nouvelle entreprise (idempotent ; ``tenant_setup``), dans le pays
    et la devise de l'entreprise, sans opérateur."""
    tenant = db.get(Tenant, tenant_id)
    assert tenant is not None
    existing = set(db.scalars(select(ConfiguredPaymentMethod.label)))
    for kind, label, order in DEFAULT_PAYMENT_METHODS:
        if label not in existing:
            db.add(
                ConfiguredPaymentMethod(
                    tenant_id=tenant_id,
                    label=label,
                    kind=kind,
                    sort_order=order,
                    country_code=tenant.country_code,
                    currency=tenant.currency,
                )
            )


# Raisons d'indisponibilité d'un moyen sur un site (calculées par le serveur).
INACTIVE = "inactive"
SITE_DISABLED = "site_disabled"
CURRENCY_MISMATCH = "currency_mismatch"


class PaymentMethodService:
    def __init__(self, db: Session, ctx: RequestContext, now: datetime) -> None:
        self.db = db
        self.ctx = ctx
        self.now = now

    # --- Lecture ------------------------------------------------------------------------------

    def get(self, method_id: uuid.UUID, *, lock: bool = False) -> ConfiguredPaymentMethod:
        stmt = select(ConfiguredPaymentMethod).where(ConfiguredPaymentMethod.id == method_id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        method = self.db.scalars(stmt).one_or_none()
        if method is None:
            raise NotFoundError("Moyen de paiement introuvable", code=NOT_FOUND)
        return method

    def all(self) -> list[ConfiguredPaymentMethod]:
        return list(
            self.db.scalars(
                select(ConfiguredPaymentMethod).order_by(
                    ConfiguredPaymentMethod.sort_order, ConfiguredPaymentMethod.label
                )
            )
        )

    def disabled_sites(self, method_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[uuid.UUID]]:
        """Sites où chaque moyen est désactivé (une seule requête)."""
        result: dict[uuid.UUID, list[uuid.UUID]] = {}
        if not method_ids:
            return result
        rows = self.db.execute(
            select(PaymentMethodSite.payment_method_id, PaymentMethodSite.site_id).where(
                PaymentMethodSite.payment_method_id.in_(method_ids),
                PaymentMethodSite.is_enabled.is_(False),
            )
        ).all()
        for method_id, site_id in rows:
            result.setdefault(method_id, []).append(site_id)
        return result

    def is_available(self, method: ConfiguredPaymentMethod, site_id: uuid.UUID) -> bool:
        if not method.is_active:
            return False
        enabled = self.db.scalar(
            select(PaymentMethodSite.is_enabled).where(
                PaymentMethodSite.payment_method_id == method.id,
                PaymentMethodSite.site_id == site_id,
            )
        )
        return enabled is not False

    @property
    def sale_currency(self) -> str:
        """Devise des ventes : celle de l'entreprise, figée (aucune colonne redondante)."""
        return self.ctx.tenant.currency

    def resolve(
        self,
        site_id: uuid.UUID,
        method_id: uuid.UUID | None,
        kind: PaymentMethod | None,
    ) -> ConfiguredPaymentMethod:
        """Moyen utilisable pour un paiement sur ``site_id`` : celui demandé, sinon (compatibilité
        de l'API) l'**unique** moyen disponible du type demandé. Sa devise doit être celle de la
        vente (``payment_method_currency_mismatch``) : aucune conversion."""
        if method_id is not None:
            method = self.db.get(ConfiguredPaymentMethod, method_id)
            if method is None:
                raise BusinessRuleError("Moyen de paiement introuvable", code=NOT_FOUND)
            if not self.is_available(method, site_id):
                raise BusinessRuleError(
                    "Ce moyen de paiement n'est pas disponible sur ce site",
                    code="payment_method_unavailable",
                    extra={"label": method.label},
                )
            if method.currency != self.sale_currency:
                raise BusinessRuleError(
                    "La devise de ce moyen de paiement n'est pas celle de la vente",
                    code="payment_method_currency_mismatch",
                    extra={
                        "label": method.label,
                        "currency": method.currency,
                        "sale_currency": self.sale_currency,
                    },
                )
            if kind is not None and method.kind is not kind:
                raise BusinessRuleError(
                    "Le type ne correspond pas au moyen de paiement choisi",
                    code="payment_method_mismatch",
                )
            return method
        assert kind is not None  # garanti par le schéma
        candidates = [
            m
            for m in self.db.scalars(
                select(ConfiguredPaymentMethod).where(
                    ConfiguredPaymentMethod.kind == kind,
                    ConfiguredPaymentMethod.is_active.is_(True),
                    ConfiguredPaymentMethod.currency == self.sale_currency,
                )
            )
            if self.is_available(m, site_id)
        ]
        if len(candidates) != 1:
            raise BusinessRuleError(
                "Choisissez le moyen de paiement",
                code="payment_method_required" if candidates else "payment_method_unavailable",
                extra={"kind": kind.value},
            )
        return candidates[0]

    # --- Écritures ----------------------------------------------------------------------------

    def _check_label(self, label: str, exclude: uuid.UUID | None = None) -> str:
        label = label.strip()
        stmt = select(ConfiguredPaymentMethod.id).where(
            func.lower(ConfiguredPaymentMethod.label) == label.lower()
        )
        if exclude is not None:
            stmt = stmt.where(ConfiguredPaymentMethod.id != exclude)
        if self.db.scalar(stmt) is not None:
            raise ConflictError(
                "Un moyen de paiement porte déjà ce libellé", code="payment_method_label_taken"
            )
        return label

    @staticmethod
    def _check_integration(mode: PaymentIntegration) -> None:
        if mode is not PaymentIntegration.MANUAL:
            raise BusinessRuleError(
                "Aucune intégration de paiement n'est disponible : saisie manuelle seulement",
                code="payment_integration_unavailable",
            )

    def _check_reference_data(
        self, method: ConfiguredPaymentMethod, *, provider_changed: bool
    ) -> None:
        """Pays et devise du référentiel ; opérateur du catalogue cohérent (type, devise, pays).
        Un opérateur personnalisé n'est contrôlé que sur sa forme (jamais requis)."""
        if method.country_code is not None and self.db.get(GeoCountry, method.country_code) is None:
            raise BusinessRuleError(
                "Pays inconnu du référentiel",
                code="country_unknown",
                extra={"country_code": method.country_code},
            )
        known_currency = self.db.scalar(
            select(GeoCountry.code).where(GeoCountry.currency == method.currency).limit(1)
        )
        if known_currency is None:
            raise BusinessRuleError(
                "Devise inconnue du référentiel",
                code="currency_unknown",
                extra={"currency": method.currency},
            )
        if method.kind is PaymentMethod.CASH and (method.provider_code or method.provider_name):
            raise BusinessRuleError(
                "Les espèces n'ont pas d'opérateur", code="payment_provider_not_allowed"
            )
        if method.provider_code is None:
            return
        provider = get_provider(self.db, method.provider_code)
        if provider is None or (provider_changed and not provider.is_active):
            raise BusinessRuleError(
                "Opérateur inconnu du catalogue",
                code="payment_provider_unknown",
                extra={"provider_code": method.provider_code},
            )
        mismatch = (
            "kind"
            if method.kind.value not in provider.kinds
            else "currency"
            if method.currency not in provider.currencies
            else "country"
            if method.country_code is not None
            and provider.countries
            and method.country_code not in provider.countries
            else None
        )
        if mismatch is not None:
            raise BusinessRuleError(
                "Cet opérateur ne couvre pas ce type, cette devise ou ce pays",
                code="payment_provider_mismatch",
                extra={"provider_code": provider.code, "field": mismatch},
            )

    def create(self, data: PaymentMethodCreate) -> ConfiguredPaymentMethod:
        self._check_integration(data.integration_mode)
        tenant = self.ctx.tenant
        method = ConfiguredPaymentMethod(
            tenant_id=self.ctx.tenant_id,
            label=self._check_label(data.label),
            kind=data.kind,
            integration_mode=data.integration_mode,
            reference_required=data.reference_required,
            sort_order=data.sort_order,
            is_active=True,
            created_by=self.ctx.user.id,
            provider_code=data.provider_code,
            provider_name=data.provider_name,
            # Pays omis : celui de l'entreprise ; ``null`` explicite : international.
            country_code=(
                data.country_code
                if "country_code" in data.model_fields_set
                else tenant.country_code
            ),
            currency=data.currency or tenant.currency,
        )
        self._check_reference_data(method, provider_changed=True)
        self.db.add(method)
        self.db.flush()
        self._audit("created", method, {"after": _snapshot(method)})
        return method

    def update(self, method_id: uuid.UUID, data: PaymentMethodUpdate) -> ConfiguredPaymentMethod:
        method = self.get(method_id, lock=True)
        before = _snapshot(method)
        # Opérateur et pays : ``null`` explicite = retrait ; autres champs : ``null`` ignoré.
        nullable = {"provider_code", "provider_name", "country_code"}
        changes = {
            k: v
            for k, v in data.model_dump(exclude_unset=True).items()
            if v is not None or k in nullable
        }
        if "label" in changes:
            changes["label"] = self._check_label(changes["label"], exclude=method.id)
        if "integration_mode" in changes:
            self._check_integration(changes["integration_mode"])
        # Choisir un opérateur remplace l'autre forme (catalogue ↔ personnalisé).
        if changes.get("provider_code") is not None:
            changes.setdefault("provider_name", None)
        if changes.get("provider_name") is not None:
            changes.setdefault("provider_code", None)
        provider_changed = (
            changes.get("provider_code", method.provider_code) != method.provider_code
        )
        for field, value in changes.items():
            setattr(method, field, value)
        self._check_reference_data(method, provider_changed=provider_changed)
        self.db.flush()
        after = _snapshot(method)
        if after != before:
            self._audit("updated", method, {"before": before, "after": after})
        return method

    def set_site(
        self, method_id: uuid.UUID, site_id: uuid.UUID, enabled: bool
    ) -> ConfiguredPaymentMethod:
        method = self.get(method_id, lock=True)
        self.db.execute(
            insert(PaymentMethodSite)
            .values(
                tenant_id=self.ctx.tenant_id,
                payment_method_id=method.id,
                site_id=site_id,
                is_enabled=enabled,
                updated_at=self.now,
                updated_by=self.ctx.user.id,
            )
            .on_conflict_do_update(
                index_elements=["payment_method_id", "site_id"],
                set_={
                    "is_enabled": enabled,
                    "updated_at": self.now,
                    "updated_by": self.ctx.user.id,
                },
            )
        )
        self.db.flush()
        self._audit(
            "site_enabled" if enabled else "site_disabled",
            method,
            {"label": method.label},
            site_id=site_id,
        )
        return method

    # --- Audit et sortie API ------------------------------------------------------------------

    def _audit(
        self,
        action: str,
        method: ConfiguredPaymentMethod,
        data: dict[str, Any],
        site_id: uuid.UUID | None = None,
    ) -> None:
        audit_action(
            self.db,
            self.ctx,
            f"payment_method.{action}",
            entity_type="payment_method",
            entity_id=method.id,
            site_id=site_id,
            data=data,
        )

    def provider_labels(self, methods: Sequence[ConfiguredPaymentMethod]) -> dict[str, str]:
        """Noms affichés des opérateurs du catalogue (langue de l'entreprise)."""
        codes = {m.provider_code for m in methods if m.provider_code}
        if not codes:
            return {}
        locale = self.ctx.tenant.locale
        return {
            p.code: localized(p.name, p.names, locale) or p.name
            for p in self.db.scalars(select(PaymentProvider).where(PaymentProvider.code.in_(codes)))
        }

    def provider_label(
        self, method: ConfiguredPaymentMethod, labels: dict[str, str] | None = None
    ) -> str | None:
        if method.provider_code:
            labels = labels if labels is not None else self.provider_labels([method])
            return labels.get(method.provider_code, method.provider_code)
        return method.provider_name

    def unavailable_reason(
        self,
        method: ConfiguredPaymentMethod,
        disabled_sites: Sequence[uuid.UUID],
        site_id: uuid.UUID,
    ) -> str | None:
        if not method.is_active:
            return INACTIVE
        if site_id in disabled_sites:
            return SITE_DISABLED
        if method.currency != self.sale_currency:
            return CURRENCY_MISMATCH
        return None

    def to_out(
        self, methods: Sequence[ConfiguredPaymentMethod], site_id: uuid.UUID | None = None
    ) -> list[PaymentMethodOut]:
        disabled = self.disabled_sites([m.id for m in methods])
        labels = self.provider_labels(methods)
        out = []
        for m in methods:
            reason = (
                self.unavailable_reason(m, disabled.get(m.id, []), site_id)
                if site_id is not None
                else None
            )
            out.append(
                PaymentMethodOut(
                    id=m.id,
                    label=m.label,
                    kind=m.kind,
                    integration_mode=m.integration_mode,
                    reference_required=m.reference_required,
                    is_active=m.is_active,
                    sort_order=m.sort_order,
                    disabled_site_ids=disabled.get(m.id, []),
                    provider_code=m.provider_code,
                    provider_name=m.provider_name,
                    provider_label=self.provider_label(m, labels),
                    country_code=m.country_code,
                    currency=m.currency,
                    available=reason is None if site_id is not None else None,
                    unavailable_reason=reason,
                )
            )
        return out


def _snapshot(method: ConfiguredPaymentMethod) -> dict[str, Any]:
    return {
        "label": method.label,
        "kind": method.kind.value,
        "integration_mode": method.integration_mode.value,
        "reference_required": method.reference_required,
        "is_active": method.is_active,
        "sort_order": method.sort_order,
        "provider_code": method.provider_code,
        "provider_name": method.provider_name,
        "country_code": method.country_code,
        "currency": method.currency,
    }
