"""``promotions.quote``: read the adjuster request, load candidates, price them.

No writes. The same loader runs under the row locks in ``redemption.py``
(``lock=True``): candidate ids are found first, then the ``Promotion`` rows
(ordered by id) and the ``PromoCode`` rows are locked, then the rules are
re-read from the locked rows and usage is counted — in that order.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count, Q
from django.utils import timezone

from .. import money, pricing
from ..errors import ERR_400_PROMO_CODE_UNKNOWN, ERR_400_PROMO_CODES_INVALID, ERR_400_PROMO_CODES_TOO_MANY
from ..models import CODE_MAX_LENGTH, PromoCode, Promotion, Redemption, RedemptionStatus
from . import codes as code_text


class PayloadInvalid(ValueError):
    """The orders request itself is malformed (not the shopper's context)."""


@dataclass
class Request:
    cart: pricing.Cart
    codes: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    #: Adjustments this package issued, as the payload carries them back
    #: (the reserve payload holds the complete final list).
    own_adjustments: list[dict] = field(default_factory=list)


def _str(value, name: str, *, max_length: int = 255) -> str:
    if not isinstance(value, str) or len(value) > max_length:
        raise PayloadInvalid(f"{name} must be a string of at most {max_length} characters")
    return value


def _codes(context) -> tuple[list[str], list[dict]]:
    from ..conf import promotions_settings

    raw = (context or {}).get("codes") if isinstance(context, dict) else None
    if raw in (None, []):
        return [], []
    if not isinstance(raw, list) or not all(isinstance(c, str) and len(c) <= 2 * CODE_MAX_LENGTH for c in raw):
        return [], [pricing.error(ERR_400_PROMO_CODES_INVALID)]
    normalized = list(dict.fromkeys(code_text.normalize(c) for c in raw if code_text.normalize(c)))
    limit = int(promotions_settings.MAX_CODES_PER_CART)
    if len(normalized) > limit:
        return [], [pricing.error(ERR_400_PROMO_CODES_TOO_MANY, max=limit)]
    return normalized, []


def parse_request(payload: dict) -> Request:
    """The orders adjuster request as engine inputs (spec §4, contract §2)."""
    if not isinstance(payload, dict):
        raise PayloadInvalid("payload must be an object")
    try:
        currency = _str(payload.get("currency"), "currency", max_length=3)
        subtotal = money.parse(payload.get("subtotal"))
        user_id = payload.get("user_id")
        user_id = None if user_id in (None, "") else _str(str(user_id), "user_id", max_length=64)
        lines = []
        for raw in payload.get("lines") or []:
            lines.append(pricing.Line(
                index=int(raw["index"]),
                purchasable_type=_str(raw.get("purchasable_type"), "purchasable_type", max_length=32),
                purchasable_key=_str(str(raw.get("purchasable_key")), "purchasable_key"),
                line_total=money.parse(raw.get("line_total")),
            ))
        prior_lines: dict[int, Decimal] = {}
        prior_cart = money.ZERO
        own = []
        for adj in payload.get("adjustments_so_far") or []:
            meta = adj.get("meta") if isinstance(adj.get("meta"), dict) else {}
            if meta.get("issuer") == pricing.ISSUER:
                own.append(adj)
                continue
            amount = money.parse(adj.get("amount"), signed=True)
            if adj.get("kind") != "discount" or amount >= 0:
                continue
            if adj.get("line_index") is None:
                prior_cart += -amount
            else:
                index = int(adj["line_index"])
                prior_lines[index] = prior_lines.get(index, money.ZERO) + (-amount)
    except (KeyError, TypeError, ValueError) as exc:
        raise PayloadInvalid(str(exc)) from exc
    codes, errors = _codes(payload.get("context"))
    cart = pricing.Cart(
        currency=currency, subtotal=subtotal, user_id=user_id, lines=tuple(lines),
        prior_line_discounts=prior_lines, prior_cart_discount=prior_cart,
    )
    return Request(cart=cart, codes=codes, errors=errors, own_adjustments=own)


# ── loading ───────────────────────────────────────────────────────────────


def rule_of(p: Promotion) -> pricing.Rule:
    return pricing.Rule(
        id=p.pk, key=p.key, name=p.name, kind=p.kind, value=p.value, currency=p.currency,
        max_discount=p.max_discount, min_subtotal=p.min_subtotal, scope=p.scope,
        scope_type=p.scope_type, scope_keys=frozenset(str(k) for k in (p.scope_keys or [])),
        starts_at=p.starts_at, ends_at=p.ends_at, is_active=p.is_active, automatic=p.automatic,
        stackable=p.stackable, priority=p.priority, usage_limit_total=p.usage_limit_total,
        usage_limit_per_user=p.usage_limit_per_user,
        free_shipping_methods=tuple(str(m) for m in (p.free_shipping_methods or [])),
    )


def code_of(c: PromoCode) -> pricing.Code:
    return pricing.Code(
        id=c.pk, code=c.code, is_active=c.is_active, usage_limit=c.usage_limit,
        assigned_user_id=c.assigned_user_id, revoked=c.revoked_at is not None,
    )


def holding_q(now: dt.datetime) -> Q:
    """Ledger rows that count toward a cap at *now* (spec §2 "Usage")."""
    return Q(status=RedemptionStatus.COMMITTED) | Q(
        status=RedemptionStatus.RESERVED, attempt__expires_at__gt=now,
    )


def count_usage(promotion_ids, code_ids, user_id, now, exclude_attempt_id=None) -> dict:
    """``{"total": {pid: n}, "user": {pid: n}, "code": {cid: n}}`` of holding rows."""
    rows = Redemption.objects.filter(holding_q(now), promotion_id__in=list(promotion_ids))
    if exclude_attempt_id is not None:
        rows = rows.exclude(attempt_id=exclude_attempt_id)
    total = dict(rows.values_list("promotion_id").annotate(n=Count("id")).values_list("promotion_id", "n"))
    user = {}
    if user_id:
        user = dict(rows.filter(user_id=user_id).values_list("promotion_id").annotate(n=Count("id"))
                    .values_list("promotion_id", "n"))
    per_code = {}
    if code_ids:
        per_code = dict(rows.filter(code_id__in=list(code_ids)).values_list("code_id").annotate(n=Count("id"))
                        .values_list("code_id", "n"))
    return {"total": total, "user": user, "code": per_code}


def _live_automatic_ids(now: dt.datetime) -> list[int]:
    return list(
        Promotion.objects.filter(automatic=True, is_active=True)
        .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
        .order_by("id").values_list("id", flat=True)
    )


def load_candidates(request: Request, now: dt.datetime, *, lock: bool = False,
                    exclude_attempt_id: int | None = None,
                    promotion_ids: list[int] | None = None) -> tuple[list[pricing.Candidate], list[dict]]:
    """Candidates for *request* and the errors of codes that do not exist.

    ``promotion_ids`` restricts the automatic candidates (reserve passes the
    promotions the snapshot holds). With ``lock`` the caller must be inside a
    transaction.
    """
    user_id = request.cart.user_id or ""
    errors: list[dict] = []
    code_rows = {c.code: c for c in PromoCode.objects.filter(code__in=request.codes)}
    for text in request.codes:
        row = code_rows.get(text)
        if row is None or (row.assigned_user_id and row.assigned_user_id != user_id):
            code_rows.pop(text, None)
            errors.append(pricing.error(ERR_400_PROMO_CODE_UNKNOWN, code=text))
    auto_ids = _live_automatic_ids(now) if promotion_ids is None else list(promotion_ids)
    wanted = sorted(set(auto_ids) | {c.promotion_id for c in code_rows.values()})
    if lock:
        promotions = {p.pk: p for p in Promotion.objects.select_for_update().filter(id__in=wanted).order_by("id")}
        locked_codes = {c.pk: c for c in PromoCode.objects.select_for_update()
                        .filter(id__in=sorted(c.pk for c in code_rows.values())).order_by("id")}
    else:
        promotions = {p.pk: p for p in Promotion.objects.filter(id__in=wanted)}
        locked_codes = {c.pk: c for c in code_rows.values()}
    usage = count_usage(promotions, locked_codes, user_id, now, exclude_attempt_id)
    candidates = []
    for text in request.codes:
        row = code_rows.get(text)
        if row is None or row.pk not in locked_codes or row.promotion_id not in promotions:
            continue
        candidates.append(_candidate(promotions[row.promotion_id], locked_codes[row.pk], usage))
    for pid in auto_ids:
        promotion = promotions.get(pid)
        if promotion is not None and promotion.automatic:
            candidates.append(_candidate(promotion, None, usage))
    return candidates, errors


def _candidate(promotion: Promotion, code: PromoCode | None, usage: dict) -> pricing.Candidate:
    return pricing.Candidate(
        rule=rule_of(promotion),
        code=code_of(code) if code is not None else None,
        usage=pricing.Usage(
            total=usage["total"].get(promotion.pk, 0),
            per_user=usage["user"].get(promotion.pk, 0),
            per_code=usage["code"].get(code.pk, 0) if code is not None else 0,
        ),
    )


def quote(payload: dict, *, now: dt.datetime | None = None) -> dict:
    """The adjuster answer ``{"adjustments", "errors"}`` (no writes)."""
    now = now or timezone.now()
    request = parse_request(payload)
    candidates, unknown = load_candidates(request, now)
    result = pricing.evaluate(request.cart, candidates, now)
    return {"adjustments": result.adjustments(), "errors": request.errors + unknown + result.errors}


__all__ = ["PayloadInvalid", "Request", "count_usage", "holding_q", "load_candidates", "parse_request", "quote"]
