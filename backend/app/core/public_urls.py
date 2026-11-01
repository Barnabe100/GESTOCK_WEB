"""Adresses PUBLIQUES de l'interface (R7, ADR-0052) : seul endroit où elles sont construites.

La base vient de la configuration du serveur (``SM_PUBLIC_APP_URL``, contrôlée au démarrage
contre ``SM_PUBLIC_ALLOWED_HOSTS``) — jamais de l'en-tête ``Host``, de ``X-Forwarded-Host`` ni
d'une adresse transmise par le navigateur. Les secrets (jeton d'un point QR, secret de suivi)
sont placés dans le FRAGMENT (``#``) : un navigateur ne l'envoie jamais au serveur, il
n'apparaît donc ni dans les journaux du serveur ou du proxy ni dans l'en-tête ``Referer``.
"""

from app.core.config import Settings
from app.core.errors import ServiceUnavailableError

# Chemins des pages publiques de l'interface (``frontend/src/app/router.tsx``).
QR_MENU_PATH = "/q"
QR_TRACKING_PATH = "/q/track"


class PublicUrlNotConfiguredError(ServiceUnavailableError):
    code = "public_url_not_configured"
    title = "Adresse publique non configurée"


class PublicUrls:
    def __init__(self, settings: Settings) -> None:
        self._base = settings.public_base_url

    @property
    def configured(self) -> bool:
        return self._base is not None

    def _url(self, path: str, secret: str) -> str:
        if self._base is None:
            raise PublicUrlNotConfiguredError(
                "L'adresse publique de StockManager Web n'est pas configurée (SM_PUBLIC_APP_URL)"
            )
        return f"{self._base}{path}#{secret}"

    def qr_point(self, token: str) -> str:
        """Adresse imprimée dans le QR Code d'un point (table ou comptoir)."""
        return self._url(QR_MENU_PATH, token)

    def qr_tracking(self, secret: str) -> str:
        """Lien de suivi temporaire d'une commande passée par QR Code."""
        return self._url(QR_TRACKING_PATH, secret)
