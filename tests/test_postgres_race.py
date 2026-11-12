"""The last-use race with real threads and real row locks (Postgres only).

Armed by ``STAPEL_TEST_DATABASE_URL`` (a Postgres URL); on SQLite it skips
with that reason and ``tests/test_concurrency.py`` carries the ordering and
interleaving proofs instead.

Each test starts N shoppers on N threads (N connections) behind a barrier,
all reserving the same promotion. A second barrier sits right AFTER the
usage count: with the row lock, a later thread is blocked on
``SELECT … FOR UPDATE`` before it can count, so the barrier times out and
the first thread commits alone — the next counts after it. The inversion
removes the lock and every thread counts the same stale number.
"""
import threading
import uuid

import pytest
from django.db import connection, connections
from django.db.models import QuerySet

from stapel_promotions.models import Redemption
from stapel_promotions.services import quote as quote_service

from .factories import make_promotion
from .orders import FakeOrders, line, request

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql",
                       reason="needs Postgres row locks: set STAPEL_TEST_DATABASE_URL"),
]

SHOPPERS = 6


def _race(monkeypatch, users, codes, *, lock=True):
    orders = FakeOrders()
    payloads = [request([line(0, "40.00")], user=u, codes=codes) for u in users]
    answers = [orders.quote(p) for p in payloads]
    start = threading.Barrier(len(users))
    after_count = threading.Barrier(len(users), timeout=1.5)
    real_count = quote_service.count_usage

    def count_then_wait(*a, **kw):
        result = real_count(*a, **kw)
        try:
            after_count.wait()
        except threading.BrokenBarrierError:
            pass
        return result

    monkeypatch.setattr(quote_service, "count_usage", count_then_wait)
    if not lock:
        monkeypatch.setattr(QuerySet, "select_for_update", lambda self, *a, **kw: self)
    results = [None] * len(users)

    def shopper(i):
        try:
            start.wait()
            results[i] = orders.reserve(payloads[i], answers[i], str(uuid.uuid4()))[1]
        finally:
            connections.close_all()

    threads = [threading.Thread(target=shopper, args=(i,)) for i in range(len(users))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return results


def _users(n, same=False):
    from django.contrib.auth import get_user_model

    model = get_user_model()
    if same:
        u = model.objects.create_user(username="one", password="x")
        return [u] * n
    return [model.objects.create_user(username=f"s{i}", password="x") for i in range(n)]


def test_total_cap_holds_under_real_concurrency(monkeypatch):
    make_promotion(value="10", code="LAST", usage_limit_total=2)
    results = _race(monkeypatch, _users(SHOPPERS), ["LAST"])
    assert sum(1 for r in results if r and r["ok"]) == 2
    assert {r["errors"][0]["code"] for r in results if r and not r["ok"]} == {"error.409.promo_code_exhausted"}
    assert Redemption.objects.count() == 2


def test_per_user_cap_holds_across_tabs(monkeypatch):
    make_promotion(value="10", code="ONCE", usage_limit_per_user=1)
    results = _race(monkeypatch, _users(SHOPPERS, same=True), ["ONCE"])
    assert sum(1 for r in results if r and r["ok"]) == 1
    assert Redemption.objects.count() == 1


def test_inversion_without_the_lock_the_cap_is_overspent(monkeypatch):
    make_promotion(value="10", code="LAST", usage_limit_total=2)
    results = _race(monkeypatch, _users(SHOPPERS), ["LAST"], lock=False)
    assert sum(1 for r in results if r and r["ok"]) > 2
    assert Redemption.objects.count() > 2
