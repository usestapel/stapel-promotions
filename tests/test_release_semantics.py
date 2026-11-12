"""Release and commit over every attempt state: only ``cancelled`` undoes a commit.

The table is the contract (orders CONTRACT-DELTAS 13, 16, 17, 22): a release
with ``checkout_failed`` or ``orphaned`` for a committed pair is a no-op that
answers ok and keeps the use; ``cancelled`` returns the use per
``RETURN_USAGE_ON_RELEASE``. A commit after a release that was not a cancel
still wins (the placed order holds the discount); after a cancel it is a
no-op.
"""
import datetime as dt
import uuid

import pytest
from django.utils import timezone

from stapel_promotions.models import Redemption, RedemptionAttempt, RedemptionStatus
from stapel_promotions.services import redemption

from .factories import make_promotion
from .orders import line, request

pytestmark = pytest.mark.django_db

REASONS = ("checkout_failed", "orphaned", "cancelled")
STATES = ("unknown", "reserved", "committed", "expired",
          "released:checkout_failed", "released:orphaned", "released:cancelled")

RES, COM, REL, EXP = (RedemptionStatus.RESERVED, RedemptionStatus.COMMITTED,
                      RedemptionStatus.RELEASED, RedemptionStatus.EXPIRED)


def _arrange(orders, user, state) -> str:
    make_promotion(value="10", code="Q", usage_limit_total=5)
    if state == "unknown":
        return str(uuid.uuid4())
    payload = request([line(0, "40.00")], user=user, codes=["Q"])
    key, answer = orders.reserve(payload, orders.quote(payload))
    assert answer["ok"] is True
    if state == "committed":
        orders.commit(key)
    elif state == "expired":
        RedemptionAttempt.objects.update(expires_at=timezone.now() - dt.timedelta(seconds=1))
        assert redemption.expire_reservations() == 1
    elif state.startswith("released:"):
        orders.release(key, reason=state.split(":", 1)[1])
    return key


def _observe(key):
    """(attempt status, row status, committed uses) — what the cap counts."""
    attempt = RedemptionAttempt.objects.filter(attempt_key=key).first()
    row = Redemption.objects.filter(attempt__attempt_key=key).first()
    return (
        None if attempt is None else attempt.status,
        None if row is None else row.status,
        Redemption.objects.filter(status=COM).count(),
    )


#: (state, reason) -> observation after the release (RETURN_USAGE_ON_RELEASE on).
RELEASE_TABLE = {
    **{("unknown", r): (None, None, 0) for r in REASONS},
    **{("reserved", r): (REL, REL, 0) for r in REASONS},
    ("committed", "checkout_failed"): (COM, COM, 1),
    ("committed", "orphaned"): (COM, COM, 1),
    ("committed", "cancelled"): (REL, REL, 0),
    **{("expired", r): (EXP, EXP, 0) for r in REASONS},
    **{(f"released:{first}", r): (REL, REL, 0) for first in REASONS for r in REASONS},
}

#: state -> observation after a commit.
COMMIT_TABLE = {
    "unknown": (None, None, 0),
    "reserved": (COM, COM, 1),
    "committed": (COM, COM, 1),
    "expired": (COM, COM, 1),
    "released:checkout_failed": (COM, COM, 1),
    "released:orphaned": (COM, COM, 1),
    "released:cancelled": (REL, REL, 0),
}


def test_the_tables_cover_every_pair():
    assert set(RELEASE_TABLE) == {(s, r) for s in STATES for r in REASONS}
    assert set(COMMIT_TABLE) == set(STATES)


@pytest.mark.parametrize("state,reason", sorted(RELEASE_TABLE))
def test_release(orders, user, captured_events, state, reason):
    key = _arrange(orders, user, state)
    before = len([e for e in captured_events if e.event_type.endswith("released")])
    for _ in range(2):
        assert orders.release(key, reason=reason)["ok"] is True
        assert _observe(key) == RELEASE_TABLE[(state, reason)]
    released = len([e for e in captured_events if e.event_type.endswith("released")]) - before
    assert released == (1 if (state, reason) == ("committed", "cancelled") else 0)


@pytest.mark.parametrize("state", sorted(COMMIT_TABLE))
def test_commit(orders, user, state):
    key = _arrange(orders, user, state)
    assert orders.commit(key)["ok"] is True
    assert _observe(key) == COMMIT_TABLE[state]


@pytest.mark.parametrize("reason", REASONS)
def test_the_axis_off_keeps_every_committed_use(orders, user, settings, reason):
    settings.STAPEL_PROMOTIONS = {"RETURN_USAGE_ON_RELEASE": False}
    key = _arrange(orders, user, "committed")
    assert orders.release(key, reason=reason) == {"ok": True, "status": "committed"}
    assert _observe(key) == (COM, COM, 1)


@pytest.mark.parametrize("first", ["checkout_failed", "orphaned", "expired"])
def test_a_cancel_after_another_end_stops_a_late_commit(orders, user, first):
    key = _arrange(orders, user, "expired" if first == "expired" else f"released:{first}")
    orders.release(key, reason="cancelled")
    assert RedemptionAttempt.objects.get(attempt_key=key).release_reason == "cancelled"
    orders.commit(key)
    assert _observe(key)[2] == 0
