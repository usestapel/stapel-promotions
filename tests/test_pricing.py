"""The pricing engine, rule by rule (spec §4), without a database."""
import datetime as dt
from decimal import Decimal

from stapel_promotions import pricing
from stapel_promotions.errors import (
    ERR_403_PROMO_CODE_REQUIRES_ACCOUNT,
    ERR_409_PROMO_CODE_EXHAUSTED,
    ERR_409_PROMO_CODE_MIN_SUBTOTAL,
    ERR_409_PROMO_CODE_NOT_ACTIVE,
    ERR_409_PROMO_CODE_NOT_APPLICABLE,
    ERR_409_PROMO_CODE_USER_LIMIT_REACHED,
)

NOW = dt.datetime(2026, 11, 12, 12, 0, tzinfo=dt.timezone.utc)
D = Decimal


def cart(*totals, currency="USD", user="u1", types=None, prior_lines=None, prior_cart="0.00"):
    lines = tuple(
        pricing.Line(index=i, purchasable_type=(types or {}).get(i, "listing"), purchasable_key=f"k{i}",
                     line_total=D(t))
        for i, t in enumerate(totals)
    )
    return pricing.Cart(
        currency=currency, subtotal=sum((line.line_total for line in lines), D("0.00")), user_id=user,
        lines=lines, prior_line_discounts={k: D(v) for k, v in (prior_lines or {}).items()},
        prior_cart_discount=D(prior_cart),
    )


_ids = iter(range(1, 10_000))


def rule(kind="percent", value="10", **kw):
    rid = kw.pop("id", next(_ids))
    return pricing.Rule(id=rid, key=f"r{rid}", name=f"Rule {rid}", kind=kind, value=D(value), **kw)


def auto(r, usage=pricing.Usage()):
    return pricing.Candidate(rule=r, code=None, usage=usage)


def coded(r, text="SAVE", usage=pricing.Usage(), **kw):
    return pricing.Candidate(rule=r, code=pricing.Code(id=r.id, code=text, **kw), usage=usage)


def amounts(result):
    return [a["amount"] for a in result.adjustments()]


class TestKinds:
    def test_percent_rounds_half_up(self):
        result = pricing.evaluate(cart("10.05"), [auto(rule(value="10"))], NOW)
        assert amounts(result) == ["-1.01"]  # 1.005 -> 1.01

    def test_percent_cap(self):
        result = pricing.evaluate(cart("500.00"), [auto(rule(value="50", max_discount=D("20.00")))], NOW)
        assert amounts(result) == ["-20.00"]

    def test_fixed_never_exceeds_the_base(self):
        result = pricing.evaluate(cart("3.00"), [auto(rule("fixed", "10.00", currency="USD"))], NOW)
        assert amounts(result) == ["-3.00"]

    def test_free_shipping_is_a_zero_marker(self):
        result = pricing.evaluate(cart("30.00"), [auto(rule("free_shipping", "0"))], NOW)
        [adj] = result.adjustments()
        assert adj["amount"] == "0.00" and adj["line_index"] is None
        assert adj["meta"]["free_shipping"] is True and adj["kind"] == "discount"
        assert "allocation" not in adj["meta"]

    def test_hundred_percent_zeroes_the_cart(self):
        result = pricing.evaluate(cart("19.99", "0.01"), [auto(rule(value="100"))], NOW)
        assert result.total == D("20.00")


class TestEligibility:
    def check(self, candidate, c=None):
        return pricing.check(candidate, c or cart("50.00"), NOW)[0]

    def test_window_is_half_open(self):
        assert self.check(auto(rule(starts_at=NOW))) is None
        assert self.check(auto(rule(ends_at=NOW))) == ERR_409_PROMO_CODE_NOT_ACTIVE
        assert self.check(auto(rule(starts_at=NOW + dt.timedelta(seconds=1)))) == ERR_409_PROMO_CODE_NOT_ACTIVE

    def test_inactive_rule_or_code(self):
        assert self.check(auto(rule(is_active=False))) == ERR_409_PROMO_CODE_NOT_ACTIVE
        assert self.check(coded(rule(), is_active=False)) == ERR_409_PROMO_CODE_NOT_ACTIVE

    def test_fixed_needs_the_cart_currency(self):
        assert self.check(auto(rule("fixed", "5", currency="EUR"))) == ERR_409_PROMO_CODE_NOT_APPLICABLE
        assert self.check(auto(rule("fixed", "5", currency="USD"))) is None

    def test_percent_without_currency_applies_to_any(self):
        assert self.check(auto(rule()), cart("50.00", currency="EUR")) is None

    def test_per_user_cap_refuses_a_guest(self):
        assert self.check(coded(rule(usage_limit_per_user=1)), cart("50.00", user=None)) == \
            ERR_403_PROMO_CODE_REQUIRES_ACCOUNT

    def test_caps(self):
        assert self.check(auto(rule(usage_limit_total=2), pricing.Usage(total=2))) == ERR_409_PROMO_CODE_EXHAUSTED
        assert self.check(auto(rule(usage_limit_total=2), pricing.Usage(total=1))) is None
        assert self.check(coded(rule(), usage=pricing.Usage(per_code=1), usage_limit=1)) == \
            ERR_409_PROMO_CODE_EXHAUSTED
        assert self.check(auto(rule(usage_limit_per_user=1), pricing.Usage(per_user=1))) == \
            ERR_409_PROMO_CODE_USER_LIMIT_REACHED

    def test_min_subtotal_names_the_threshold(self):
        reason, params = pricing.check(coded(rule(min_subtotal=D("60.00"), currency="USD")), cart("50.00"), NOW)
        assert reason == ERR_409_PROMO_CODE_MIN_SUBTOTAL
        assert params == {"code": "SAVE", "min_subtotal": "60.00", "currency": "USD"}
        assert self.check(coded(rule(min_subtotal=D("50.00"), currency="USD"))) is None

    def test_scope_without_a_line_is_not_applicable(self):
        r = rule(scope="purchasable_type", scope_type="course")
        assert self.check(auto(r)) == ERR_409_PROMO_CODE_NOT_APPLICABLE
        assert self.check(auto(r), cart("5.00", types={0: "course"})) is None

    def test_failed_code_is_an_error_failed_automatic_is_silent(self):
        result = pricing.evaluate(cart("50.00"), [coded(rule(is_active=False)), auto(rule(is_active=False))], NOW)
        assert [e["code"] for e in result.errors] == [ERR_409_PROMO_CODE_NOT_ACTIVE]
        assert result.errors[0]["field"] == "codes" and result.adjustments() == []


class TestScopeAndAllocation:
    def test_keys_scope_touches_only_listed_lines(self):
        r = rule("fixed", "4.00", currency="USD", scope="keys", scope_type="listing", scope_keys=frozenset({"k1"}))
        [adj] = pricing.evaluate(cart("10.00", "10.00"), [auto(r)], NOW).adjustments()
        assert adj["line_index"] == 1 and adj["amount"] == "-4.00"

    def test_type_scope_on_two_lines_is_one_adjustment_with_allocation(self):
        r = rule(value="10", scope="purchasable_type", scope_type="listing")
        [adj] = pricing.evaluate(cart("10.00", "20.00", "5.00", types={2: "course"}), [auto(r)], NOW).adjustments()
        assert adj["line_index"] is None and adj["amount"] == "-3.00"
        assert adj["meta"]["allocation"] == [{"line_index": 0, "amount": "-1.00"},
                                             {"line_index": 1, "amount": "-2.00"}]

    def test_cart_scope_allocates_leftover_cents_to_largest_remainders(self):
        [adj] = pricing.evaluate(cart("1.00", "1.00", "1.00"), [auto(rule("fixed", "1.00", currency="USD"))],
                                 NOW).adjustments()
        assert adj["line_index"] is None
        shares = [a["amount"] for a in adj["meta"]["allocation"]]
        assert shares == ["-0.34", "-0.33", "-0.33"]

    def test_prior_discounts_reduce_the_base(self):
        result = pricing.evaluate(cart("10.00", "10.00", prior_lines={0: "10.00"}),
                                  [auto(rule("fixed", "15.00", currency="USD"))], NOW)
        assert result.total == D("10.00")

    def test_prior_cart_discount_is_spread_first(self):
        result = pricing.evaluate(cart("10.00", "10.00", prior_cart="20.00"), [auto(rule(value="50"))], NOW)
        assert result.applied == []


class TestStacking:
    def test_stackables_combine_in_priority_order(self):
        low = rule(value="10", priority=0)
        high = rule("fixed", "10.00", currency="USD", priority=5)
        result = pricing.evaluate(cart("100.00"), [auto(low), auto(high)], NOW)
        assert amounts(result) == ["-10.00", "-9.00"]  # fixed first, then 10% of 90

    def test_best_set_wins_and_a_beaten_code_is_not_an_error(self):
        stack = [auto(rule(value="10")), auto(rule(value="5"))]
        exclusive = coded(rule(value="20", stackable=False), "BIG")
        result = pricing.evaluate(cart("100.00"), [*stack, exclusive], NOW)
        assert amounts(result) == ["-20.00"] and result.errors == []
        weak = coded(rule(value="3", stackable=False), "WEAK")
        result = pricing.evaluate(cart("100.00"), [*stack, weak], NOW)
        assert sorted(amounts(result)) == ["-10.00", "-4.50"] and result.errors == []

    def test_tie_goes_to_the_set_with_free_shipping(self):
        shipping = auto(rule("free_shipping", "0"))
        exclusive = auto(rule("fixed", "0.00", currency="USD", stackable=False))
        result = pricing.evaluate(cart("10.00"), [exclusive, shipping], NOW)
        assert [a.free_shipping for a in result.applied] == [True]

    def test_same_promotion_twice_applies_once_preferring_the_code(self):
        r = rule(value="10", automatic=True)
        result = pricing.evaluate(cart("100.00"), [auto(r), coded(r, "TEN")], NOW)
        assert [a["code"] for a in result.adjustments()] == ["TEN"]

    def test_at_most_max_applied_promotions(self):
        many = [auto(rule(value="1", priority=i)) for i in range(pricing.MAX_APPLIED + 3)]
        result = pricing.evaluate(cart("100.00"), many, NOW)
        assert len(result.adjustments()) == pricing.MAX_APPLIED

    def test_automatic_code_is_auto_prefixed_and_short(self):
        r = rule(value="10")
        [adj] = pricing.evaluate(cart("10.00"), [auto(r)], NOW).adjustments()
        assert adj["code"] == f"auto:{r.key}" and adj["meta"]["source"] == "automatic"
        assert adj["meta"]["issuer"] == "promotions"
