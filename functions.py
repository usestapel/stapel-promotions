"""comm Functions provided by stapel-promotions (schemas in ``schemas/functions/``).

- ``promotions.quote`` — the orders adjuster (``kind=discount``).
- ``promotions.reserve`` / ``.commit`` / ``.release`` — the orders checkout hook.
- ``promotions.codes.issue`` — mint codes of a promotion (a referral reward),
  idempotent on an optional ``idempotency_key``.
- ``promotions.codes.revoke`` — withdraw one issued code (a reversed reward).

A malformed request raises (the caller sees a failed call and fails
closed); a shopper's bad code is an ``errors[]`` entry, never an exception.
"""
from stapel_core.comm import function


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
    from .services.issuing import issue

    return issue(payload)


@function("promotions.codes.revoke")
def promotions_codes_revoke(payload):
    from .services.issuing import revoke

    return revoke(payload)
