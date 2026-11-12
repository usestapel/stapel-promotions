"""comm Functions provided by stapel-promotions (schemas in ``schemas/functions/``).

- ``promotions.quote`` — the orders adjuster (``kind=discount``).
- ``promotions.reserve`` / ``.commit`` / ``.release`` — the orders checkout hook.
- ``promotions.codes.issue`` — mint codes of a promotion (a referral reward).

A malformed request raises (the caller sees a failed call and fails
closed); a shopper's bad code is an ``errors[]`` entry, never an exception.
"""
from stapel_core.comm import function

ISSUE_MAX = 100


@function("promotions.quote")
def promotions_quote(payload):
    from .services.quote import quote

    return quote(payload)


@function("promotions.reserve")
def promotions_reserve(payload):
    from .services.redemption import reserve

    return reserve(payload)


@function("promotions.commit")
def promotions_commit(payload):
    from .services.redemption import commit

    return commit(payload)


@function("promotions.release")
def promotions_release(payload):
    from .services.redemption import release

    return release(payload)


@function("promotions.codes.issue")
def promotions_codes_issue(payload):
    from .models import Promotion
    from .services.codes import generate

    promotion = Promotion.objects.filter(key=str(payload["promotion_key"])).first()
    if promotion is None:
        raise LookupError(f"promotion {payload['promotion_key']!r} not found")
    count = max(1, min(int(payload.get("count") or 1), ISSUE_MAX))
    usage_limit = payload.get("usage_limit", 1)
    _, codes = generate(
        promotion, count, prefix=payload.get("prefix") or "",
        usage_limit=None if usage_limit is None else int(usage_limit),
        assigned_user_id=str(payload.get("assigned_user_id") or ""),
    )
    return {"promotion_key": promotion.key, "codes": codes}
