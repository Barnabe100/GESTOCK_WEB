"""Lectures publiques des commandes par QR Code (palier R7, ADR-0052)."""

from sqlalchemy.orm import Session

from app.modules.restaurant_menu.api import site_menu
from app.modules.restaurant_qr.access import QrAccess
from app.modules.restaurant_qr.public_schemas import (
    PublicMenuItem,
    PublicMenuOut,
    PublicMenuSection,
    PublicPoint,
)

# Seul motif « non commandable » montré au public : épuisé (signalé par le personnel). Les
# autres motifs (article désactivé, hors assortiment, conditionnement sans prix…) sont internes :
# l'élément est simplement absent du menu public.
PUBLIC_BLOCKERS = frozenset({"unavailable"})


def point_label(access: QrAccess) -> str:
    if access.table is not None:
        return access.table.label
    return access.point.label or ""


def public_menu(db: Session, access: QrAccess) -> PublicMenuOut:
    sections, items = site_menu(db, access.scope, access.site_id)
    by_section: dict[object, list[PublicMenuItem]] = {s.id: [] for s in sections}
    for item in items:
        if item.price is None or not set(item.blockers) <= PUBLIC_BLOCKERS:
            continue
        by_section[item.section_id].append(
            PublicMenuItem(
                id=item.id,
                name=item.display_name or item.designation,
                packaging_name=item.packaging_name,
                description=item.description,
                price=item.price,
                available=item.orderable,
            )
        )
    tenant = access.scope.tenant
    return PublicMenuOut(
        establishment=tenant.trade_name or tenant.name,
        site_name=access.scope.site.name,
        point=PublicPoint(kind=access.point.kind, label=point_label(access)),
        currency=tenant.currency,
        sections=[
            PublicMenuSection(id=s.id, name=s.name, items=by_section[s.id])
            for s in sections
            if by_section[s.id]
        ],
    )
