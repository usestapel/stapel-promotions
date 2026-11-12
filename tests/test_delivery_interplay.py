"""Free shipping meets the delivery adjuster through ``adjustments_so_far`` only.

A fake delivery adjuster (order 200, after promotions at 100) applies the
rule the delivery brick ships: it finds a ``discount`` adjustment with
``meta.free_shipping: true`` in ``adjustments_so_far`` and quotes its own
shipping line at 0.00 — for every method, or only for the codes in
``meta.free_shipping_methods`` when that list is present.
"""
from decimal import Decimal

import pytest

from stapel_promotions.preset import ADJUSTER_ORDER

from .factories import make_promotion
from .orders import line, request

pytestmark = pytest.mark.django_db

DELIVERY_ORDER = 200


def fake_delivery_quote(payload: dict, method: str = "courier", price: str = "7.50") -> dict:
    waived = False
    for adj in payload["adjustments_so_far"]:
        meta = adj.get("meta") or {}
        if adj.get("kind") == "discount" and meta.get("free_shipping") is True:
            methods = meta.get("free_shipping_methods")
            waived = waived or methods is None or method in methods
    return {"adjustments": [{"kind": "shipping", "code": method, "label": "Courier",
                             "amount": "0.00" if waived else price, "line_index": None, "meta": {}}],
            "errors": []}


def run_chain(orders, payload, method="courier"):
    """orders' adjuster loop: promotions (100), then delivery (200)."""
    assert ADJUSTER_ORDER < DELIVERY_ORDER
    ours = orders.quote(payload)
    so_far = orders.final_list(payload, ours)
    delivery = fake_delivery_quote({**payload, "adjustments_so_far": so_far}, method)
    total = Decimal(payload["subtotal"]) + sum(
        (Decimal(a["amount"]) for a in ours["adjustments"] + delivery["adjustments"]), Decimal("0.00"))
    return ours, delivery, total


def test_marker_shape_zeroes_the_shipping_line(orders, user):
    make_promotion(kind="free_shipping", automatic=True, min_subtotal="50.00", currency="USD")
    ours, delivery, total = run_chain(orders, request([line(0, "60.00")], user=user))
    [marker] = ours["adjustments"]
    assert marker["kind"] == "discount" and marker["amount"] == "0.00" and marker["line_index"] is None
    assert marker["meta"]["free_shipping"] is True and "free_shipping_methods" not in marker["meta"]
    assert set(marker["meta"]) == {"issuer", "promotion_id", "promotion_key", "source", "kind", "free_shipping"}
    assert delivery["adjustments"][0]["amount"] == "0.00" and total == Decimal("60.00")


def test_below_the_threshold_shipping_is_charged(orders, user):
    make_promotion(kind="free_shipping", automatic=True, min_subtotal="50.00", currency="USD")
    ours, delivery, total = run_chain(orders, request([line(0, "40.00")], user=user))
    assert ours["adjustments"] == [] and total == Decimal("47.50")


def test_methods_narrow_the_waiver(orders, user):
    make_promotion(kind="free_shipping", code="PICKUPFREE", free_shipping_methods=["pickup"])
    payload = request([line(0, "20.00")], user=user, codes=["PICKUPFREE"])
    ours, courier, _ = run_chain(orders, payload, "courier")
    assert ours["adjustments"][0]["meta"]["free_shipping_methods"] == ["pickup"]
    assert courier["adjustments"][0]["amount"] == "7.50"
    _, pickup, _ = run_chain(orders, payload, "pickup")
    assert pickup["adjustments"][0]["amount"] == "0.00"


def test_free_shipping_stacks_with_a_money_discount(orders, user):
    make_promotion(kind="free_shipping", automatic=True)
    make_promotion(value="10", code="TEN")
    ours, delivery, total = run_chain(orders, request([line(0, "30.00")], user=user, codes=["TEN"]))
    assert sorted(a["amount"] for a in ours["adjustments"]) == ["-3.00", "0.00"]
    assert total == Decimal("27.00")
