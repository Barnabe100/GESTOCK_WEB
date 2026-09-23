import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.core.security import (
    AccessClaims,
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.platform.subscriptions.models import BillingPeriod, Subscription, SubscriptionStatus
from app.platform.subscriptions.service import add_months, effective_status, period_end
from app.shared.ids import new_id

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


def test_password_hashing() -> None:
    hashed = hash_password("un-mot-de-passe")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "un-mot-de-passe")
    assert not verify_password(hashed, "autre")
    assert not verify_password(None, "un-mot-de-passe")


def test_access_token_roundtrip_and_tampering() -> None:
    settings = Settings(environment="test")
    claims = AccessClaims(user_id=uuid.uuid4(), session_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    token = create_access_token(claims, settings, datetime.now(UTC))
    assert decode_access_token(token, settings) == claims
    with pytest.raises(InvalidTokenError):
        decode_access_token(token[:-2] + "xx", settings)
    other = Settings(environment="test", jwt_secret="x" * 40)
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, other)


def test_expired_access_token_is_rejected() -> None:
    settings = Settings(environment="test")
    claims = AccessClaims(user_id=uuid.uuid4(), session_id=uuid.uuid4(), tenant_id=None)
    token = create_access_token(claims, settings, datetime.now(UTC) - timedelta(hours=1))
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, settings)


def test_production_requires_strong_jwt_secret() -> None:
    with pytest.raises(ValueError, match="SM_JWT_SECRET"):
        Settings(environment="production")


def test_uuid7_is_versioned_and_sortable() -> None:
    ids = [new_id() for _ in range(50)]
    assert all(i.version == 7 for i in ids)
    assert len(set(ids)) == 50


def test_add_months_clamps_end_of_month() -> None:
    assert add_months(datetime(2026, 1, 31, tzinfo=UTC), 1) == datetime(2026, 2, 28, tzinfo=UTC)
    assert period_end(datetime(2026, 3, 15, tzinfo=UTC), BillingPeriod.ANNUAL) == datetime(
        2027, 3, 15, tzinfo=UTC
    )


def _subscription(status: SubscriptionStatus, end: datetime) -> Subscription:
    return Subscription(status=status, current_period_end=end)


@pytest.mark.parametrize(
    ("status", "end", "expected"),
    [
        (SubscriptionStatus.ACTIVE, NOW + timedelta(days=1), SubscriptionStatus.ACTIVE),
        (SubscriptionStatus.ACTIVE, NOW - timedelta(days=3), SubscriptionStatus.PAST_DUE),
        (SubscriptionStatus.ACTIVE, NOW - timedelta(days=8), SubscriptionStatus.EXPIRED),
        (SubscriptionStatus.TRIAL, NOW - timedelta(days=1), SubscriptionStatus.EXPIRED),
        (SubscriptionStatus.SUSPENDED, NOW + timedelta(days=10), SubscriptionStatus.SUSPENDED),
        (SubscriptionStatus.CANCELLED, NOW - timedelta(days=10), SubscriptionStatus.CANCELLED),
    ],
)
def test_effective_status(
    status: SubscriptionStatus, end: datetime, expected: SubscriptionStatus
) -> None:
    assert effective_status(_subscription(status, end), grace_days=7, now=NOW) is expected
