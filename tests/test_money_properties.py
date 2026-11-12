"""Money properties (spec §4.5): a discount never exceeds what it applies to,
totals never go negative, allocation is exact. Hypothesis drives the inputs;
the rounding rule itself is pinned by named examples in test_pricing.py.
"""
import datetime as dt
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from stapel_promotions import money, pricing

NOW = dt.datetime(2026, 11, 12, tzinfo=dt.timezone.utc)
cents = st.integers(min_value=0, max_value=10**9).map(lambda c: Decimal(c) / 100)
positive_cents = st.integers(min_value=1, max_value=10**8).map(lambda c: Decimal(c) / 100)
percent = st.integers(min_value=1, max_value=10000).map(lambda c: Decimal(c) / 100)


@st.composite
def rules(draw, rid):
    kind = draw(st.sampled_from(["percent", "fixed", "free_shipping"]))
    scope = draw(st.sampled_from(["cart", "purchasable_type", "keys"]))
    return pricing.Rule(
        id=rid, key=f"r{rid}", name="r", kind=kind,
        value=draw(percent) if kind == "percent" else (draw(positive_cents) if kind == "fixed" else money.ZERO),
        currency="USD" if kind == "fixed" else "",
        max_discount=draw(st.one_of(st.none(), positive_cents)) if kind == "percent" else None,
        scope=scope, scope_type="a" if scope != "cart" else "",
        scope_keys=frozenset(draw(st.sets(st.sampled_from(["k0", "k1", "k2", "k3"]), min_size=1))),
        stackable=draw(st.booleans()), priority=draw(st.integers(-3, 3)),
    )


@st.composite
def scenarios(draw):
    totals = draw(st.lists(cents, min_size=1, max_size=6))
    lines = tuple(pricing.Line(index=i, purchasable_type=draw(st.sampled_from(["a", "b"])),
                               purchasable_key=f"k{i}", line_total=t) for i, t in enumerate(totals))
    prior = {i: min(t, draw(cents)) for i, t in enumerate(totals) if draw(st.booleans())}
    cart = pricing.Cart(currency="USD", subtotal=sum(totals, Decimal("0.00")), user_id="u", lines=lines,
                        prior_line_discounts=prior)
    candidates = [pricing.Candidate(rule=draw(rules(i + 1)), code=None) for i in range(draw(st.integers(1, 6)))]
    return cart, candidates


@settings(max_examples=400, deadline=None)
@given(scenarios())
def test_discounts_never_exceed_their_base_and_totals_stay_non_negative(scenario):
    cart, candidates = scenario
    result = pricing.evaluate(cart, candidates, NOW)
    bases = pricing.line_bases(cart)
    taken = {index: Decimal("0.00") for index in bases}
    for applied in result.applied:
        assert applied.amount >= 0
        assert sum((s for _, s in applied.shares), Decimal("0.00")) == applied.amount
        for index, share in applied.shares:
            assert share > 0 and pricing.in_scope(applied.candidate.rule, cart.lines[index])
            taken[index] += share
    for index, base in bases.items():
        assert taken[index] <= base
    adjustments = result.adjustments()
    total = cart.subtotal + sum((Decimal(a["amount"]) for a in adjustments), Decimal("0.00"))
    assert total >= 0
    for adj in adjustments:
        assert Decimal(adj["amount"]) <= 0 and Decimal(adj["amount"]) == Decimal(adj["amount"]).quantize(money.QUANTUM)


@settings(max_examples=400, deadline=None)
@given(cents, st.lists(cents, min_size=1, max_size=10))
def test_allocation_is_exact_and_bounded(amount, bases):
    shares = money.allocate(amount, bases)
    assert sum(shares, Decimal("0.00")) == min(amount, sum(bases, Decimal("0.00")))
    assert all(Decimal("0.00") <= s <= b for s, b in zip(shares, bases))


@settings(max_examples=300, deadline=None)
@given(cents, percent)
def test_percent_never_exceeds_its_base(base, pct):
    assert Decimal("0.00") <= money.percent_of(base, pct) <= base


def test_parse_refuses_floats_and_extra_places():
    import pytest

    for bad in (1.5, "1.005", "abc", None, "-1.00", "NaN"):
        with pytest.raises(money.MalformedAmount):
            money.parse(bad)
    assert money.parse("-1.50", signed=True) == Decimal("-1.50")
