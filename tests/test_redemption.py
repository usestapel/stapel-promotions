"""The checkout hook: reserve / commit / release, idempotency, caps, facts."""
import datetime as dt

import pytest
from django.utils import timezone

from stapel_promotions.errors import (
    ERR_409_PROMO_CODE_EXHAUSTED,
    ERR_409_PROMO_CODE_USER_LIMIT_REACHED,
    ERR_409_PROMOTION_EXHAUSTED,
)
from stapel_promotions.models import Redemption, RedemptionAttempt, RedemptionStatus
from stapel_promotions.services import redemption

from .factories import make_code, make_promotion
from .orders import line, request

pytestmark = pytest.mark.django_db


def cart(user, codes=None, total="40.00"):
    return request([line(0, total)], user=user, codes=codes)


def test_checkout_ledgers_the_snapshot(orders, user, captured_events):
    p = make_promotion(value="25", code="Q")
    out = orders.checkout(cart(user, ["q"]), order_id="77")
    assert out["placed"] and out["total"] == 30
    [row] = Redemption.objects.all()
    assert (row.promotion_id, row.status, str(row.amount), row.code.code, row.user_id) == \
        (p.pk, "committed", "10.00", "Q", str(user.pk))
    assert row.attempt.order_id == "77"
    [fact] = captured_events
    assert fact.event_type == "promotions.redemption.committed"
    assert fact.payload["amount"] == "10.00" and fact.payload["order_id"] == "77"


def test_reserve_is_idempotent_on_the_attempt_key(orders, user):
    make_promotion(value="10", code="Q")
    payload = cart(user, ["Q"])
    answer = orders.quote(payload)
    key, first = orders.reserve(payload, answer)
    _, again = orders.reserve(payload, answer, key)
    assert first["ok"] and again == first and first["token"]
    assert RedemptionAttempt.objects.count() == 1 and Redemption.objects.count() == 1


def test_reserve_after_release_reserves_anew(orders, user):
    make_promotion(value="10", code="Q")
    payload = cart(user, ["Q"])
    answer = orders.quote(payload)
    key, first = orders.reserve(payload, answer)
    assert orders.release(key, first["token"]) == {"ok": True, "status": "released"}
    _, second = orders.reserve(payload, answer, key)
    assert second["ok"] and second["token"] != first["token"]
    assert Redemption.objects.get().status == RedemptionStatus.RESERVED


def test_commit_and_release_are_idempotent_and_tolerate_a_null_token(orders, user, captured_events):
    make_promotion(value="10", code="Q")
    payload = cart(user, ["Q"])
    key, _ = orders.reserve(payload, orders.quote(payload))
    assert orders.commit(key, None)["ok"] and orders.commit(key, None)["ok"]
    assert len([e for e in captured_events if e.event_type.endswith("committed")]) == 1
    assert orders.release(key, None, reason="cancelled")["status"] == "released"
    assert orders.release(key, None, reason="cancelled")["status"] == "released"
    released = [e for e in captured_events if e.event_type.endswith("released")]
    assert len(released) == 1 and released[0].payload["reason"] == "cancelled"


def test_unknown_attempt_is_ok_none(orders):
    assert orders.commit("00000000-0000-0000-0000-000000000000") == {"ok": True, "status": "none"}
    assert orders.release("00000000-0000-0000-0000-000000000000") == {"ok": True, "status": "none"}


def test_a_checkout_without_promotions_reserves_an_empty_attempt(orders, user):
    out = orders.checkout(cart(user))
    assert out["placed"] and Redemption.objects.count() == 0
    assert RedemptionAttempt.objects.get().status == RedemptionStatus.COMMITTED


def test_total_cap(orders, user, other_user, captured_events):
    make_promotion(value="10", code="TWO", usage_limit_total=2)
    assert orders.checkout(cart(user, ["TWO"]))["placed"]
    assert orders.checkout(cart(other_user, ["TWO"]))["placed"]
    out = orders.checkout(cart(user, ["TWO"]))
    assert not out["placed"] and out["errors"][0]["code"] == ERR_409_PROMO_CODE_EXHAUSTED
    exhausted = [e for e in captured_events if e.event_type == "promotions.promotion.exhausted"]
    assert len(exhausted) == 1 and exhausted[0].payload["used"] == 2


def test_per_user_cap_and_per_code_cap(orders, user, other_user):
    p = make_promotion(value="10", code="ONCE", usage_limit_per_user=1)
    assert orders.checkout(cart(user, ["ONCE"]))["placed"]
    out = orders.checkout(cart(user, ["ONCE"]))
    assert out["errors"][0]["code"] == ERR_409_PROMO_CODE_USER_LIMIT_REACHED
    assert orders.checkout(cart(other_user, ["ONCE"]))["placed"]
    make_code(p, "SINGLE", usage_limit=1)
    p.usage_limit_per_user = None
    p.save()
    assert orders.checkout(cart(user, ["SINGLE"]))["placed"]
    assert orders.checkout(cart(other_user, ["SINGLE"]))["errors"][0]["code"] == ERR_409_PROMO_CODE_EXHAUSTED


def test_cancel_returns_the_use_by_default(orders, user, other_user):
    make_promotion(value="10", code="ONE", usage_limit_total=1)
    out = orders.checkout(cart(user, ["ONE"]))
    assert not orders.checkout(cart(other_user, ["ONE"]))["placed"]
    orders.release(out["key"], reason="cancelled")
    assert orders.checkout(cart(other_user, ["ONE"]))["placed"]


def test_cancel_keeps_the_use_when_switched_off(orders, user, other_user, settings, captured_events):
    settings.STAPEL_PROMOTIONS = {"RETURN_USAGE_ON_RELEASE": False}
    make_promotion(value="10", code="ONE", usage_limit_total=1)
    out = orders.checkout(cart(user, ["ONE"]))
    assert orders.release(out["key"], reason="cancelled") == {"ok": True, "status": "committed"}
    assert not orders.checkout(cart(other_user, ["ONE"]))["placed"]
    assert not [e for e in captured_events if e.event_type.endswith("released")]


def test_a_lapsed_hold_stops_counting(orders, user, other_user):
    make_promotion(value="10", code="ONE", usage_limit_total=1)
    payload = cart(user, ["ONE"])
    key, _ = orders.reserve(payload, orders.quote(payload))
    assert not orders.checkout(cart(other_user, ["ONE"]))["placed"]
    RedemptionAttempt.objects.update(expires_at=timezone.now() - dt.timedelta(seconds=1))
    assert orders.checkout(cart(other_user, ["ONE"]))["placed"]
    assert redemption.expire_reservations() == 1
    # The late commit of the lapsed hold still wins: the order holds the discount.
    assert orders.commit(key)["status"] == "committed"
    assert Redemption.objects.filter(status="committed").count() == 2


def test_an_automatic_promotion_capped_between_quote_and_reserve_refuses(orders, user, other_user):
    make_promotion(value="10", automatic=True, usage_limit_total=1)
    payload = cart(user)
    answer = orders.quote(payload)
    assert orders.checkout(cart(other_user))["placed"]
    _, reserved = orders.reserve(payload, answer)
    assert reserved["ok"] is False and reserved["errors"][0]["code"] == ERR_409_PROMOTION_EXHAUSTED
    # The shopper's next quote is honest and the checkout goes through without it.
    out = orders.checkout(payload)
    assert out["placed"] and out["answer"]["adjustments"] == []


def test_a_capped_automatic_promotion_never_blocks_checkout(orders, user):
    make_promotion(value="10", automatic=True, usage_limit_total=1)
    Redemption.objects.all().delete()
    assert orders.checkout(cart(user))["placed"]
    assert orders.checkout(cart(user))["placed"]


def test_reserve_refuses_a_snapshot_that_grants_more_than_the_subtotal(orders, user):
    from stapel_core.comm import call

    make_promotion(value="10", code="Q")
    payload = cart(user, ["Q"])
    answer = orders.quote(payload)
    answer["adjustments"][0]["amount"] = "-999.00"
    with pytest.raises(Exception):
        call("promotions.reserve", {**payload, "attempt_key": "k", "adjustments_so_far": answer["adjustments"]})


def test_housekeeping_purges_old_dead_attempts(orders, user, settings):
    make_promotion(value="10", code="Q")
    payload = cart(user, ["Q"])
    key, _ = orders.reserve(payload, orders.quote(payload))
    orders.release(key)
    RedemptionAttempt.objects.update(updated_at=timezone.now() - dt.timedelta(days=91))
    assert redemption.purge_attempts() == 2  # the attempt and its ledger row
    assert not RedemptionAttempt.objects.exists()
