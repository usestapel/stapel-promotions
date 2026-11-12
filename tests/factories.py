"""Promotions and codes written directly, for tests that start after staff setup."""
import itertools
from decimal import Decimal

from stapel_promotions.models import PromoCode, Promotion

_seq = itertools.count(1)


def make_promotion(*, kind="percent", value="10.00", code=None, **extra) -> Promotion:
    n = next(_seq)
    fields = {"key": f"promo-{n}", "name": f"Promotion {n}", "kind": kind, "value": Decimal(value)}
    if kind == "free_shipping":
        fields["value"] = Decimal("0.00")
    for name in ("min_subtotal", "max_discount"):
        if extra.get(name) is not None:
            extra[name] = Decimal(str(extra[name]))
    fields.update(extra)
    promotion = Promotion.objects.create(**fields)
    if code:
        PromoCode.objects.create(promotion=promotion, code=code.upper())
    return promotion


def make_code(promotion, code, **extra) -> PromoCode:
    return PromoCode.objects.create(promotion=promotion, code=code.upper(), **extra)
