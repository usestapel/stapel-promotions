"""HTTP surface: public reads, staff CRUD, permissions, refusals."""
import datetime as dt

import pytest
from django.utils import timezone

from stapel_promotions.models import PromoCode, Promotion

from .factories import make_code, make_promotion
from .orders import line, request

pytestmark = pytest.mark.django_db
V1 = "/promotions/api/v1"


def err(resp):
    return resp.json()["localizable_error"]


class TestPublic:
    def test_active_lists_live_automatic_promotions_only(self, anon_client):
        live = make_promotion(kind="free_shipping", automatic=True, min_subtotal="50.00", currency="USD")
        make_promotion(automatic=True, is_active=False)
        make_promotion(automatic=True, ends_at=timezone.now() - dt.timedelta(seconds=1))
        make_promotion(code="CODEONLY")
        body = anon_client.get(f"{V1}/active/").json()
        assert [p["key"] for p in body["items"]] == [live.key] and body["truncated"] is False
        assert body["items"][0]["min_subtotal"] == "50.00"
        assert "usage_limit_total" not in body["items"][0]

    def test_active_is_bounded(self, anon_client, settings):
        settings.STAPEL_PROMOTIONS = {"ACTIVE_LIST_LIMIT": 2}
        for _ in range(3):
            make_promotion(automatic=True)
        body = anon_client.get(f"{V1}/active/").json()
        assert len(body["items"]) == 2 and body["truncated"] is True

    def test_validate_a_good_code(self, anon_client):
        make_promotion(value="15", code="SPRING")
        body = anon_client.post(f"{V1}/codes/validate/", {"code": "spring"}, format="json").json()
        assert body["valid"] is True and body["code"] == "SPRING" and body["error"] is None
        assert body["promotion"]["value"] == "15.00"

    def test_validate_reports_the_reason(self, anon_client, auth_client):
        assert anon_client.post(f"{V1}/codes/validate/", {"code": "nope"}, format="json").json()["error"] == \
            "error.400.promo_code_unknown"
        make_promotion(code="BIG", min_subtotal="100.00", currency="USD")
        body = auth_client.post(f"{V1}/codes/validate/", {"code": "BIG", "currency": "USD", "subtotal": "20.00"},
                                format="json").json()
        assert body["valid"] is False and body["error"] == "error.409.promo_code_min_subtotal"
        assert body["params"]["min_subtotal"] == "100.00"
        make_promotion(code="MEMBERS", usage_limit_per_user=1)
        body = anon_client.post(f"{V1}/codes/validate/", {"code": "MEMBERS"}, format="json").json()
        assert body["error"] == "error.403.promo_code_requires_account"

    def test_validate_is_throttled(self, anon_client, settings):
        settings.STAPEL_PROMOTIONS = {"VALIDATE_RATE": "3/min"}
        codes = [anon_client.post(f"{V1}/codes/validate/", {"code": f"X{i}"}, format="json").status_code
                 for i in range(4)]
        assert codes == [200, 200, 200, 429]

    def test_a_throttled_validate_says_when_to_retry(self, anon_client, settings):
        """429 carries the Retry-After header and the same seconds in params
        (core's envelope names it ``retry_after``), so a countdown can render."""
        settings.STAPEL_PROMOTIONS = {"VALIDATE_RATE": "1/min"}
        anon_client.post(f"{V1}/codes/validate/", {"code": "X0"}, format="json")
        refused = anon_client.post(f"{V1}/codes/validate/", {"code": "X1"}, format="json")
        assert refused.status_code == 429
        wait = int(refused["Retry-After"])
        assert 0 < wait <= 60
        body = refused.json()
        assert body["localizable_error"].startswith("error.429.")
        assert body["params"]["retry_after"] == wait


class TestStaffGuard:
    @pytest.mark.parametrize("path", ["/staff/promotions/", "/staff/promotions/1/", "/staff/promotions/1/codes/",
                                      "/staff/promotions/1/redemptions/"])
    def test_anonymous_and_members_are_refused(self, anon_client, auth_client, path):
        assert anon_client.get(V1 + path).status_code in (401, 403)
        assert auth_client.get(V1 + path).status_code == 403


class TestStaffPromotions:
    def test_create_list_update_delete(self, staff_client):
        resp = staff_client.post(f"{V1}/staff/promotions/", {
            "name": "Spring sale", "kind": "percent", "value": "20", "max_discount": "50.00",
            "scope": "purchasable_type", "scope_type": "listing", "usage_limit_total": 100,
        }, format="json")
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["key"].startswith("spring-sale-") and body["usage"] == {"committed": 0, "reserved": 0}
        assert body["live"] is True and body["scope_keys"] == [] and body["free_shipping_methods"] == []
        pid = body["id"]
        page = staff_client.get(f"{V1}/staff/promotions/?kind=percent").json()
        assert [p["id"] for p in page["items"]] == [pid]
        resp = staff_client.patch(f"{V1}/staff/promotions/{pid}/", {"is_active": False}, format="json")
        assert resp.status_code == 200 and resp.json()["live"] is False
        assert staff_client.get(f"{V1}/staff/promotions/?active=true").json()["items"] == []
        assert staff_client.delete(f"{V1}/staff/promotions/{pid}/").status_code == 204
        assert staff_client.get(f"{V1}/staff/promotions/{pid}/").status_code == 404

    @pytest.mark.parametrize("body,key", [
        ({"kind": "percent", "value": "120"}, "error.400.promotion_value_invalid"),
        ({"kind": "fixed", "value": "5.00"}, "error.400.promotion_currency_required"),
        ({"kind": "percent", "value": "5", "min_subtotal": "10.00"}, "error.400.promotion_currency_required"),
        ({"kind": "percent", "value": "5", "scope": "keys", "scope_type": "listing"},
         "error.400.promotion_scope_invalid"),
        ({"kind": "percent", "value": "5", "starts_at": "2027-01-02T00:00:00Z", "ends_at": "2027-01-01T00:00:00Z"},
         "error.400.promotion_window_invalid"),
    ])
    def test_invalid_shapes_are_refused(self, staff_client, body, key):
        resp = staff_client.post(f"{V1}/staff/promotions/", {"name": "x", **body}, format="json")
        assert resp.status_code == 400 and err(resp) == key

    def test_free_shipping_methods_only_on_free_shipping(self, staff_client):
        resp = staff_client.post(f"{V1}/staff/promotions/", {"name": "Free pickup", "kind": "free_shipping",
                                                              "free_shipping_methods": ["pickup", "pickup"]},
                                 format="json")
        assert resp.json()["free_shipping_methods"] == ["pickup"]
        resp = staff_client.post(f"{V1}/staff/promotions/", {"name": "Ten", "kind": "percent", "value": "10",
                                                              "free_shipping_methods": ["pickup"]}, format="json")
        assert resp.json()["free_shipping_methods"] == []

    def test_patch_revalidates_the_merged_promotion(self, staff_client):
        p = make_promotion(kind="percent", value="10")
        resp = staff_client.patch(f"{V1}/staff/promotions/{p.pk}/", {"kind": "fixed"}, format="json")
        assert resp.status_code == 400 and err(resp) == "error.400.promotion_currency_required"

    def test_duplicate_key_is_409(self, staff_client):
        make_promotion(key="black-friday")
        resp = staff_client.post(f"{V1}/staff/promotions/", {"name": "x", "key": "black-friday",
                                                              "kind": "free_shipping"}, format="json")
        assert resp.status_code == 409 and err(resp) == "error.409.promotion_key_taken"

    def test_used_promotion_cannot_be_deleted(self, staff_client, orders, user):
        p = make_promotion(code="USED")
        assert orders.checkout(request([line(0, "10.00")], user=user, codes=["USED"]))["placed"]
        resp = staff_client.delete(f"{V1}/staff/promotions/{p.pk}/")
        assert resp.status_code == 409 and err(resp) == "error.409.promotion_has_redemptions"
        page = staff_client.get(f"{V1}/staff/promotions/{p.pk}/redemptions/").json()
        assert [(r["status"], r["amount"], r["code"]) for r in page["items"]] == [("committed", "1.00", "USED")]
        assert staff_client.get(f"{V1}/staff/promotions/{p.pk}/").json()["usage"]["committed"] == 1

    def test_search_by_exact_code(self, staff_client):
        p = make_promotion(code="FINDME")
        make_promotion()
        assert [i["id"] for i in staff_client.get(f"{V1}/staff/promotions/?q=findme").json()["items"]] == [p.pk]

    def test_list_pages_by_anchor(self, staff_client):
        ids = [make_promotion().pk for _ in range(3)]
        first = staff_client.get(f"{V1}/staff/promotions/?limit=2").json()
        assert [i["id"] for i in first["items"]] == ids[::-1][:2] and first["has_next"]
        second = staff_client.get(f"{V1}/staff/promotions/?limit=2&anchor={first['next_anchor']}").json()
        assert [i["id"] for i in second["items"]] == [ids[0]]
        assert staff_client.get(f"{V1}/staff/promotions/?anchor=garbage").status_code == 200


class TestStaffCodes:
    def test_create_normalises_and_refuses_duplicates(self, staff_client):
        p = make_promotion()
        resp = staff_client.post(f"{V1}/staff/promotions/{p.pk}/codes/", {"code": "welcome-5"}, format="json")
        assert resp.status_code == 201 and resp.json()["code"] == "WELCOME-5"
        resp = staff_client.post(f"{V1}/staff/promotions/{p.pk}/codes/", {"code": "Welcome-5"}, format="json")
        assert resp.status_code == 409 and err(resp) == "error.409.promo_code_taken"
        resp = staff_client.post(f"{V1}/staff/promotions/{p.pk}/codes/", {"code": "no spaces?"}, format="json")
        assert resp.status_code == 400 and err(resp) == "error.400.promo_code_format_invalid"

    def test_generate_a_batch(self, staff_client):
        p = make_promotion()
        resp = staff_client.post(f"{V1}/staff/promotions/{p.pk}/codes/generate/",
                                 {"count": 25, "prefix": "bf-", "usage_limit": 1}, format="json")
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["created"] == 25 and len(set(body["codes"])) == 25
        assert all(c.startswith("BF-") and len(c) == 13 for c in body["codes"])
        assert PromoCode.objects.filter(batch=body["batch"], usage_limit=1).count() == 25
        page = staff_client.get(f"{V1}/staff/promotions/{p.pk}/codes/?limit=200").json()
        assert page["count"] == 25 and page["items"][0]["used"] == 0

    def test_generate_is_bounded_per_call(self, staff_client, settings):
        settings.STAPEL_PROMOTIONS = {"GENERATE_MAX_PER_CALL": 5}
        p = make_promotion()
        resp = staff_client.post(f"{V1}/staff/promotions/{p.pk}/codes/generate/", {"count": 6}, format="json")
        assert resp.status_code == 400 and err(resp) == "error.400.generate_count_too_large"

    def test_update_and_delete(self, staff_client, orders, user):
        p = make_promotion()
        code = make_code(p, "BYE")
        resp = staff_client.patch(f"{V1}/staff/codes/{code.pk}/", {"is_active": False, "usage_limit": 3},
                                  format="json")
        assert resp.json()["is_active"] is False and resp.json()["usage_limit"] == 3
        assert staff_client.delete(f"{V1}/staff/codes/{code.pk}/").status_code == 204
        used = make_code(p, "KEPT")
        assert orders.checkout(request([line(0, "10.00")], user=user, codes=["KEPT"]))["placed"]
        resp = staff_client.delete(f"{V1}/staff/codes/{used.pk}/")
        assert resp.status_code == 409 and err(resp) == "error.409.promo_code_has_redemptions"
        assert staff_client.patch(f"{V1}/staff/codes/999999/", {}, format="json").status_code == 404

    def test_unknown_promotion_is_404(self, staff_client):
        resp = staff_client.get(f"{V1}/staff/promotions/999/codes/")
        assert resp.status_code == 404 and err(resp) == "error.404.promotion_not_found"
        assert Promotion.objects.count() == 0


class TestCodesBatchAndExport:
    def test_codes_filter_by_batch(self, staff_client):
        from stapel_promotions.models import PromoCode

        p = make_promotion()
        PromoCode.objects.create(promotion=p, code="A1", batch="b1")
        PromoCode.objects.create(promotion=p, code="A2", batch="b2")
        body = staff_client.get(f"{V1}/staff/promotions/{p.pk}/codes/?batch=b1").json()
        assert [c["code"] for c in body["items"]] == ["A1"]

    def test_export_streams_csv_with_safe_cells(self, staff_client):
        import csv
        import io

        from stapel_promotions.models import PromoCode

        p = make_promotion()
        PromoCode.objects.create(promotion=p, code="SAFE1", batch="=HYPERLINK(1)", assigned_user_id="@7")
        PromoCode.objects.create(promotion=p, code="OTHER", batch="b2")
        r = staff_client.get(f"{V1}/staff/promotions/{p.pk}/codes/export/?batch==HYPERLINK(1)")
        assert r.status_code == 200 and r.streaming and r["Content-Type"].startswith("text/csv")
        rows = list(csv.reader(io.StringIO(b"".join(r.streaming_content).decode())))
        assert rows[0] == ["code", "batch", "is_active", "usage_limit", "assigned_user_id", "created_at",
                           "revoked_at", "revoke_reason"]
        assert [row[:5] for row in rows[1:]] == [["SAFE1", "'=HYPERLINK(1)", "true", "", "'@7"]]

    def test_export_of_an_unknown_promotion_is_404_and_staff_only(self, staff_client, auth_client):
        assert staff_client.get(f"{V1}/staff/promotions/999999/codes/export/").status_code == 404
        p = make_promotion()
        assert auth_client.get(f"{V1}/staff/promotions/{p.pk}/codes/export/").status_code == 403
