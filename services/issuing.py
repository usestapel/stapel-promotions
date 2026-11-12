"""Codes minted and withdrawn by other services (``promotions.codes.*``).

``issue`` is idempotent on the caller's ``idempotency_key``: the
:class:`~stapel_promotions.models.CodeIssue` row is inserted BEFORE the codes
are minted, inside one transaction, so a concurrent duplicate waits on the
unique index and then reads the winner's batch — a retry after a lost answer
never mints a second code. Without a key every call mints (the 0.1.0 shape).

``revoke`` withdraws one code: it never applies again (validate, quote and
reserve refuse it with ``error.409.promo_code_revoked``); redemptions already
committed stay as they are — a discount that was granted is history, not a
debt. Revoking twice is a no-op that answers the same.
"""
from __future__ import annotations

import hashlib
import json

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import CodeIssue, PromoCode, Promotion, RedemptionStatus
from . import codes as code_text

ISSUE_MAX = 100


class IdempotencyConflict(ValueError):
    """The idempotency key already names a different issue request."""


def _issue_request(payload: dict) -> dict:
    usage_limit = payload.get("usage_limit", 1)
    return {
        "promotion_key": str(payload["promotion_key"]),
        "count": max(1, min(int(payload.get("count") or 1), ISSUE_MAX)),
        "prefix": payload.get("prefix") or "",
        "usage_limit": None if usage_limit is None else int(usage_limit),
        "assigned_user_id": str(payload.get("assigned_user_id") or ""),
    }


def _fingerprint(request: dict) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()


def _batch_codes(batch: str) -> list[str]:
    return list(PromoCode.objects.filter(batch=batch).order_by("id").values_list("code", flat=True))


def _replay(key: str, fingerprint: str) -> dict:
    issue = CodeIssue.objects.select_related("promotion").get(idempotency_key=key)
    if issue.fingerprint != fingerprint:
        raise IdempotencyConflict(f"idempotency_key {key!r} was used for a different issue request")
    return {"promotion_key": issue.promotion.key, "codes": _batch_codes(issue.batch)}


def issue(payload: dict) -> dict:
    """``{promotion_key, codes}``; unknown promotion raises ``LookupError``."""
    request = _issue_request(payload)
    promotion = Promotion.objects.filter(key=request["promotion_key"]).first()
    if promotion is None:
        raise LookupError(f"promotion {request['promotion_key']!r} not found")
    key = payload.get("idempotency_key") or ""

    def mint() -> str:
        batch, _ = code_text.generate(
            promotion, request["count"], prefix=request["prefix"],
            usage_limit=request["usage_limit"], assigned_user_id=request["assigned_user_id"],
        )
        return batch

    if not key:
        return {"promotion_key": promotion.key, "codes": _batch_codes(mint())}
    fingerprint = _fingerprint(request)
    if CodeIssue.objects.filter(idempotency_key=key).exists():
        return _replay(key, fingerprint)
    try:
        with transaction.atomic():
            row = CodeIssue.objects.create(idempotency_key=key, promotion=promotion, fingerprint=fingerprint)
            row.batch = mint()
            row.save(update_fields=["batch"])
    except IntegrityError:
        # A concurrent call with the same key won the insert; answer with its codes.
        return _replay(key, fingerprint)
    return {"promotion_key": promotion.key, "codes": _batch_codes(row.batch)}


def revoke(payload: dict) -> dict:
    """``{code, code_id, revoked, already_revoked, redeemed, revoked_at}``.

    Unknown code raises ``LookupError``. ``redeemed`` says the code has
    committed redemptions, which revoking leaves untouched.
    """
    if payload.get("code_id") is not None:
        lookup = {"pk": payload["code_id"]}
    else:
        lookup = {"code": code_text.normalize(payload.get("code"))}
    with transaction.atomic():
        row = PromoCode.objects.select_for_update().filter(**lookup).first()
        if row is None:
            raise LookupError("promo code not found")
        already = row.revoked_at is not None
        if not already:
            row.revoked_at = timezone.now()
            row.revoke_reason = str(payload.get("reason") or "")[:64]
            row.save(update_fields=["revoked_at", "revoke_reason", "updated_at"])
        redeemed = row.redemptions.filter(status=RedemptionStatus.COMMITTED).exists()
    return {
        "code": row.code,
        "code_id": row.pk,
        "revoked": True,
        "already_revoked": already,
        "redeemed": redeemed,
        "revoked_at": row.revoked_at.isoformat(),
    }


__all__ = ["ISSUE_MAX", "IdempotencyConflict", "issue", "revoke"]
