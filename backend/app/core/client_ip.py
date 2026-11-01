"""Adresse IP du client (R7, ADR-0052) : seule source pour la limitation de fréquence.

L'adresse de la connexion TCP fait foi. L'en-tête ``X-Forwarded-For`` n'est lu QUE si cette
connexion vient d'un proxy de confiance déclaré (``SM_TRUSTED_PROXIES``, adresses ou réseaux) :
il est alors parcouru de droite à gauche en sautant les proxies de confiance, et la première
adresse valide qui n'en est pas un est retenue. Une adresse transmise par un client quelconque
n'est jamais acceptée."""

import ipaddress

from fastapi import Request

from app.core.config import Settings

UNKNOWN = "unknown"


def _parse(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def client_ip(request: Request, settings: Settings) -> str:
    peer = _parse(request.client.host) if request.client else None
    if peer is None:
        return UNKNOWN
    trusted = settings.trusted_proxy_networks
    if not trusted or not any(peer in network for network in trusted):
        return str(peer)
    forwarded = ",".join(request.headers.getlist("x-forwarded-for"))
    for hop in reversed([part for part in forwarded.split(",") if part.strip()]):
        address = _parse(hop)
        if address is None:
            break  # en-tête altéré : on s'arrête au dernier proxy de confiance
        if not any(address in network for network in trusted):
            return str(address)
    return str(peer)
