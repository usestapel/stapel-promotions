"""Staff writes and the public reads (active promotions, validate a code).

Every write re-validates the whole promotion (the PATCH merge included), so
a row in the table is always one the pricing engine can price.
"""
from __future__ import annotations

import datetime as dt
import secrets
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from .. import money, pricing
from ..errors import (
    ERR_400_PROMO_CODE_UNKNOWN,
    ERR_400_PROMOTION_CURRENCY_REQUIRED,
    ERR_400_PROMOTION_SCOPE_INVALID,
    ERR_400_PROMOTION_VALUE_INVALID,
    ERR_400_PROMOTION_WINDOW_INVALID,
    ERR_404_PROMO_CODE_NOT_FOUND,
    ERR_404_PROMOTION_NOT_FOUND,
    ERR_409_PROMO_CODE_HAS_REDEMPTIONS,
    ERR_409_PROMO_CODE_TAKEN,
    ERR_409_PROMOTION_HAS_REDEMPTIONS,
    ERR_409_PROMOTION_KEY_TAKEN,
)
from ..models import KEY_MAX_LENGTH, PromoCode, Promotion, PromotionKind, PromotionScope, Redemption
from . import PromotionsError
from . import codes as code_text
from .quote import code_of, count_usage, rule_of

PROMOTION_FIELDS = (
    "key", "name", "description", "kind", "value", "currency", "max_discount", "min_subtotal",
    "scope", "scope_type", "scope_keys", "starts_at", "ends_at", "is_active", "automatic",
    "stackable", "priority", "usage_limit_total", "usage_limit_per_user", "free_shipping_methods",
)


def _bad(key: str, **params):
    return PromotionsError(400, key, params)


def validate_promotion(values: dict) -> dict:
    """The full promotion shape; normalises what has one right form."""
    kind = values["kind"]
    value = values.get("value") or money.ZERO
    if kind == PromotionKind.PERCENT and not (0 < value <= 100):
        raise _bad(ERR_400_PROMOTION_VALUE_INVALID, kind=kind)
    if kind == PromotionKind.FIXED and not (0 < value < money.VALUE_LIMIT):
        raise _bad(ERR_400_PROMOTION_VALUE_INVALID, kind=kind)
    if kind == PromotionKind.FREE_SHIPPING:
        values["value"] = money.ZERO
        values["free_shipping_methods"] = list(dict.fromkeys(values.get("free_shipping_methods") or []))
    else:
        values["free_shipping_methods"] = []
    if kind != PromotionKind.PERCENT:
        values["max_discount"] = None
    if values.get("max_discount") is not None and values["max_discount"] <= 0:
        raise _bad(ERR_400_PROMOTION_VALUE_INVALID, kind=kind)
    if values.get("min_subtotal") is not None and values["min_subtotal"] <= 0:
        values["min_subtotal"] = None
    currency = (values.get("currency") or "").upper()
    values["currency"] = currency
    if not currency and (kind == PromotionKind.FIXED or values.get("min_subtotal") is not None):
        raise _bad(ERR_400_PROMOTION_CURRENCY_REQUIRED)
    scope = values.get("scope") or PromotionScope.CART
    keys = [str(k) for k in (values.get("scope_keys") or [])]
    if scope == PromotionScope.CART:
        values["scope_type"], keys = "", []
    elif not values.get("scope_type") or (scope == PromotionScope.KEYS and not keys):
        raise _bad(ERR_400_PROMOTION_SCOPE_INVALID, scope=scope)
    if scope == PromotionScope.PURCHASABLE_TYPE:
        keys = []
    values["scope"], values["scope_keys"] = scope, list(dict.fromkeys(keys))
    starts, ends = values.get("starts_at"), values.get("ends_at")
    if starts is not None and ends is not None and ends <= starts:
        raise _bad(ERR_400_PROMOTION_WINDOW_INVALID)
    return values


def _new_key(name: str) -> str:
    stem = slugify(name)[: KEY_MAX_LENGTH - 7].strip("-") or "promotion"
    return f"{stem}-{secrets.token_hex(3)}"


def get_promotion(promotion_id) -> Promotion:
    promotion = Promotion.objects.filter(pk=promotion_id).first()
    if promotion is None:
        raise PromotionsError(404, ERR_404_PROMOTION_NOT_FOUND)
    return promotion


def list_promotions(kind=None, active=None, q=None):
    qs = Promotion.objects.all()
    if kind:
        qs = qs.filter(kind=kind)
    if active is not None:
        qs = qs.filter(is_active=active)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(key__icontains=q) | Q(codes__code=code_text.normalize(q))).distinct()
    return qs


def create_promotion(user, data: dict) -> Promotion:
    values = validate_promotion({k: data[k] for k in PROMOTION_FIELDS if k in data})
    values["key"] = values.get("key") or _new_key(values["name"])
    try:
        with transaction.atomic():
            return Promotion.objects.create(created_by=str(user.pk), **values)
    except IntegrityError:
        raise PromotionsError(409, ERR_409_PROMOTION_KEY_TAKEN, {"key": values["key"]}) from None


def update_promotion(promotion_id, data: dict) -> Promotion:
    with transaction.atomic():
        promotion = Promotion.objects.select_for_update().filter(pk=promotion_id).first()
        if promotion is None:
            raise PromotionsError(404, ERR_404_PROMOTION_NOT_FOUND)
        values = {name: getattr(promotion, name) for name in PROMOTION_FIELDS}
        values.update({k: data[k] for k in PROMOTION_FIELDS if k in data})
        values = validate_promotion(values)
        values["key"] = values.get("key") or promotion.key
        for name, value in values.items():
            setattr(promotion, name, value)
        try:
            with transaction.atomic():
                promotion.save()
        except IntegrityError:
            raise PromotionsError(409, ERR_409_PROMOTION_KEY_TAKEN, {"key": values["key"]}) from None
    return promotion


def delete_promotion(promotion_id) -> None:
    promotion = get_promotion(promotion_id)
    if Redemption.objects.filter(promotion=promotion).exists():
        raise PromotionsError(409, ERR_409_PROMOTION_HAS_REDEMPTIONS)
    promotion.delete()


def list_codes(promotion_id, batch: str | None = None):
    qs = PromoCode.objects.filter(promotion=get_promotion(promotion_id))
    if batch:
        qs = qs.filter(batch=batch)
    return qs


#: Columns of the staff CSV export of a promotion's codes.
CODE_EXPORT_COLUMNS = ("code", "batch", "is_active", "usage_limit", "assigned_user_id", "created_at")
#: A spreadsheet reads a cell starting with one of these as a formula.
_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value) -> str:
    """A cell a spreadsheet will not evaluate (CSV/formula injection guard)."""
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_LEADS) else text


def export_code_rows(promotion_id, batch: str | None = None):
    """Rows of the codes CSV (header first) in pk order, as a lazy iterator.

    The promotion is resolved now (an unknown id raises before anything
    streams); the rows are read in chunks while the response is written.
    """
    qs = list_codes(promotion_id, batch).order_by("pk")

    def rows():
        yield list(CODE_EXPORT_COLUMNS)
        for code in qs.iterator(chunk_size=500):
            yield [csv_cell(v) for v in (
                code.code, code.batch, "true" if code.is_active else "false",
                "" if code.usage_limit is None else code.usage_limit,
                code.assigned_user_id, code.created_at.isoformat(),
            )]

    return rows()


def create_code(promotion_id, data: dict) -> PromoCode:
    promotion = get_promotion(promotion_id)
    code = code_text.require_well_formed(data["code"])
    try:
        with transaction.atomic():
            return PromoCode.objects.create(
                promotion=promotion, code=code, usage_limit=data.get("usage_limit"),
                assigned_user_id=str(data.get("assigned_user_id") or ""),
                is_active=data.get("is_active", True),
            )
    except IntegrityError:
        raise PromotionsError(409, ERR_409_PROMO_CODE_TAKEN, {"code": code}) from None


def get_code(code_id) -> PromoCode:
    code = PromoCode.objects.select_related("promotion").filter(pk=code_id).first()
    if code is None:
        raise PromotionsError(404, ERR_404_PROMO_CODE_NOT_FOUND)
    return code


def update_code(code_id, data: dict) -> PromoCode:
    code = get_code(code_id)
    for name in ("is_active", "usage_limit"):
        if name in data:
            setattr(code, name, data[name])
    code.save()
    return code


def delete_code(code_id) -> None:
    code = get_code(code_id)
    if Redemption.objects.filter(code=code).exists():
        raise PromotionsError(409, ERR_409_PROMO_CODE_HAS_REDEMPTIONS)
    code.delete()


def generate_codes(promotion_id, data: dict) -> tuple[str, list[str]]:
    return code_text.generate(
        get_promotion(promotion_id), int(data["count"]), prefix=data.get("prefix") or "",
        length=data.get("length"), usage_limit=data.get("usage_limit"),
    )


def list_redemptions(promotion_id):
    return Redemption.objects.filter(promotion=get_promotion(promotion_id)).select_related("attempt", "code")


# ── public reads ──────────────────────────────────────────────────────────


def live(promotion: Promotion, now: dt.datetime) -> bool:
    return bool(
        promotion.is_active
        and (promotion.starts_at is None or promotion.starts_at <= now)
        and (promotion.ends_at is None or now < promotion.ends_at)
    )


def active_promotions(now: dt.datetime | None = None) -> tuple[list[Promotion], bool]:
    """Automatic promotions live now, highest priority first (bounded)."""
    from ..conf import promotions_settings

    now = now or timezone.now()
    limit = int(promotions_settings.ACTIVE_LIST_LIMIT)
    rows = list(
        Promotion.objects.filter(automatic=True, is_active=True)
        .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
        .order_by("-priority", "id")[: limit + 1]
    )
    return rows[:limit], len(rows) > limit


def validate_code(raw_code: str, *, user_id: str | None, currency: str | None = None,
                  subtotal: Decimal | None = None, now: dt.datetime | None = None) -> dict:
    """Would this code apply? ``{code, valid, error, params, promotion}``.

    Without a cart the scope rule is not checked (the quote does that); a
    missing currency or subtotal skips the rules that need them.
    """
    now = now or timezone.now()
    code = code_text.normalize(raw_code)
    row = None
    if code_text.is_well_formed(code):
        row = PromoCode.objects.select_related("promotion").filter(code=code).first()
    if row is None or (row.assigned_user_id and row.assigned_user_id != (user_id or "")):
        return {"code": code, "valid": False, "error": ERR_400_PROMO_CODE_UNKNOWN,
                "params": {"code": code}, "promotion": None}
    promotion = row.promotion
    usage = count_usage([promotion.pk], [row.pk], user_id or "", now)
    candidate = pricing.Candidate(
        rule=rule_of(promotion),
        code=code_of(row),
        usage=pricing.Usage(total=usage["total"].get(promotion.pk, 0),
                            per_user=usage["user"].get(promotion.pk, 0),
                            per_code=usage["code"].get(row.pk, 0)),
    )
    cart = pricing.Cart(
        currency=(currency or promotion.currency or "").upper(),
        subtotal=subtotal if subtotal is not None else money.AMOUNT_LIMIT - money.QUANTUM,
        user_id=user_id, lines=(),
    )
    reason, params = pricing.check(candidate, cart, now, scope=False)
    return {"code": code, "valid": reason is None, "error": reason, "params": params or {},
            "promotion": promotion}


__all__ = [
    "PROMOTION_FIELDS", "active_promotions", "create_code", "create_promotion", "delete_code",
    "delete_promotion", "generate_codes", "get_code", "get_promotion", "list_codes",
    "list_promotions", "list_redemptions", "live", "update_code", "update_promotion",
    "validate_code", "validate_promotion",
]
