"""The pricing engine: which promotions apply to a cart, and by how much.

Pure: no database, no clock, no settings — the caller hands in the cart,
the candidate rules with their usage, and ``now``. Every rule of spec §4
lives here once; ``services/quote.py`` loads the inputs and
``services/redemption.py`` re-runs the same function under the row locks.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from . import money
from .errors import (
    ERR_403_PROMO_CODE_REQUIRES_ACCOUNT,
    ERR_409_PROMO_CODE_EXHAUSTED,
    ERR_409_PROMO_CODE_MIN_SUBTOTAL,
    ERR_409_PROMO_CODE_NOT_ACTIVE,
    ERR_409_PROMO_CODE_NOT_APPLICABLE,
    ERR_409_PROMO_CODE_REVOKED,
    ERR_409_PROMO_CODE_USER_LIMIT_REACHED,
)

PERCENT = "percent"
FIXED = "fixed"
FREE_SHIPPING = "free_shipping"
SCOPE_CART = "cart"
SCOPE_TYPE = "purchasable_type"
SCOPE_KEYS = "keys"

SOURCE_CODE = "code"
SOURCE_AUTOMATIC = "automatic"
#: ``meta.issuer`` on every adjustment this package emits; adjustments that
#: carry it are ignored when they come back in ``adjustments_so_far``.
ISSUER = "promotions"
AUTO_CODE_PREFIX = "auto:"
CODES_FIELD = "codes"
#: Promotions one cart may receive (orders accepts 20 adjustments in total,
#: shared with shipping, tax and fees).
MAX_APPLIED = 5


@dataclass(frozen=True)
class Line:
    index: int
    purchasable_type: str
    purchasable_key: str
    line_total: Decimal


@dataclass(frozen=True)
class Cart:
    currency: str
    subtotal: Decimal
    user_id: str | None
    lines: tuple[Line, ...]
    #: Discounts earlier adjusters already gave, per line index (positive).
    prior_line_discounts: dict = field(default_factory=dict)
    #: Cart-level discounts earlier adjusters already gave (positive).
    prior_cart_discount: Decimal = money.ZERO


@dataclass(frozen=True)
class Rule:
    """A promotion as the engine sees it (a snapshot of the row)."""

    id: int
    key: str
    name: str
    kind: str
    value: Decimal
    currency: str = ""
    max_discount: Decimal | None = None
    min_subtotal: Decimal | None = None
    scope: str = SCOPE_CART
    scope_type: str = ""
    scope_keys: frozenset = frozenset()
    starts_at: dt.datetime | None = None
    ends_at: dt.datetime | None = None
    is_active: bool = True
    automatic: bool = False
    stackable: bool = True
    priority: int = 0
    usage_limit_total: int | None = None
    usage_limit_per_user: int | None = None
    #: free_shipping only: delivery method codes it waives; empty = all.
    free_shipping_methods: tuple = ()


@dataclass(frozen=True)
class Code:
    id: int
    code: str
    is_active: bool = True
    usage_limit: int | None = None
    assigned_user_id: str = ""
    revoked: bool = False


@dataclass(frozen=True)
class Usage:
    """Holding uses counted for one candidate (excluding the caller's own attempt)."""

    total: int = 0
    per_user: int = 0
    per_code: int = 0


@dataclass(frozen=True)
class Candidate:
    rule: Rule
    code: Code | None
    usage: Usage = Usage()

    @property
    def source(self) -> str:
        return SOURCE_CODE if self.code is not None else SOURCE_AUTOMATIC

    @property
    def adjustment_code(self) -> str:
        return self.code.code if self.code is not None else f"{AUTO_CODE_PREFIX}{self.rule.key}"


@dataclass(frozen=True)
class Applied:
    """One promotion granted: its total and the per-line shares."""

    candidate: Candidate
    amount: Decimal
    shares: tuple[tuple[int, Decimal], ...]

    @property
    def free_shipping(self) -> bool:
        return self.candidate.rule.kind == FREE_SHIPPING


@dataclass
class Result:
    applied: list[Applied] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    #: Automatic promotions eligible in every way but their cap.
    capped_automatic: list[int] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((a.amount for a in self.applied), money.ZERO)

    def adjustments(self) -> list[dict]:
        return [adjustment(applied) for applied in self.applied]


def error(key: str, **params) -> dict:
    return {"code": key, "field": CODES_FIELD, "params": params}


# ── eligibility ───────────────────────────────────────────────────────────


def _in_window(rule: Rule, now: dt.datetime) -> bool:
    if rule.starts_at is not None and now < rule.starts_at:
        return False
    if rule.ends_at is not None and now >= rule.ends_at:
        return False
    return True


def in_scope(rule: Rule, line: Line) -> bool:
    if rule.scope == SCOPE_CART:
        return True
    if line.purchasable_type != rule.scope_type:
        return False
    if rule.scope == SCOPE_TYPE:
        return True
    return line.purchasable_key in rule.scope_keys


def line_bases(cart: Cart) -> dict[int, Decimal]:
    """Each line's total minus discounts earlier adjusters gave it (spec §4.3)."""
    lines = list(cart.lines)
    bases = {
        line.index: max(money.ZERO, line.line_total - cart.prior_line_discounts.get(line.index, money.ZERO))
        for line in lines
    }
    if cart.prior_cart_discount > 0 and lines:
        shares = money.allocate(cart.prior_cart_discount, [bases[line.index] for line in lines])
        for line, share in zip(lines, shares):
            bases[line.index] -= share
    return bases


def check(candidate: Candidate, cart: Cart, now: dt.datetime, *, scope: bool = True) -> tuple[str | None, dict]:
    """The first reason *candidate* cannot apply, or ``(None, {})`` (spec §4.2).

    ``scope=False`` skips the last rule (a line in scope), for a check made
    without a cart (the public validate endpoint).
    """
    rule, code = candidate.rule, candidate.code
    label = {"code": candidate.adjustment_code}
    if code is not None and code.revoked:
        return ERR_409_PROMO_CODE_REVOKED, label
    if not rule.is_active or not _in_window(rule, now) or (code is not None and not code.is_active):
        return ERR_409_PROMO_CODE_NOT_ACTIVE, label
    needs_currency = rule.kind == FIXED or rule.min_subtotal is not None
    if (rule.currency or needs_currency) and rule.currency != cart.currency:
        return ERR_409_PROMO_CODE_NOT_APPLICABLE, label
    if rule.usage_limit_per_user is not None and not cart.user_id:
        return ERR_403_PROMO_CODE_REQUIRES_ACCOUNT, label
    if rule.usage_limit_total is not None and candidate.usage.total >= rule.usage_limit_total:
        return ERR_409_PROMO_CODE_EXHAUSTED, label
    if code is not None and code.usage_limit is not None and candidate.usage.per_code >= code.usage_limit:
        return ERR_409_PROMO_CODE_EXHAUSTED, label
    if rule.usage_limit_per_user is not None and candidate.usage.per_user >= rule.usage_limit_per_user:
        return ERR_409_PROMO_CODE_USER_LIMIT_REACHED, label
    if rule.min_subtotal is not None and cart.subtotal < rule.min_subtotal:
        return ERR_409_PROMO_CODE_MIN_SUBTOTAL, {
            **label, "min_subtotal": money.to_wire(rule.min_subtotal), "currency": rule.currency,
        }
    if not scope:
        return None, {}
    bases = line_bases(cart)
    if not any(bases[line.index] > 0 for line in cart.lines if in_scope(rule, line)):
        return ERR_409_PROMO_CODE_NOT_APPLICABLE, label
    return None, {}


_CAP_REASONS = (ERR_409_PROMO_CODE_EXHAUSTED, ERR_409_PROMO_CODE_USER_LIMIT_REACHED)


# ── stacking and amounts ──────────────────────────────────────────────────


def _order(candidate: Candidate) -> tuple[int, int]:
    return (-candidate.rule.priority, candidate.rule.id)


def apply_set(candidates: list[Candidate], cart: Cart, limit: int = MAX_APPLIED) -> list[Applied]:
    """Apply *candidates* in priority order, each on the base the previous left.

    At most *limit* promotions apply (the first ones in priority order).
    """
    remaining = line_bases(cart)
    applied: list[Applied] = []
    for candidate in sorted(candidates, key=_order):
        if len(applied) >= limit:
            break
        rule = candidate.rule
        lines = [line for line in cart.lines if in_scope(rule, line) and remaining[line.index] > 0]
        if not lines:
            continue
        if rule.kind == FREE_SHIPPING:
            applied.append(Applied(candidate, money.ZERO, ()))
            continue
        bases = [remaining[line.index] for line in lines]
        base = sum(bases, money.ZERO)
        if rule.kind == PERCENT:
            amount = money.percent_of(base, rule.value)
            if rule.max_discount is not None:
                amount = min(amount, rule.max_discount)
        else:
            amount = min(rule.value, base)
        if amount <= 0:
            continue
        shares = money.allocate(amount, bases)
        for line, share in zip(lines, shares):
            remaining[line.index] -= share
        applied.append(Applied(
            candidate, amount,
            tuple((line.index, share) for line, share in zip(lines, shares) if share > 0),
        ))
    return applied


def _rank(applied: list[Applied]):
    total = sum((a.amount for a in applied), money.ZERO)
    free = any(a.free_shipping for a in applied)
    priority = sum(a.candidate.rule.priority for a in applied)
    ids = tuple(sorted(a.candidate.rule.id for a in applied))
    return (total, free, priority, tuple(-i for i in ids))


def evaluate(cart: Cart, candidates: list[Candidate], now: dt.datetime, limit: int = MAX_APPLIED) -> Result:
    """Rules 2-5 of spec §4 over already-loaded candidates."""
    result = Result()
    eligible: list[Candidate] = []
    seen: set[int] = set()
    for candidate in sorted(candidates, key=lambda c: (c.code is None, _order(c))):
        if candidate.rule.id in seen:
            continue
        reason, params = check(candidate, cart, now)
        if reason is None:
            seen.add(candidate.rule.id)
            eligible.append(candidate)
        elif candidate.code is not None:
            result.errors.append(error(reason, **params))
        elif reason in _CAP_REASONS:
            result.capped_automatic.append(candidate.rule.id)
    if not eligible:
        return result
    options = [[c for c in eligible if c.rule.stackable]]
    options += [[c] for c in eligible if not c.rule.stackable]
    outcomes = [apply_set(option, cart, limit) for option in options if option]
    result.applied = max(outcomes, key=_rank)
    return result


# ── output ────────────────────────────────────────────────────────────────


def _meta(applied: Applied) -> dict:
    """Ids, kinds and shares only: orders keeps meta past GDPR erasure.

    The free-shipping marker is the delivery brick's contract:
    ``free_shipping: true`` and, when narrowed, ``free_shipping_methods``.
    """
    rule = applied.candidate.rule
    meta = {
        "issuer": ISSUER,
        "promotion_id": rule.id,
        "promotion_key": rule.key,
        "source": applied.candidate.source,
        "kind": rule.kind,
        "free_shipping": applied.free_shipping,
    }
    if applied.free_shipping and rule.free_shipping_methods:
        meta["free_shipping_methods"] = list(rule.free_shipping_methods)
    return meta


def adjustment(applied: Applied) -> dict:
    """One adjustment per promotion (orders bounds a quote at 20 adjustments).

    ``line_index`` names the line when the discount sits on exactly one;
    otherwise it is null and ``meta.allocation`` carries the per-line shares
    (line indexes and amounts only — meta survives GDPR erasure).
    """
    candidate = applied.candidate
    meta = _meta(applied)
    line_index = None
    if not applied.free_shipping:
        meta["allocation"] = [
            {"line_index": index, "amount": money.to_wire(-share)} for index, share in applied.shares
        ]
        if len(applied.shares) == 1 and candidate.rule.scope != SCOPE_CART:
            line_index = applied.shares[0][0]
    return {
        "kind": "discount",
        "code": candidate.adjustment_code,
        "label": candidate.rule.name,
        "amount": money.to_wire(-applied.amount) if applied.amount > 0 else money.to_wire(money.ZERO),
        "line_index": line_index,
        "meta": meta,
    }


__all__ = [
    "Applied", "Candidate", "Cart", "Code", "Line", "Result", "Rule", "Usage",
    "adjustment", "apply_set", "check", "error", "evaluate", "in_scope", "line_bases",
]
