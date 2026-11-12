"""Facts stapel-promotions emits (always through a ``mutate_and_emit`` emitter).

Key = the promotion id, so one promotion's facts stay ordered.
"""
from __future__ import annotations

from ..money import to_wire

EVENT_COMMITTED = "promotions.redemption.committed"
EVENT_RELEASED = "promotions.redemption.released"
EVENT_EXHAUSTED = "promotions.promotion.exhausted"


def redemption_payload(row, attempt) -> dict:
    return {
        "redemption_id": str(row.pk),
        "promotion_id": str(row.promotion_id),
        "promotion_key": row.promotion.key,
        "code": row.code.code if row.code_id else None,
        "user_id": row.user_id or None,
        "attempt_key": attempt.attempt_key,
        "order_id": attempt.order_id or None,
        "amount": to_wire(row.amount),
        "currency": row.currency,
        "free_shipping": row.free_shipping,
    }


def released_payload(row, attempt, reason: str) -> dict:
    return {**redemption_payload(row, attempt), "reason": reason}


def exhausted_payload(promotion, used: int) -> dict:
    return {
        "promotion_id": str(promotion.pk),
        "promotion_key": promotion.key,
        "usage_limit_total": promotion.usage_limit_total,
        "used": used,
    }


__all__ = [
    "EVENT_COMMITTED", "EVENT_EXHAUSTED", "EVENT_RELEASED",
    "exhausted_payload", "redemption_payload", "released_payload",
]
