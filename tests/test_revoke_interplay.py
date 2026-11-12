"""Revoke and issue meet release semantics, ownership and the staff code pages.

Each feature has its own suite; these are the seams between them: the revoked
refusal carries ``code`` like every other code refusal, a revoked code reads
as revoked on the batch page and in the CSV export, an issued code follows the
shopper through ``user.merged`` and erasure without a replay minting again,
and a cancel returns the use without giving the code back.
"""
import csv
import io

import pytest
from stapel_core.comm import call

from stapel_promotions.errors import ERR_409_PROMO_CODE_REVOKED
from stapel_promotions.gdpr import erase_subject
from stapel_promotions.models import CodeIssue, PromoCode, Redemption, RedemptionStatus
from stapel_promotions.services import redemption
from stapel_promotions.services import staff as staff_service

from .factories import make_code, make_promotion
from .orders import line, request

pytestmark = pytest.mark.django_db

V1 = "/promotions/api/v1"


def _revoke(code, reason="reward_reversed"):
    return call("promotions.codes.revoke", {"code": code, "reason": reason})


@pytest.fixture
def promo(db):
    return make_promotion(value="10", usage_limit_total=1)


class TestTheRevokedRefusalNamesTheCode:
    def test_validate(self, promo):
        make_code(promo, "GIFT-1")
        _revoke("GIFT-1")
        outcome = staff_service.validate_code("gift-1", user_id=None, currency="USD")
        assert (outcome["error"], outcome["params"]) == (ERR_409_PROMO_CODE_REVOKED, {"code": "GIFT-1"})

    def test_validate_endpoint(self, promo, anon_client):
        make_code(promo, "GIFT-1")
        _revoke("GIFT-1")
        body = anon_client.post(f"{V1}/codes/validate/", {"code": "gift-1", "currency": "USD"},
                                format="json").json()
        assert (body["error"], body["params"]) == (ERR_409_PROMO_CODE_REVOKED, {"code": "GIFT-1"})

    def test_quote(self, orders, promo, user):
        make_code(promo, "GIFT-1")
        _revoke("GIFT-1")
        answer = orders.quote(request([line(0, "40.00")], user=user, codes=["GIFT-1"]))
        [error] = answer["errors"]
        assert (error["code"], error["params"]) == (ERR_409_PROMO_CODE_REVOKED, {"code": "GIFT-1"})

    def test_reserve(self, orders, promo, user):
        make_code(promo, "GIFT-1")
        payload = request([line(0, "40.00")], user=user, codes=["GIFT-1"])
        answer = orders.quote(payload)
        _revoke("GIFT-1")
        _, reserved = orders.reserve(payload, answer)
        [error] = reserved["errors"]
        assert (error["code"], error["params"]) == (ERR_409_PROMO_CODE_REVOKED, {"code": "GIFT-1"})


class TestStaffPagesShowRevocation:
    @pytest.fixture
    def batch(self, promo):
        make_code(promo, "B1-KEEP", batch="b1")
        make_code(promo, "B1-GONE", batch="b1")
        make_code(promo, "B2-GONE", batch="b2")
        _revoke("B1-GONE", reason="fraud")
        return promo

    def test_the_batch_listing(self, batch, staff_client):
        body = staff_client.get(f"{V1}/staff/promotions/{batch.pk}/codes/?batch=b1").json()
        by_code = {c["code"]: c for c in body["items"]}
        assert set(by_code) == {"B1-KEEP", "B1-GONE"}
        assert by_code["B1-GONE"]["revoked_at"] and by_code["B1-GONE"]["revoke_reason"] == "fraud"
        assert (by_code["B1-KEEP"]["revoked_at"], by_code["B1-KEEP"]["revoke_reason"]) == (None, None)

    def test_the_csv_export(self, batch, staff_client):
        r = staff_client.get(f"{V1}/staff/promotions/{batch.pk}/codes/export/?batch=b1")
        header, *rows = list(csv.reader(io.StringIO(b"".join(r.streaming_content).decode())))
        table = {row[0]: dict(zip(header, row)) for row in rows}
        assert set(table) == {"B1-KEEP", "B1-GONE"}
        gone = PromoCode.objects.get(code="B1-GONE")
        assert table["B1-GONE"]["revoked_at"] == gone.revoked_at.isoformat()
        assert table["B1-GONE"]["revoke_reason"] == "fraud"
        assert (table["B1-KEEP"]["revoked_at"], table["B1-KEEP"]["revoke_reason"]) == ("", "")


class TestIssuedCodesFollowTheShopper:
    @pytest.fixture
    def reward(self, db):
        return make_promotion(key="referral-reward", kind="fixed", value="5.00", currency="USD")

    def _issue(self, assigned):
        return call("promotions.codes.issue", {
            "promotion_key": "referral-reward", "assigned_user_id": assigned,
            "idempotency_key": "referrals:reward:7",
        })

    def test_merge_moves_the_issued_code_and_a_replay_mints_nothing(self, reward):
        [code] = self._issue("guest-42")["codes"]
        redemption.merge_users("guest-42", "acct-1")
        assert PromoCode.objects.get(code=code).assigned_user_id == "acct-1"
        assert self._issue("guest-42")["codes"] == [code]
        assert PromoCode.objects.count() == 1 and CodeIssue.objects.count() == 1

    def test_erasure_retires_the_issued_code_and_a_replay_mints_nothing(self, reward):
        [code] = self._issue("acct-9")["codes"]
        erase_subject("account", "acct-9")
        row = PromoCode.objects.get(code=code)
        assert row.is_active is False and row.assigned_user_id != "acct-9"
        assert self._issue("acct-9")["codes"] == [code]
        assert PromoCode.objects.count() == 1

    def test_the_redemption_of_an_issued_code_records_its_actor(self, orders, reward, user):
        [code] = self._issue(str(user.pk))["codes"]
        out = orders.checkout(request([line(0, "40.00")], user=user, codes=[code]))
        assert out["placed"], out
        row = Redemption.objects.get()
        assert (row.user_id, row.actor_id, row.code.code) == (str(user.pk), str(user.pk), code)


class TestCancelAfterRevoke:
    def test_cancel_returns_the_use_and_the_code_stays_revoked(self, orders, promo, user):
        make_code(promo, "GIFT-1")
        out = orders.checkout(request([line(0, "40.00")], user=user, codes=["GIFT-1"]))
        assert out["placed"], out
        _revoke("GIFT-1")
        assert orders.release(out["key"], reason="cancelled") == {"ok": True, "status": RedemptionStatus.RELEASED}
        assert Redemption.objects.get().status == RedemptionStatus.RELEASED
        assert redemption.usage_of(promo.pk) == {"committed": 0, "reserved": 0}
        row = PromoCode.objects.get(code="GIFT-1")
        assert row.revoked_at is not None and row.revoke_reason == "reward_reversed"
        again = orders.checkout(request([line(0, "40.00")], user=user, codes=["GIFT-1"]))
        assert [e["code"] for e in again["errors"]] == [ERR_409_PROMO_CODE_REVOKED]
