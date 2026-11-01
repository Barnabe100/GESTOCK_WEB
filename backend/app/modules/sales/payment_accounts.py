"""Identifiants de réception des moyens de paiement (R6.5-D, ADR-0051).

Un identifiant (téléphone, code marchand, code agent, numéro de compte, identifiant
personnalisé) indique où l'entreprise reçoit les fonds d'un moyen :

- **propre à un site** (``site_id``) : géré avec ``sales.payment_method.manage`` détenue sur CE
  site, visible seulement par qui accède à ce site ;
- **commun à tous les sites** (``site_id`` nul) : géré seulement par qui détient la permission
  sur TOUS les sites de l'entreprise (un gestionnaire limité à un site ne modifie jamais ce qui
  vaut pour les autres) ; visible sur chaque site.

Aucun nombre maximal ; plusieurs identifiants pour un même moyen et un même site. Jamais
supprimé : désactivé. Le format n'est contrôlé que par une règle DÉCLARÉE dans le catalogue de
l'opérateur du moyen (longueur, motif) ; sinon seul un contrôle générique s'applique (texte
non vide, caractères imprimables ; téléphone : chiffres et « + » initial, séparateurs retirés,
jamais réécrit avec un indicatif deviné). Le service ne valide pas la transaction.
"""

import re
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, ForbiddenError, NotFoundError
from app.modules.sales.models import (
    ConfiguredPaymentMethod,
    PaymentMethod,
    PaymentMethodAccount,
    ReceivingIdentifierType,
)
from app.modules.sales.schemas import (
    PaymentAccountCreate,
    PaymentAccountOut,
    PaymentAccountUpdate,
)
from app.platform.audit.service import audit_action
from app.platform.catalog.payment_providers import get_provider
from app.platform.context import RequestContext
from app.platform.tenancy.models import Site
from app.shared.text import normalize_phone

MANAGE = "sales.payment_method.manage"
NOT_FOUND = "payment_account_not_found"
_PRINTABLE = re.compile(r"^[^\x00-\x1f\x7f]+$")


class PaymentAccountService:
    def __init__(self, db: Session, ctx: RequestContext, now: datetime) -> None:
        self.db = db
        self.ctx = ctx
        self.now = now

    # --- Portée et droits --------------------------------------------------------------------

    def _visible_sites(self) -> frozenset[uuid.UUID]:
        return self.ctx.capabilities.accessible_site_ids

    def _ensure_can_manage(self, site_id: uuid.UUID | None) -> None:
        """Site : permission sur CE site ; commun : permission sur TOUS les sites."""
        if site_id is not None:
            if site_id not in self._visible_sites():
                raise NotFoundError("Site introuvable", code="site_not_found")
            if not self.ctx.has_site_permission(site_id, MANAGE):
                raise ForbiddenError(
                    "Permission insuffisante sur ce site", code="permission_denied"
                )
            self.ctx.ensure_site_allows(site_id)
            return
        all_sites = set(self.db.scalars(select(Site.id)))
        if not all(self.ctx.has_site_permission(s, MANAGE) for s in all_sites):
            raise ForbiddenError(
                "Un identifiant commun à tous les sites exige la permission sur chaque site",
                code="permission_denied",
                extra={"scope": "all_sites"},
            )

    def _method(self, method_id: uuid.UUID, *, lock: bool = False) -> ConfiguredPaymentMethod:
        stmt = select(ConfiguredPaymentMethod).where(ConfiguredPaymentMethod.id == method_id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        method = self.db.scalars(stmt).one_or_none()
        if method is None:
            raise NotFoundError("Moyen de paiement introuvable", code="payment_method_not_found")
        return method

    def get(self, account_id: uuid.UUID, *, lock: bool = False) -> PaymentMethodAccount:
        """Identifiant visible : commun, ou d'un site accessible (sinon introuvable)."""
        stmt = select(PaymentMethodAccount).where(PaymentMethodAccount.id == account_id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        account = self.db.scalars(stmt).one_or_none()
        if account is None or (
            account.site_id is not None and account.site_id not in self._visible_sites()
        ):
            raise NotFoundError("Identifiant de réception introuvable", code=NOT_FOUND)
        return account

    # --- Lecture -------------------------------------------------------------------------------

    def list_for_method(
        self, method_id: uuid.UUID, site_id: uuid.UUID | None = None, *, active_only: bool = False
    ) -> list[PaymentMethodAccount]:
        """Identifiants d'un moyen : communs et propres aux sites accessibles (ou à ``site_id``
        seulement, en plus des communs)."""
        self._method(method_id)
        visible = self._visible_sites()
        if site_id is not None and site_id not in visible:
            raise NotFoundError("Site introuvable", code="site_not_found")
        scope = [site_id] if site_id is not None else list(visible)
        stmt = select(PaymentMethodAccount).where(
            PaymentMethodAccount.payment_method_id == method_id,
            or_(PaymentMethodAccount.site_id.is_(None), PaymentMethodAccount.site_id.in_(scope)),
        )
        if active_only:
            stmt = stmt.where(PaymentMethodAccount.is_active.is_(True))
        return list(self.db.scalars(stmt.order_by(*_ORDER)))

    def usable_on_site(
        self, method_ids: Sequence[uuid.UUID], site_id: uuid.UUID
    ) -> dict[uuid.UUID, list[PaymentMethodAccount]]:
        """Identifiants ACTIFS utilisables sur ``site_id`` (communs + propres au site), par moyen
        (une seule requête) ; ``site_id`` doit être accessible (vérifié par l'appelant)."""
        result: dict[uuid.UUID, list[PaymentMethodAccount]] = {}
        if not method_ids:
            return result
        rows = self.db.scalars(
            select(PaymentMethodAccount)
            .where(
                PaymentMethodAccount.payment_method_id.in_(method_ids),
                PaymentMethodAccount.is_active.is_(True),
                or_(
                    PaymentMethodAccount.site_id.is_(None),
                    PaymentMethodAccount.site_id == site_id,
                ),
            )
            .order_by(*_ORDER)
        )
        for account in rows:
            result.setdefault(account.payment_method_id, []).append(account)
        return result

    # --- Contrôle d'un identifiant ----------------------------------------------------------

    def _checked_value(
        self,
        method: ConfiguredPaymentMethod,
        identifier_type: ReceivingIdentifierType,
        raw: str,
    ) -> str:
        value = raw.strip()
        if not value or not _PRINTABLE.match(value):
            raise BusinessRuleError(
                "Identifiant invalide", code="payment_account_invalid", extra={"reason": "format"}
            )
        if identifier_type is ReceivingIdentifierType.PHONE:
            try:
                value = normalize_phone(value) or value
            except ValueError as exc:
                raise BusinessRuleError(
                    "Numéro de téléphone invalide",
                    code="payment_account_invalid",
                    extra={"reason": "phone"},
                ) from exc
        provider = get_provider(self.db, method.provider_code) if method.provider_code else None
        if provider is None or not provider.identifiers:
            return value  # opérateur personnalisé ou sans règle déclarée : contrôle générique
        if identifier_type.value not in provider.identifiers:
            raise BusinessRuleError(
                "Ce type d'identifiant n'est pas accepté par l'opérateur",
                code="payment_account_type_not_accepted",
                extra={"accepted": sorted(provider.identifiers)},
            )
        rule: dict[str, Any] = provider.identifiers[identifier_type.value] or {}
        too_short = rule.get("min_length") is not None and len(value) < rule["min_length"]
        too_long = rule.get("max_length") is not None and len(value) > rule["max_length"]
        bad_pattern = rule.get("pattern") is not None and not re.fullmatch(rule["pattern"], value)
        if too_short or too_long or bad_pattern:
            raise BusinessRuleError(
                "L'identifiant ne respecte pas le format de l'opérateur",
                code="payment_account_invalid",
                extra={"reason": "provider_rule", **{k: v for k, v in rule.items()}},
            )
        return value

    def _ensure_unique(
        self,
        method_id: uuid.UUID,
        site_id: uuid.UUID | None,
        identifier_type: ReceivingIdentifierType,
        value: str,
        exclude: uuid.UUID | None = None,
    ) -> None:
        stmt = select(PaymentMethodAccount.id).where(
            PaymentMethodAccount.payment_method_id == method_id,
            PaymentMethodAccount.site_id.is_(None)
            if site_id is None
            else PaymentMethodAccount.site_id == site_id,
            PaymentMethodAccount.identifier_type == identifier_type,
            PaymentMethodAccount.value == value,
            PaymentMethodAccount.is_active.is_(True),
        )
        if exclude is not None:
            stmt = stmt.where(PaymentMethodAccount.id != exclude)
        if self.db.scalar(stmt) is not None:
            raise ConflictError(
                "Cet identifiant est déjà enregistré pour ce moyen",
                code="payment_account_duplicate",
            )

    # --- Écritures -----------------------------------------------------------------------------

    def create(self, method_id: uuid.UUID, data: PaymentAccountCreate) -> PaymentMethodAccount:
        self._ensure_can_manage(data.site_id)
        # Verrou du moyen : sérialise les créations concurrentes (doublons) et ses modifications.
        method = self._method(method_id, lock=True)
        if method.kind is PaymentMethod.CASH:
            raise BusinessRuleError(
                "Les espèces n'ont pas d'identifiant de réception",
                code="payment_account_not_allowed",
            )
        if data.identifier_type is ReceivingIdentifierType.CUSTOM and not data.custom_label:
            raise BusinessRuleError(
                "Un identifiant personnalisé exige un libellé",
                code="payment_account_label_required",
            )
        value = self._checked_value(method, data.identifier_type, data.value)
        self._ensure_unique(method.id, data.site_id, data.identifier_type, value)
        account = PaymentMethodAccount(
            tenant_id=self.ctx.tenant_id,
            payment_method_id=method.id,
            site_id=data.site_id,
            identifier_type=data.identifier_type,
            custom_label=(
                data.custom_label
                if data.identifier_type is ReceivingIdentifierType.CUSTOM
                else None
            ),
            value=value,
            holder_name=data.holder_name,
            instructions=data.instructions,
            sort_order=data.sort_order,
            is_active=True,
            created_by=self.ctx.user.id,
            updated_by=self.ctx.user.id,
        )
        self.db.add(account)
        self.db.flush()
        self._audit("created", account, method, {"after": _snapshot(account)})
        return account

    def update(self, account_id: uuid.UUID, data: PaymentAccountUpdate) -> PaymentMethodAccount:
        account = self.get(account_id)
        self._ensure_can_manage(account.site_id)
        method = self._method(account.payment_method_id, lock=True)
        account = self.get(account_id, lock=True)
        before = _snapshot(account)
        changes = data.model_dump(exclude_unset=True)
        if "value" in changes and changes["value"] is not None:
            value = self._checked_value(method, account.identifier_type, changes["value"])
            if account.is_active:
                self._ensure_unique(
                    method.id, account.site_id, account.identifier_type, value, exclude=account.id
                )
            account.value = value
        if "custom_label" in changes and (
            account.identifier_type is ReceivingIdentifierType.CUSTOM
        ):
            if not changes["custom_label"]:
                raise BusinessRuleError(
                    "Un identifiant personnalisé exige un libellé",
                    code="payment_account_label_required",
                )
            account.custom_label = changes["custom_label"]
        for field in ("holder_name", "instructions"):
            if field in changes:
                setattr(account, field, changes[field])
        if changes.get("sort_order") is not None:
            account.sort_order = changes["sort_order"]
        account.updated_by = self.ctx.user.id
        self.db.flush()
        after = _snapshot(account)
        if after != before:
            self._audit("updated", account, method, {"before": before, "after": after})
        return account

    def set_active(self, account_id: uuid.UUID, active: bool) -> PaymentMethodAccount:
        """Désactivation (jamais de suppression) ; réactivation revérifiée (format, doublon)."""
        account = self.get(account_id)
        self._ensure_can_manage(account.site_id)
        method = self._method(account.payment_method_id, lock=True)
        account = self.get(account_id, lock=True)
        if account.is_active is active:
            return account
        if active:
            account.value = self._checked_value(method, account.identifier_type, account.value)
            self._ensure_unique(
                method.id, account.site_id, account.identifier_type, account.value, account.id
            )
        account.is_active = active
        account.updated_by = self.ctx.user.id
        self.db.flush()
        self._audit("activated" if active else "deactivated", account, method, _snapshot(account))
        return account

    # --- Audit et sortie API ------------------------------------------------------------------

    def _audit(
        self,
        action: str,
        account: PaymentMethodAccount,
        method: ConfiguredPaymentMethod,
        data: dict[str, Any],
    ) -> None:
        audit_action(
            self.db,
            self.ctx,
            f"payment_method_account.{action}",
            entity_type="payment_method_account",
            entity_id=account.id,
            site_id=account.site_id,
            data={"payment_method_id": str(method.id), "method_label": method.label, **data},
        )

    def to_out(self, accounts: Sequence[PaymentMethodAccount]) -> list[PaymentAccountOut]:
        site_ids = {a.site_id for a in accounts if a.site_id is not None}
        names: dict[uuid.UUID, str] = {}
        if site_ids:
            rows = self.db.execute(select(Site.id, Site.name).where(Site.id.in_(site_ids)))
            names = {site_id: name for site_id, name in rows}
        return [
            PaymentAccountOut(
                id=a.id,
                payment_method_id=a.payment_method_id,
                site_id=a.site_id,
                site_name=names.get(a.site_id) if a.site_id else None,
                shared=a.site_id is None,
                identifier_type=a.identifier_type,
                custom_label=a.custom_label,
                value=a.value,
                holder_name=a.holder_name,
                instructions=a.instructions,
                sort_order=a.sort_order,
                is_active=a.is_active,
                created_at=a.created_at,
                updated_at=a.updated_at,
            )
            for a in accounts
        ]


_ORDER = (
    PaymentMethodAccount.sort_order,
    PaymentMethodAccount.site_id.is_not(None),
    PaymentMethodAccount.created_at,
    PaymentMethodAccount.id,
)


def _snapshot(account: PaymentMethodAccount) -> dict[str, Any]:
    return {
        "site_id": str(account.site_id) if account.site_id else None,
        "identifier_type": account.identifier_type.value,
        "custom_label": account.custom_label,
        "value": account.value,
        "holder_name": account.holder_name,
        "instructions": account.instructions,
        "sort_order": account.sort_order,
        "is_active": account.is_active,
    }
