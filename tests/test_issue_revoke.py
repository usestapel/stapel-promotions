"""Codes minted and withdrawn by other services: issue idempotency, revoke."""
import threading

import pytest
from django.db import connection, connections
from stapel_core.comm import call
from stapel_core.comm.exceptions import FunctionCallError, SchemaValidationError

from stapel_promotions.errors import ERR_409_PROMO_CODE_REVOKED
from stapel_promotions.models import CodeIssue, PromoCode, Redemption, RedemptionStatus
from stapel_promotions.services import staff as staff_service
from stapel_promotions.services.issuing import IdempotencyConflict

from .factories import make_code, make_promotion
from .orders import line, request

V1 = "/promotions/api/v1"


def _issue(user, **extra):
    return call("promotions.codes.issue", {
        "promotion_key": "referral-reward", "assigned_user_id": str(user.pk), **extra,
    })


@pytest.fixture
def reward(db):
    return make_promotion(key="referral-reward", kind="fixed", value="5.00", currency="USD")


# ── issue: idempotency_key ────────────────────────────────────────────────


@pytest.mark.django_db
class TestIssueIdempotency:
    def test_a_retry_with_the_key_answers_the_same_code_and_mints_nothing(self, reward, user):
        first = _issue(user, idempotency_key="referrals:reward:7")
        again = _issue(user, idempotency_key="referrals:reward:7")
        assert again == first and len(first["codes"]) == 1
        assert PromoCode.objects.filter(promotion=reward).count() == 1
        assert CodeIssue.objects.get().idempotency_key == "referrals:reward:7"

    def test_distinct_keys_mint_distinct_codes(self, reward, user):
        a = _issue(user, idempotency_key="k-1")["codes"]
        b = _issue(user, idempotency_key="k-2")["codes"]
        assert a != b and PromoCode.objects.count() == 2

    def test_without_a_key_every_call_mints(self, reward, user):
        _issue(user)
        _issue(user)
        assert PromoCode.objects.count() == 2 and not CodeIssue.objects.exists()

    def test_the_key_reused_for_another_request_fails_the_call(self, reward, user, other_user):
        _issue(user, idempotency_key="k-1")
        with pytest.raises(FunctionCallError) as caught:
            _issue(other_user, idempotency_key="k-1")
        assert isinstance(caught.value.__cause__, IdempotencyConflict)
        assert PromoCode.objects.count() == 1

    def test_a_failed_mint_leaves_no_key_behind(self, reward, user):
        with pytest.raises(Exception):
            _issue(user, idempotency_key="k-1", prefix="no spaces!")
        assert not CodeIssue.objects.exists()
        assert len(_issue(user, idempotency_key="k-1")["codes"]) == 1

    def test_the_key_is_bounded_by_the_schema(self, reward, user):
        with pytest.raises(SchemaValidationError):
            _issue(user, idempotency_key="")
        with pytest.raises(SchemaValidationError):
            _issue(user, idempotency_key="k" * 129)


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql",
                    reason="needs Postgres row locks: set STAPEL_TEST_DATABASE_URL")
def test_concurrent_retries_mint_exactly_one_code():
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(username="racer", email="racer@example.com")
    make_promotion(key="referral-reward", kind="fixed", value="5.00", currency="USD")
    workers = 6
    barrier = threading.Barrier(workers)
    answers, errors = [], []

    def worker():
        try:
            barrier.wait()
            answers.append(_issue(user, idempotency_key="referrals:reward:race"))
        except Exception as exc:  # noqa: BLE001 — surfaced by the assert below
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len({tuple(a["codes"]) for a in answers}) == 1 and len(answers) == workers
    assert PromoCode.objects.count() == 1 and CodeIssue.objects.count() == 1


# ── revoke ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestRevoke:
    def test_revoke_by_code_text_is_case_insensitive_and_idempotent(self, reward):
        row = make_code(reward, "GIFT-1")
        first = call("promotions.codes.revoke", {"code": " gift-1 ", "reason": "reward_reversed"})
        assert first["revoked"] is True and first["already_revoked"] is False
        assert (first["code"], first["code_id"], first["redeemed"]) == ("GIFT-1", row.pk, False)
        again = call("promotions.codes.revoke", {"code_id": row.pk, "reason": "other"})
        assert again["already_revoked"] is True and again["revoked_at"] == first["revoked_at"]
        row.refresh_from_db()
        assert row.revoked_at is not None and row.revoke_reason == "reward_reversed"

    def test_an_unknown_code_fails_the_call(self, db):
        with pytest.raises(LookupError):
            call("promotions.codes.revoke", {"code": "NOPE", "reason": "x"})

    @pytest.mark.parametrize("payload", [
        {"code": "A"},
        {"reason": "x"},
        {"code": "A", "code_id": 1, "reason": "x"},
        {"code": "A", "reason": "x", "idempotency_key": "k"},
    ])
    def test_the_payload_is_validated(self, db, payload):
        with pytest.raises(SchemaValidationError):
            call("promotions.codes.revoke", payload)

    def test_validate_refuses_a_revoked_code_with_its_own_key(self, reward):
        make_code(reward, "GIFT-1")
        assert staff_service.validate_code("gift-1", user_id=None, currency="USD")["valid"] is True
        call("promotions.codes.revoke", {"code": "GIFT-1", "reason": "x"})
        outcome = staff_service.validate_code("gift-1", user_id=None, currency="USD")
        assert (outcome["valid"], outcome["error"]) == (False, ERR_409_PROMO_CODE_REVOKED)

    def test_the_validate_endpoint_answers_the_revoked_key(self, reward, anon_client):
        make_code(reward, "GIFT-1")
        call("promotions.codes.revoke", {"code": "GIFT-1", "reason": "x"})
        body = anon_client.post(f"{V1}/codes/validate/", {"code": "gift-1", "currency": "USD"},
                                format="json").json()
        assert body["error"] == ERR_409_PROMO_CODE_REVOKED

    def test_quote_refuses_a_revoked_code(self, orders, reward, user):
        make_code(reward, "GIFT-1")
        call("promotions.codes.revoke", {"code": "GIFT-1", "reason": "x"})
        answer = orders.quote(request([line(0, "40.00")], user=user, codes=["GIFT-1"]))
        assert answer["adjustments"] == []
        assert [e["code"] for e in answer["errors"]] == [ERR_409_PROMO_CODE_REVOKED]

    def test_reserve_refuses_a_code_revoked_after_the_quote(self, orders, reward, user):
        make_code(reward, "GIFT-1")
        payload = request([line(0, "40.00")], user=user, codes=["GIFT-1"])
        answer = orders.quote(payload)
        assert answer["errors"] == [] and answer["adjustments"]
        call("promotions.codes.revoke", {"code": "GIFT-1", "reason": "x"})
        _, reserved = orders.reserve(payload, answer)
        assert reserved["ok"] is False
        assert [e["code"] for e in reserved["errors"]] == [ERR_409_PROMO_CODE_REVOKED]
        assert not Redemption.objects.exists()

    def test_committed_redemptions_are_untouched(self, orders, reward, user):
        make_code(reward, "GIFT-1")
        out = orders.checkout(request([line(0, "40.00")], user=user, codes=["GIFT-1"]))
        assert out["placed"]
        answer = call("promotions.codes.revoke", {"code": "GIFT-1", "reason": "x"})
        assert answer["redeemed"] is True
        [row] = Redemption.objects.all()
        assert row.status == RedemptionStatus.COMMITTED and str(row.amount) == "5.00"

    def test_a_revoked_code_shows_in_the_staff_list(self, reward, staff_client):
        make_code(reward, "GIFT-1")
        call("promotions.codes.revoke", {"code": "GIFT-1", "reason": "reward_reversed"})
        body = staff_client.get(f"{V1}/staff/promotions/{reward.pk}/codes/").json()
        [code] = body["items"]
        assert code["revoke_reason"] == "reward_reversed" and code["revoked_at"]
