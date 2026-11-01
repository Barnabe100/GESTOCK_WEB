"""Limite de fréquence par fenêtre glissante, persistée en base : correcte avec plusieurs
processus ou instances (aucun cache partagé n'existe dans la pile). Les tentatives d'une même
clé sont sérialisées par un verrou consultatif transactionnel.

Clés (adresses IP, points QR) jamais stockées en clair : empreinte HMAC-SHA256 avec la clé
secrète du serveur (``SM_RATE_LIMIT_HMAC_KEY``, R7) — sans elle, une empreinte ne permet pas de
retrouver une adresse IP par essais."""

import hashlib
import hmac
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.errors import TooManyRequestsError
from app.platform.ratelimit.models import RateLimitHit


class RateLimiter:
    def __init__(self, session: Session, now: datetime, secret: str) -> None:
        self.db = session
        self.now = now
        self._secret = secret.encode()

    def key_hash(self, bucket: str, key: str) -> str:
        return hmac.new(self._secret, f"{bucket}:{key}".encode(), hashlib.sha256).hexdigest()

    def hit(self, bucket: str, key: str, *, limit: int, window: timedelta) -> None:
        """Compte une tentative ; ``TooManyRequestsError`` si ``limit`` tentatives ont déjà eu
        lieu dans la fenêtre. La tentative refusée n'est pas comptée. N'effectue pas de
        commit : l'appelant valide la tentative (avant l'opération protégée, pour qu'un échec
        de celle-ci reste compté)."""
        key_hash = self.key_hash(bucket, key)
        self.db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key_hash, 0))))
        since = self.now - window
        self.db.execute(
            delete(RateLimitHit).where(
                RateLimitHit.bucket == bucket,
                RateLimitHit.key_hash == key_hash,
                RateLimitHit.created_at <= since,
            )
        )
        hits = list(
            self.db.scalars(
                select(RateLimitHit.created_at)
                .where(RateLimitHit.bucket == bucket, RateLimitHit.key_hash == key_hash)
                .order_by(RateLimitHit.created_at)
            )
        )
        if len(hits) >= limit:
            retry_after = max(1, int((hits[0] + window - self.now).total_seconds()) + 1)
            raise TooManyRequestsError(
                "Trop de tentatives. Réessayez plus tard.", retry_after=retry_after
            )
        self.db.add(RateLimitHit(bucket=bucket, key_hash=key_hash, created_at=self.now))
        self.db.flush()
