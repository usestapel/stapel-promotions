"""``actor_id``: who owns a checkout's redemption rows, a guest included.

Orders sends ``actor_id`` (always present: the guest's own id or the
account) next to ``user_id`` (null for a guest on the adjuster and hook payloads; the
``orders.order.placed`` fact keeps ``user_id`` = the acting account). Per-user
caps still read ``user_id`` and refuse a guest; the ledger records the
actor, which ``user.merged`` and GDPR export/erase follow. Payloads without
``actor_id`` record ``user_id`` as the actor (today's behaviour).
"""
import pytest
from stapel_core.comm import emit, mutate_and_emit

from stapel_promotions.errors import ERR_403_PROMO_CODE_REQUIRES_ACCOUNT
from stapel_promotions.gdpr import PromotionsGDPRProvider, erase_subject
from stapel_promotions.models import Redemption, RedemptionAttempt

from .factories import make_promotion
from .orders import line, request

pytestmark = pytest.mark.django_db

GUEST = "guest-42"


def guest_cart(codes):
    return {**request([line(0, "40.00")], codes=codes), "actor_id": GUEST}


def guest_checkout(orders, codes=("OPEN",), order_id="901"):
    payload = guest_cart(list(codes))
    key, answer = orders.reserve(payload, orders.quote(payload))
    assert answer["ok"] is True, answer
    orders.commit(key, order_id=order_id, user_id=None)
    return key


def test_a_guest_redemption_records_the_actor_not_a_user(orders):
    make_promotion(value="10", code="OPEN")
    guest_checkout(orders)
    attempt = RedemptionAttempt.objects.get()
    row = Redemption.objects.get()
    assert (attempt.user_id, attempt.actor_id) == ("", GUEST)
    assert (row.user_id, row.actor_id, row.status) == ("", GUEST, "committed")


def test_per_user_caps_still_refuse_a_guest_with_an_actor_id(orders):
    make_promotion(value="10", code="ONCE", usage_limit_per_user=1)
    answer = orders.quote(guest_cart(["ONCE"]))
    assert answer["errors"][0]["code"] == ERR_403_PROMO_CODE_REQUIRES_ACCOUNT


def test_a_real_account_is_its_own_actor(orders, user):
    make_promotion(value="10", code="OPEN")
    payload = {**request([line(0, "40.00")], user=user, codes=["OPEN"]), "actor_id": str(user.pk)}
    key, _ = orders.reserve(payload, orders.quote(payload))
    row = Redemption.objects.get()
    assert (row.user_id, row.actor_id) == (str(user.pk), str(user.pk))


def test_a_payload_without_actor_id_records_the_user_id(orders, user):
    make_promotion(value="10", code="OPEN")
    orders.checkout(request([line(0, "40.00")], user=user, codes=["OPEN"]))
    assert Redemption.objects.get().actor_id == str(user.pk)


def test_user_merged_moves_the_guest_rows_but_not_into_the_cap_count(orders, user):
    make_promotion(value="10", code="OPEN")
    guest_checkout(orders)
    with mutate_and_emit():
        emit("user.merged", {"from_user_id": GUEST, "into_user_id": str(user.pk)})
    row = Redemption.objects.get()
    assert (row.user_id, row.actor_id) == ("", str(user.pk))
    assert RedemptionAttempt.objects.get().actor_id == str(user.pk)


def test_gdpr_covers_rows_keyed_by_actor(orders):
    make_promotion(value="10", code="OPEN")
    guest_checkout(orders)
    exported = PromotionsGDPRProvider().export(GUEST)
    assert [r["order_id"] for r in exported["redemptions"]] == ["901"]
    counts = erase_subject("account", GUEST)
    assert (counts["redemptions"], counts["attempts"]) == (1, 1)
    assert not Redemption.objects.filter(actor_id=GUEST).exists()
    assert not RedemptionAttempt.objects.filter(actor_id=GUEST).exists()


def test_erasure_counts_a_real_account_row_once(orders, user):
    make_promotion(value="10", code="OPEN")
    orders.checkout({**request([line(0, "40.00")], user=user, codes=["OPEN"]), "actor_id": str(user.pk)})
    counts = erase_subject("account", str(user.pk))
    assert (counts["redemptions"], counts["attempts"]) == (1, 1)
    row = Redemption.objects.get()
    assert row.user_id == row.actor_id != str(user.pk)
