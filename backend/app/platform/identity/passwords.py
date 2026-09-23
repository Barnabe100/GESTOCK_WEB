from app.core.config import Settings
from app.core.errors import BusinessRuleError


def validate_new_password(password: str, *, email: str, settings: Settings) -> None:
    if len(password) < settings.password_min_length:
        raise BusinessRuleError(
            f"Le mot de passe doit contenir au moins {settings.password_min_length} caractères",
            code="password_too_short",
            extra={"min_length": settings.password_min_length},
        )
    if password.strip().lower() == email.lower():
        raise BusinessRuleError(
            "Le mot de passe ne peut pas être l'adresse email", code="password_too_weak"
        )


def normalize_email(email: str) -> str:
    return email.strip().lower()
