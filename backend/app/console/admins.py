"""Administrateurs TechNova : création et révocation **par la CLI uniquement** (ADR-0031).

Aucune route (ni de la console, ni de l'API des tenants) ne permet d'attribuer ou de retirer
``is_platform_admin`` : le rôle applicatif n'a aucun droit d'écriture sur cette colonne et le
rôle de la console non plus. La CLI utilise le rôle propriétaire de la base.

Un administrateur TechNova est un compte **dédié** : un e-mail déjà utilisé (compte d'une
entreprise) est refusé ; le compte n'a aucune appartenance à un tenant et reste invisible pour
l'application des tenants.
"""

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.console.audit import PlatformActor, record_platform_audit
from app.console.models import PlatformSession
from app.core.config import Settings
from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.security import hash_password
from app.platform.identity.models import User
from app.platform.identity.passwords import normalize_email, validate_new_password
from app.shared.clock import utcnow


def create_platform_admin(
    db: Session,
    settings: Settings,
    *,
    email: str,
    full_name: str,
    password: str,
    actor: PlatformActor,
) -> User:
    try:
        normalized = normalize_email(validate_email(email, check_deliverability=False).normalized)
    except EmailNotValidError as exc:
        raise BusinessRuleError("Email invalide", code="invalid_email") from exc
    name = full_name.strip()
    if not name:
        raise BusinessRuleError("Le nom est obligatoire", code="name_required")
    if db.scalars(select(User.id).where(User.email == normalized)).first() is not None:
        raise ConflictError(
            "Cet e-mail est déjà utilisé : un administrateur TechNova a un compte dédié",
            code="user_exists",
        )
    validate_new_password(password, email=normalized, settings=settings)
    user = User(
        email=normalized,
        full_name=name,
        password_hash=hash_password(password),
        must_change_password=False,
        is_platform_admin=True,
    )
    db.add(user)
    db.flush()
    record_platform_audit(
        db,
        actor=actor,
        action="platform_admin.created",
        target_type="user",
        target_id=user.id,
        after={"email": user.email, "full_name": user.full_name, "is_platform_admin": True},
    )
    return user


def revoke_platform_admin(db: Session, *, email: str, actor: PlatformActor) -> User:
    """Retire le statut et ferme le compte dédié (inactif, sessions de la console révoquées)."""
    user = db.scalars(select(User).where(User.email == normalize_email(email))).one_or_none()
    if user is None:
        raise NotFoundError("Utilisateur introuvable", code="user_not_found")
    if not user.is_platform_admin:
        raise BusinessRuleError(
            "Ce compte n'est pas administrateur TechNova", code="not_platform_admin"
        )
    user.is_platform_admin = False
    user.is_active = False
    now = utcnow()
    for session in db.scalars(
        select(PlatformSession).where(
            PlatformSession.user_id == user.id, PlatformSession.revoked_at.is_(None)
        )
    ):
        session.revoked_at = now
    record_platform_audit(
        db,
        actor=actor,
        action="platform_admin.revoked",
        target_type="user",
        target_id=user.id,
        before={"is_platform_admin": True, "is_active": True},
        after={"is_platform_admin": False, "is_active": False},
    )
    return user


def list_platform_admins(db: Session) -> list[User]:
    return list(
        db.scalars(select(User).where(User.is_platform_admin.is_(True)).order_by(User.email))
    )
