"""The redemption ledger: the orders checkout hook (reserve / commit / release).

``reserve`` ledgers exactly what the order snapshot grants: the payload's
``adjustments_so_far`` is the complete final list, and the adjustments this
package issued (``meta.issuer == "promotions"``) name the promotions and
amounts the order holds. Under ``select_for_update()`` on those
``Promotion`` rows (ordered by id) and their ``PromoCode`` rows, usage is
re-counted and every grant re-checked; one that no longer holds refuses the
checkout. Two shoppers racing for the last use end as one ``ok`` and one
``error.409.promo_code_exhausted``.

All three are idempotent on ``attempt_key`` (the orders checkout key); the
token is a convenience and may be null.
"""
from __future__ import annotations

import datetime as dt
import logging
import secrets
from collections import OrderedDict

from django.db import IntegrityError, transaction
from django.utils import timezone
from stapel_core.comm import mutate_and_emit

from .. import money, pricing
from ..errors import ERR_409_PROMOTION_EXHAUSTED
from ..models import (
    ATTEMPT_KEY_MAX_LENGTH,
    ORDER_ID_MAX_LENGTH,
    REASON_MAX_LENGTH,
    PromoCode,
    Redemption,
    RedemptionAttempt,
    RedemptionStatus,
)
from . import events
from .quote import PayloadInvalid, Request, load_candidates, parse_request

logger = logging.getLogger(__name__)

LIVE = (RedemptionStatus.RESERVED, RedemptionStatus.COMMITTED)
DEAD = (RedemptionStatus.RELEASED, RedemptionStatus.EXPIRED)


def _attempt_key(payload) -> str:
    key = payload.get("attempt_key") if isinstance(payload, dict) else None
    if not isinstance(key, str) or not key or len(key) > ATTEMPT_KEY_MAX_LENGTH:
        raise PayloadInvalid("attempt_key must be a non-empty string")
    return key


def _ttl() -> dt.timedelta:
    from ..conf import promotions_settings

    return dt.timedelta(minutes=int(promotions_settings.RESERVATION_TTL_MINUTES))


def grants(request: Request) -> "OrderedDict[int, dict]":
    """The snapshot's own adjustments grouped per promotion."""
    out: OrderedDict[int, dict] = OrderedDict()
    for adj in request.own_adjustments:
        meta = adj["meta"]
        try:
            pid = int(meta["promotion_id"])
            amount = -money.parse(adj.get("amount"), signed=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise PayloadInvalid(f"own adjustment without a promotion: {exc}") from exc
        if amount < 0:
            raise PayloadInvalid("a discount adjustment must not be positive")
        grant = out.setdefault(pid, {
            "amount": money.ZERO, "label": str(adj.get("label") or ""),
            "code": adj.get("code") if meta.get("source") == pricing.SOURCE_CODE else None,
            "free_shipping": bool(meta.get("free_shipping")),
        })
        grant["amount"] += amount
    if sum((g["amount"] for g in out.values()), money.ZERO) > request.cart.subtotal:
        raise PayloadInvalid("the snapshot grants more than the subtotal")
    return out


def _refusal(grant: dict, candidate, cart, now) -> dict | None:
    if candidate is None:
        return pricing.error(ERR_409_PROMOTION_EXHAUSTED, label=grant["label"])
    reason, params = pricing.check(candidate, cart, now)
    if reason is None:
        return None
    if candidate.code is not None:
        return pricing.error(reason, **params)
    return pricing.error(ERR_409_PROMOTION_EXHAUSTED, label=grant["label"])


def find_attempt(key: str):
    return RedemptionAttempt.objects.filter(attempt_key=key).first()


def _answer(attempt) -> dict:
    return {"ok": True, "token": attempt.token, "errors": []}


def reserve(payload: dict, *, now: dt.datetime | None = None) -> dict:
    """``{ok, token, errors}`` — hold the snapshot's promotions for this attempt."""
    now = now or timezone.now()
    key = _attempt_key(payload)
    request = parse_request(payload)
    granted = grants(request)
    existing = find_attempt(key)
    if existing is not None and existing.status in LIVE:
        return _answer(existing)
    if request.errors:
        return {"ok": False, "token": None, "errors": request.errors}
    cart = pricing.Cart(
        currency=request.cart.currency, subtotal=request.cart.subtotal,
        user_id=request.cart.user_id, lines=request.cart.lines,
    )
    lookup = Request(cart=cart, codes=[g["code"] for g in granted.values() if g["code"]])
    automatic = [pid for pid, g in granted.items() if not g["code"]]
    try:
        with transaction.atomic():
            attempt = None
            if existing is not None:
                attempt = RedemptionAttempt.objects.select_for_update().get(pk=existing.pk)
                if attempt.status in LIVE:
                    return _answer(attempt)
            candidates, unknown = load_candidates(
                lookup, now, lock=True, promotion_ids=automatic,
                exclude_attempt_id=attempt.pk if attempt is not None else None,
            )
            by_code = {c.code.code: c for c in candidates if c.code is not None}
            by_auto = {c.rule.id: c for c in candidates if c.code is None}
            errors = []
            for pid, grant in granted.items():
                candidate = by_code.get(grant["code"]) if grant["code"] else by_auto.get(pid)
                if candidate is not None and candidate.rule.id != pid:
                    candidate = None
                refusal = _refusal(grant, candidate, cart, now)
                if refusal is not None:
                    errors.append(refusal)
            if errors:
                return {"ok": False, "token": None, "errors": errors}
            attempt = _write_attempt(attempt, key, request, now)
            codes = {c.code: c for c in PromoCode.objects.filter(code__in=list(by_code))}
            Redemption.objects.bulk_create([
                Redemption(
                    attempt=attempt, promotion_id=pid,
                    code=codes.get(grant["code"]) if grant["code"] else None,
                    user_id=attempt.user_id, status=RedemptionStatus.RESERVED,
                    amount=grant["amount"], currency=cart.currency,
                    free_shipping=grant["free_shipping"],
                )
                for pid, grant in granted.items()
            ])
    except IntegrityError:
        # A concurrent reserve of the same attempt key won the insert.
        winner = find_attempt(key)
        if winner is None:
            raise
        return _answer(winner)
    return _answer(attempt)


def _write_attempt(attempt, key: str, request: Request, now: dt.datetime) -> RedemptionAttempt:
    fields = {
        "token": secrets.token_hex(16),
        "user_id": request.cart.user_id or "",
        "status": RedemptionStatus.RESERVED,
        "currency": request.cart.currency,
        "result": {"adjustments": request.own_adjustments},
        "order_id": "",
        "release_reason": "",
        "expires_at": now + _ttl(),
    }
    if attempt is None:
        with transaction.atomic():
            return RedemptionAttempt.objects.create(attempt_key=key, **fields)
    attempt.redemptions.all().delete()
    for name, value in fields.items():
        setattr(attempt, name, value)
    attempt.save()
    return attempt


def _locked(key: str):
    return RedemptionAttempt.objects.select_for_update().filter(attempt_key=key).first()


def _rows(attempt):
    return list(
        Redemption.objects.select_for_update(of=("self",))
        .select_related("promotion", "code").filter(attempt=attempt).order_by("id")
    )


def commit(payload: dict) -> dict:
    """Make the attempt's holds permanent; emit ``redemption.committed``."""
    key = _attempt_key(payload)
    order_id = str(payload.get("order_id") or "")[:ORDER_ID_MAX_LENGTH]
    with mutate_and_emit() as emit:
        attempt = _locked(key)
        if attempt is None:
            return {"ok": True, "status": "none"}
        if attempt.status == RedemptionStatus.COMMITTED:
            return {"ok": True, "status": attempt.status}
        if attempt.status in DEAD:
            # The order already holds the discount: the late commit wins.
            logger.warning("promotions: commit of %s attempt %s", attempt.status, key)
        rows = _rows(attempt)
        attempt.status = RedemptionStatus.COMMITTED
        attempt.order_id = order_id or attempt.order_id
        attempt.save(update_fields=["status", "order_id", "updated_at"])
        Redemption.objects.filter(attempt=attempt).update(status=RedemptionStatus.COMMITTED, updated_at=timezone.now())
        for row in rows:
            row.status = RedemptionStatus.COMMITTED
            emit(events.EVENT_COMMITTED, events.redemption_payload(row, attempt), key=str(row.promotion_id))
        for row in rows:
            _maybe_exhausted(emit, row.promotion, attempt)
    return {"ok": True, "status": RedemptionStatus.COMMITTED}


def _maybe_exhausted(emit, promotion, attempt) -> None:
    limit = promotion.usage_limit_total
    if limit is None:
        return
    used = Redemption.objects.filter(promotion=promotion, status=RedemptionStatus.COMMITTED).count()
    mine = Redemption.objects.filter(promotion=promotion, attempt=attempt).count()
    if used >= limit > used - mine:
        emit(events.EVENT_EXHAUSTED, events.exhausted_payload(promotion, used), key=str(promotion.pk))


def release(payload: dict) -> dict:
    """Free the attempt's holds (a failed checkout, a cancel, an orphan)."""
    from ..conf import promotions_settings

    key = _attempt_key(payload)
    reason = str(payload.get("reason") or "")[:REASON_MAX_LENGTH]
    with mutate_and_emit() as emit:
        attempt = _locked(key)
        if attempt is None:
            return {"ok": True, "status": "none"}
        if attempt.status in DEAD:
            return {"ok": True, "status": attempt.status}
        was_committed = attempt.status == RedemptionStatus.COMMITTED
        if was_committed and not promotions_settings.RETURN_USAGE_ON_RELEASE:
            return {"ok": True, "status": attempt.status}
        rows = _rows(attempt)
        attempt.status = RedemptionStatus.RELEASED
        attempt.release_reason = reason
        attempt.save(update_fields=["status", "release_reason", "updated_at"])
        Redemption.objects.filter(attempt=attempt).update(status=RedemptionStatus.RELEASED, updated_at=timezone.now())
        if was_committed:
            for row in rows:
                emit(events.EVENT_RELEASED, events.released_payload(row, attempt, reason), key=str(row.promotion_id))
    return {"ok": True, "status": RedemptionStatus.RELEASED}


# ── housekeeping and lifecycle ────────────────────────────────────────────


def expire_reservations(now: dt.datetime | None = None) -> int:
    """Reserved attempts past their hold TTL become expired."""
    now = now or timezone.now()
    with transaction.atomic():
        ids = list(RedemptionAttempt.objects.select_for_update()
                   .filter(status=RedemptionStatus.RESERVED, expires_at__lte=now).values_list("id", flat=True))
        RedemptionAttempt.objects.filter(id__in=ids).update(status=RedemptionStatus.EXPIRED, updated_at=now)
        Redemption.objects.filter(attempt_id__in=ids).update(status=RedemptionStatus.EXPIRED, updated_at=now)
    return len(ids)


def purge_attempts(now: dt.datetime | None = None) -> int:
    """Released and expired attempts older than ATTEMPT_TTL_DAYS are deleted."""
    from ..conf import promotions_settings

    now = now or timezone.now()
    cutoff = now - dt.timedelta(days=int(promotions_settings.ATTEMPT_TTL_DAYS))
    deleted, _ = RedemptionAttempt.objects.filter(status__in=DEAD, updated_at__lt=cutoff).delete()
    return deleted


def merge_users(from_user_id: str, into_user_id: str) -> dict:
    """``user.merged``: the guest's ledger and assigned codes follow the account."""
    return {
        "redemptions": Redemption.objects.filter(user_id=from_user_id).update(user_id=into_user_id),
        "attempts": RedemptionAttempt.objects.filter(user_id=from_user_id).update(user_id=into_user_id),
        "codes": PromoCode.objects.filter(assigned_user_id=from_user_id).update(assigned_user_id=into_user_id),
    }


def usage_of(promotion_id: int, now: dt.datetime | None = None) -> dict:
    """``{"committed": n, "reserved": n}`` live counts of one promotion."""
    now = now or timezone.now()
    rows = Redemption.objects.filter(promotion_id=promotion_id)
    return {
        "committed": rows.filter(status=RedemptionStatus.COMMITTED).count(),
        "reserved": rows.filter(status=RedemptionStatus.RESERVED, attempt__expires_at__gt=now).count(),
    }


__all__ = [
    "commit", "expire_reservations", "grants", "merge_users", "purge_attempts", "release",
    "reserve", "usage_of",
]
