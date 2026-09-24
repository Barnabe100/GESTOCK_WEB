import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFoundError
from app.modules.customers.models import Customer, CustomerType
from app.modules.customers.schemas import CustomerCreate, CustomerUpdate
from app.platform.audit.service import audit_action, changes
from app.platform.context import RequestContext
from app.platform.sequences.service import next_number
from app.shared.pagination import (
    PageParams,
    apply_sort,
    escape_like,
    paginate,
    search_filter,
    text_sort,
)
from app.shared.schemas import StatusFilter
from app.shared.text import phone_digits

SEQUENCE_KEY = "customer"
CODE_PREFIX = "CLI"

CENT = Decimal("0.01")


def _normalized(values: dict[str, Any]) -> dict[str, Any]:
    """Montant stocké en NUMERIC(18,2) : même représentation dans la réponse et l'audit."""
    if values.get("credit_limit") is not None:
        values["credit_limit"] = values["credit_limit"].quantize(CENT)
    return values


SORTABLE = {
    "code": Customer.code,
    "name": text_sort(Customer.name),
    "city": text_sort(Customer.city),
    "created_at": Customer.created_at,
}


class CustomerService:
    """Référentiel clients du tenant. Ne valide pas la transaction (ADR-0008) : l'endpoint
    appelle ``commit()`` ; l'audit est écrit dans la même transaction."""

    def __init__(self, db: Session, ctx: RequestContext) -> None:
        self.db = db
        self.ctx = ctx

    def search(
        self,
        params: PageParams,
        search: str | None,
        status: StatusFilter,
        customer_type: CustomerType | None = None,
    ) -> tuple[list[Customer], int]:
        stmt = select(Customer)
        condition = search_filter(
            search,
            Customer.code,
            Customer.name,
            Customer.legal_name,
            Customer.email,
            Customer.phone,
            Customer.phone2,
        )
        if condition is not None:
            # « 70 11 22 33 » trouve le téléphone stocké normalisé « 70112233 ».
            digits = phone_digits(search or "")
            if digits:
                pattern = f"%{escape_like(digits)}%"
                condition = or_(
                    condition,
                    Customer.phone.ilike(pattern, escape="\\"),
                    Customer.phone2.ilike(pattern, escape="\\"),
                )
            stmt = stmt.where(condition)
        if status is not StatusFilter.ALL:
            stmt = stmt.where(Customer.is_active.is_(status is StatusFilter.ACTIVE))
        if customer_type is not None:
            stmt = stmt.where(Customer.customer_type == customer_type)
        stmt = apply_sort(stmt, params.sort, SORTABLE, "name", Customer.id)
        return paginate(self.db, stmt, params)

    def get(self, customer_id: uuid.UUID) -> Customer:
        # RLS + filtre ORM : un client d'un autre tenant est introuvable.
        customer = self.db.get(Customer, customer_id)
        if customer is None:
            raise NotFoundError("Client introuvable", code="customer_not_found")
        return customer

    def create(self, data: CustomerCreate) -> Customer:
        customer = Customer(
            tenant_id=self.ctx.tenant_id,
            code=next_number(self.db, self.ctx.tenant_id, SEQUENCE_KEY, CODE_PREFIX),
            is_active=True,
            **_normalized(data.model_dump()),
        )
        self.db.add(customer)
        self.db.flush()
        audit_action(
            self.db,
            self.ctx,
            "customer.created",
            entity_type="customer",
            entity_id=customer.id,
            data={
                "code": customer.code,
                "name": customer.name,
                "customer_type": customer.customer_type.value,
            },
        )
        return customer

    def update(self, customer_id: uuid.UUID, data: CustomerUpdate) -> Customer:
        customer = self.get(customer_id)
        updates = _normalized(data.model_dump(exclude_unset=True))
        for required in ("name", "customer_type"):
            if required in updates and updates[required] is None:
                raise AppError(
                    "Champ obligatoire manquant",
                    code="validation_error",
                    extra={"fields": [required]},
                )
        before = {key: getattr(customer, key) for key in updates}
        for key, value in updates.items():
            setattr(customer, key, value)
        self.db.flush()
        diff = changes(before, updates)
        if diff:
            audit_action(
                self.db,
                self.ctx,
                "customer.updated",
                entity_type="customer",
                entity_id=customer.id,
                data={"code": customer.code, **diff},
            )
        return customer

    def set_active(self, customer_id: uuid.UUID, active: bool) -> Customer:
        """Désactivation logique (jamais de suppression) : le client reste consultable mais
        n'est plus proposé pour une nouvelle opération commerciale."""
        customer = self.get(customer_id)
        if customer.is_active != active:
            customer.is_active = active
            self.db.flush()
            audit_action(
                self.db,
                self.ctx,
                "customer.activated" if active else "customer.deactivated",
                entity_type="customer",
                entity_id=customer.id,
                data={"code": customer.code, "name": customer.name},
            )
        return customer
