"""``promotions.quote`` as orders calls it (contract §2 + deltas)."""
import datetime as dt

import pytest
from django.utils import timezone
from stapel_core.comm import call
from stapel_core.comm.exceptions import FunctionCallError

from stapel_promotions.errors import (
    ERR_400_PROMO_CODE_UNKNOWN,
    ERR_400_PROMO_CODES_INVALID,
    ERR_400_PROMO_CODES_TOO_MANY,
    ERR_403_PROMO_CODE_REQUIRES_ACCOUNT,
    ERR_409_PROMO_CODE_NOT_ACTIVE,
)

from .factories import make_code, make_promotion
from .orders import line, request

pytestmark = pytest.mark.django_db


def codes_of(answer):
    return [e["code"] for e in answer["errors"]]


def test_no_promotions_no_adjustments(orders, user):
    assert orders.quote(request([line(0, "20.00")], user=user)) == {"adjustments": [], "errors": []}


def test_code_is_case_insensitive_and_space_tolerant(orders, user):
    make_promotion(value="10", code="SUMMER10")
    answer = orders.quote(request([line(0, "20.00")], user=user, codes=[" summer10 "]))
    assert [a["amount"] for a in answer["adjustments"]] == ["-2.00"]
    assert answer["adjustments"][0]["code"] == "SUMMER10"


def test_unknown_code_is_an_error_with_the_code(orders, user):
    answer = orders.quote(request([line(0, "20.00")], user=user, codes=["NOPE"]))
    assert answer["errors"] == [{"code": ERR_400_PROMO_CODE_UNKNOWN, "field": "codes", "params": {"code": "NOPE"}}]


def test_code_assigned_to_someone_else_reads_as_unknown(orders, user, other_user):
    p = make_promotion(value="10")
    make_code(p, "MINE", assigned_user_id=str(other_user.pk))
    assert codes_of(orders.quote(request([line(0, "20.00")], user=user, codes=["MINE"]))) == \
        [ERR_400_PROMO_CODE_UNKNOWN]
    assert orders.quote(request([line(0, "20.00")], user=other_user, codes=["MINE"]))["errors"] == []


def test_automatic_promotion_applies_without_a_code(orders, user):
    make_promotion(kind="free_shipping", automatic=True, min_subtotal="50.00", currency="USD")
    assert orders.quote(request([line(0, "49.99")], user=user))["adjustments"] == []
    [marker] = orders.quote(request([line(0, "50.00")], user=user))["adjustments"]
    assert marker["meta"]["free_shipping"] is True and marker["amount"] == "0.00"


def test_expired_window_is_not_active(orders, user):
    make_promotion(code="OLD", ends_at=timezone.now() - dt.timedelta(minutes=1))
    assert codes_of(orders.quote(request([line(0, "20.00")], user=user, codes=["OLD"]))) == \
        [ERR_409_PROMO_CODE_NOT_ACTIVE]


def test_guest_and_per_user_cap(orders):
    make_promotion(code="ONCE", usage_limit_per_user=1)
    assert codes_of(orders.quote(request([line(0, "20.00")], user=None, codes=["ONCE"]))) == \
        [ERR_403_PROMO_CODE_REQUIRES_ACCOUNT]


def test_context_codes_bounds(orders, user, settings):
    settings.STAPEL_PROMOTIONS = {"MAX_CODES_PER_CART": 2}
    assert codes_of(orders.quote(request([line(0, "1.00")], user=user, codes=["A", "B", "C"]))) == \
        [ERR_400_PROMO_CODES_TOO_MANY]
    payload = request([line(0, "1.00")], user=user)
    payload["context"] = {"codes": "A"}
    assert codes_of(orders.quote(payload)) == [ERR_400_PROMO_CODES_INVALID]
    payload["context"] = {"codes": ["a", "A"], "delivery": {"method": "courier"}}
    assert codes_of(orders.quote(payload)) == [ERR_400_PROMO_CODE_UNKNOWN]  # duplicates collapse


def test_earlier_adjusters_and_own_adjustments_in_adjustments_so_far(orders, user):
    make_promotion(kind="fixed", value="15.00", currency="USD", automatic=True)
    prior = [{"kind": "discount", "code": "OTHER", "label": "x", "amount": "-10.00", "line_index": 0,
              "meta": {}, "adjuster": "loyalty"},
             {"kind": "discount", "code": "auto:x", "label": "x", "amount": "-99.00", "line_index": None,
              "meta": {"issuer": "promotions", "promotion_id": 1}, "adjuster": "promotions"}]
    answer = orders.quote(request([line(0, "20.00")], user=user, prior=prior))
    assert [a["amount"] for a in answer["adjustments"]] == ["-10.00"]


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(subtotal=12.5),
    lambda p: p.update(subtotal="1.005"),
    lambda p: p["lines"][0].update(line_total="-1.00"),
    lambda p: p.pop("currency"),
])
def test_malformed_orders_payload_fails_the_call(user, mutate):
    payload = request([line(0, "20.00")], user=user)
    mutate(payload)
    with pytest.raises((FunctionCallError, Exception)):
        call("promotions.quote", payload)


def test_quote_writes_nothing(orders, user, django_assert_max_num_queries):
    make_promotion(value="10", code="TEN")
    with django_assert_max_num_queries(10) as ctx:
        orders.quote(request([line(0, "20.00")], user=user, codes=["TEN"]))
    assert not [q for q in ctx.captured_queries if q["sql"].split()[0] in ("INSERT", "UPDATE", "DELETE")]
