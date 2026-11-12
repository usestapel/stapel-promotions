"""GDPR for stapel-promotions — the ledger rule: the person goes, the economics stay.

Erasure of an ``account``:

- ledger rows and attempts keep amounts and statuses; ``user_id`` becomes
  the fleet pseudonym (``stapel_core.gdpr.pseudonymize``), so the per-user
  caps of the erased person still count under one stable id;
- codes assigned to the person are deactivated and their assignment is
  pseudonymized (nobody can use them any more);
- ``created_by`` on promotions (a staff id) is pseudonymized.

The adjustments orders snapshots carry no personal data (``meta`` holds
promotion ids, kinds and line shares only), so nothing outside this
package needs erasing on our behalf.
"""
from __future__ import annotations

from stapel_core.gdpr import GDPRProvider


class PromotionsGDPRProvider(GDPRProvider):
    section = "promotions"

    def export(self, user_id) -> dict:
        from .models import PromoCode, Redemption

        user_id = str(user_id)
        return {
            "redemptions": [
                {
                    "promotion": r.promotion.key,
                    "code": r.code.code if r.code_id else None,
                    "status": r.status,
                    "amount": str(r.amount),
                    "currency": r.currency,
                    "free_shipping": r.free_shipping,
                    "order_id": r.attempt.order_id or None,
                    "created_at": r.created_at.isoformat(),
                }
                for r in Redemption.objects.filter(user_id=user_id).select_related("promotion", "code", "attempt")
            ],
            "assigned_codes": [
                {"code": c.code, "promotion": c.promotion.key, "is_active": c.is_active}
                for c in PromoCode.objects.filter(assigned_user_id=user_id).select_related("promotion")
            ],
        }

    def delete(self, user_id) -> None:
        erase_subject("account", user_id)

    #: After erasure the rows hold amounts and a pseudonym: that IS the
    #: anonymisation, so one operation answers both names.
    anonymize = delete


OWNER = PromotionsGDPRProvider.section
SUBJECT_TYPES = ("account",)


def erase_subject(subject_type, subject_key, workspace_id=None):
    """Erase one account's personal data. Idempotent; counts what it touched."""
    from django.db import transaction
    from stapel_core.gdpr.owners import pseudonymize

    from .models import PromoCode, Promotion, Redemption, RedemptionAttempt

    if subject_type != "account" or not subject_key:
        return None
    user_id = str(subject_key)
    alias = pseudonymize(user_id)
    with transaction.atomic():
        redemptions = Redemption.objects.filter(user_id=user_id).update(user_id=alias)
        attempts = RedemptionAttempt.objects.filter(user_id=user_id).update(user_id=alias)
        codes = PromoCode.objects.filter(assigned_user_id=user_id).update(assigned_user_id=alias, is_active=False)
        promotions = Promotion.objects.filter(created_by=user_id).update(created_by=alias)
    return {"redemptions": redemptions, "attempts": attempts, "assigned_codes": codes,
            "promotions_created": promotions}
