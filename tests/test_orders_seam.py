"""promotions against the orders seam as orders' own schemas define it (see ``orders_seam``)."""
import pytest
from stapel_core.comm import call

from stapel_promotions.models import Redemption
from stapel_promotions.preset import SETTINGS_DEFAULTS

from .factories import make_promotion
from .orders_seam import GUEST, check_request, check_result, line, quote_request, run_hook

pytestmark = pytest.mark.django_db

ADJUSTER = SETTINGS_DEFAULTS["STAPEL_ORDERS"]["ADJUSTERS"]["promotions"]["quote"]
HOOK = SETTINGS_DEFAULTS["STAPEL_ORDERS"]["CHECKOUT_HOOKS"]["promotions"]


@pytest.mark.parametrize("user_id,actor_id", [(None, GUEST), ("7", "7")], ids=["guest", "account"])
def test_quote_reserve_commit_release_as_orders_sends_them(user_id, actor_id):
    make_promotion(value="10", code="OPEN")
    request = quote_request([line()], {"codes": ["open"]}, user_id=user_id, actor_id=actor_id)
    check_request("adjusters", ADJUSTER, request)
    answer = call(ADJUSTER, request)
    check_result("adjusters", answer)
    assert answer["errors"] == [] and answer["adjustments"][0]["amount"] == "-2.00"
    run_hook(HOOK, request, adjuster_answer=answer["adjustments"], adjuster="promotions")
    row = Redemption.objects.get()
    assert (row.user_id, row.actor_id, row.status) == (user_id or "", actor_id, "released")
