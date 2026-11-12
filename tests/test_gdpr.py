"""GDPR: export, erase keeps the economics and the per-user count, merge."""
import pytest
from stapel_core.gdpr.owners import pseudonymize

from stapel_promotions.gdpr import PromotionsGDPRProvider, erase_subject
from stapel_promotions.models import PromoCode, Redemption, RedemptionAttempt
from stapel_promotions.services.redemption import merge_users

from .factories import make_code, make_promotion
from .orders import line, request

pytestmark = pytest.mark.django_db


def test_export_and_erase(orders, user):
    p = make_promotion(code="ONCE", usage_limit_per_user=1, created_by=str(user.pk))
    make_code(p, "GIFT", assigned_user_id=str(user.pk))
    assert orders.checkout(request([line(0, "30.00")], user=user, codes=["ONCE"]), order_id="9")["placed"]
    exported = PromotionsGDPRProvider().export(user.pk)
    assert exported["redemptions"][0]["amount"] == "3.00" and exported["redemptions"][0]["order_id"] == "9"
    assert exported["assigned_codes"] == [{"code": "GIFT", "promotion": p.key, "is_active": True}]

    counts = erase_subject("account", user.pk)
    assert counts == {"redemptions": 1, "attempts": 1, "assigned_codes": 1, "promotions_created": 1}
    alias = pseudonymize(str(user.pk))
    row = Redemption.objects.get()
    assert row.user_id == alias and str(row.amount) == "3.00" and row.status == "committed"
    assert RedemptionAttempt.objects.get().user_id == alias
    gift = PromoCode.objects.get(code="GIFT")
    assert gift.assigned_user_id == alias and gift.is_active is False
    assert PromotionsGDPRProvider().export(user.pk) == {"redemptions": [], "assigned_codes": []}
    assert erase_subject("account", user.pk)["redemptions"] == 0
    assert erase_subject("workspace", user.pk) is None


def test_meta_on_the_order_snapshot_carries_no_user_data(orders, user):
    make_promotion(code="Q")
    answer = orders.quote(request([line(0, "30.00"), line(1, "5.00")], user=user, codes=["Q"]))
    assert str(user.pk) not in str([a["meta"] for a in answer["adjustments"]]).replace("'line_index'", "")
    assert set(answer["adjustments"][0]["meta"]) == {"issuer", "promotion_id", "promotion_key", "source",
                                                    "kind", "free_shipping", "allocation"}


def test_user_merged_moves_the_guest_history(orders, user, other_user):
    from stapel_core.comm import emit

    make_promotion(code="ONCE", usage_limit_per_user=1)
    assert orders.checkout(request([line(0, "30.00")], user=other_user, codes=["ONCE"]))["placed"]
    emit("user.merged", {"from_user_id": str(other_user.pk), "into_user_id": str(user.pk)})
    assert Redemption.objects.get().user_id == str(user.pk)
    out = orders.checkout(request([line(0, "30.00")], user=user, codes=["ONCE"]))
    assert out["errors"][0]["code"] == "error.409.promo_code_user_limit_reached"
    assert merge_users("x", "y") == {"redemptions": 0, "attempts": 0, "codes": 0}
