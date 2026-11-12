"""The last-use race: usage is re-counted under the promotion row lock.

SQLite has no row locks (Django drops FOR UPDATE there), so two OS threads
cannot prove the lock on this harness. What is proved here (the
stapel-reservations method):

1. the lock is taken — ``select_for_update()`` on ``Promotion`` (and on
   ``PromoCode``) — BEFORE usage is counted (ordering spy);
2. two interleaved checkouts, one use left: the rival commits after our
   quote and before our lock; we count after the lock, so the end is one
   placed order and one ``error.409.promo_code_exhausted``;
3. inversion: with usage counted before the lock (the classic defect), the
   same interleaving spends the last use twice — the test can see it.
On Postgres the lock makes step 2 the only possible interleaving. The same
holds for the per-user cap (one shopper, two tabs).
"""
import pytest
from django.contrib.auth import get_user_model
from django.db.models import QuerySet
from django.utils import timezone

from stapel_promotions.errors import ERR_409_PROMO_CODE_EXHAUSTED, ERR_409_PROMO_CODE_USER_LIMIT_REACHED
from stapel_promotions.models import Redemption
from stapel_promotions.services import quote as quote_service
from stapel_promotions.services import redemption

from .factories import make_promotion
from .orders import line, request

pytestmark = pytest.mark.django_db(transaction=True)


def _users():
    model = get_user_model()
    return model.objects.create_user(username="w1", password="x"), model.objects.create_user(
        username="w2", password="x")


def cart(user, codes=("LAST",)):
    return request([line(0, "40.00")], user=user, codes=list(codes))


def test_lock_is_taken_before_the_count(orders, monkeypatch):
    make_promotion(value="10", code="LAST", usage_limit_total=1)
    u1, _ = _users()
    order = []
    real_sfu = QuerySet.select_for_update
    real_count = quote_service.count_usage

    def sfu_spy(self, *a, **kw):
        order.append(f"lock:{self.model.__name__}")
        return real_sfu(self, *a, **kw)

    def count_spy(*a, **kw):
        order.append("count")
        return real_count(*a, **kw)

    payload = cart(u1)
    answer = orders.quote(payload)
    monkeypatch.setattr(QuerySet, "select_for_update", sfu_spy)
    monkeypatch.setattr(quote_service, "count_usage", count_spy)
    _, reserved = orders.reserve(payload, answer)
    assert reserved["ok"]
    assert order.index("lock:Promotion") < order.index("count")
    assert order.index("lock:PromoCode") < order.index("count")


def _interleave(orders, monkeypatch, mine, rival, *, stale=False):
    """The rival's whole checkout commits after our quote, before our lock.

    The hook is ``grants`` — the last step of reserve before its
    transaction opens — so the rival's commit is a real, separate one.
    With ``stale`` the candidates are loaded before the rival runs and
    handed to our reserve unchanged: a count taken before the lock.
    """
    real_grants = redemption.grants
    real_load = redemption.load_candidates
    state = {}

    def rival_first(request):
        if "started" not in state:
            state["started"] = True
            if stale:
                state["snapshot"] = real_load(
                    quote_service.Request(cart=request.cart, codes=list(request.codes) or ["LAST"]),
                    timezone.now())
            state["rival"] = orders.checkout(rival)
        return real_grants(request)

    def stale_load(*a, **kw):
        return state["snapshot"]

    answer = orders.quote(mine)
    monkeypatch.setattr(redemption, "grants", rival_first)
    if stale:
        monkeypatch.setattr(redemption, "load_candidates", stale_load)
    _, reserved = orders.reserve(mine, answer)
    return reserved, state["rival"]


def test_two_shoppers_one_use_left(orders, monkeypatch):
    make_promotion(value="10", code="LAST", usage_limit_total=1)
    u1, u2 = _users()
    reserved, rival = _interleave(orders, monkeypatch, cart(u1), cart(u2))
    assert rival["placed"] and reserved["ok"] is False
    assert reserved["errors"][0]["code"] == ERR_409_PROMO_CODE_EXHAUSTED
    assert Redemption.objects.count() == 1


def test_inversion_counting_before_the_lock_spends_the_last_use_twice(orders, monkeypatch):
    make_promotion(value="10", code="LAST", usage_limit_total=1)
    u1, u2 = _users()
    reserved, rival = _interleave(orders, monkeypatch, cart(u1), cart(u2), stale=True)
    assert rival["placed"] and reserved["ok"] is True
    assert Redemption.objects.count() == 2


def test_one_shopper_two_tabs_per_user_cap(orders, monkeypatch):
    make_promotion(value="10", code="LAST", usage_limit_per_user=1)
    u1, _ = _users()
    reserved, rival = _interleave(orders, monkeypatch, cart(u1), cart(u1))
    assert rival["placed"] and reserved["errors"][0]["code"] == ERR_409_PROMO_CODE_USER_LIMIT_REACHED
    assert Redemption.objects.count() == 1


def test_concurrent_reserve_of_one_attempt_key_answers_the_winner(orders, monkeypatch):
    """The twin commits its attempt row after our existence check: our
    insert hits the unique key and we answer the twin's reservation."""
    make_promotion(value="10", code="LAST")
    u1, _ = _users()
    payload = cart(u1)
    answer = orders.quote(payload)
    key = "11111111-1111-1111-1111-111111111111"
    real_find = redemption.find_attempt
    state = {}

    def twin_first(attempt_key):
        if "twin" not in state:
            state["twin"] = True
            state["answer"] = orders.reserve(payload, answer, key)[1]
            return None
        return real_find(attempt_key)

    monkeypatch.setattr(redemption, "find_attempt", twin_first)
    _, mine = orders.reserve(payload, answer, key)
    assert mine["ok"] and mine["token"] == state["answer"]["token"]
    assert Redemption.objects.count() == 1
