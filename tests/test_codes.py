"""Code text, bulk generation, the management commands and codes.issue."""
import io

import pytest
from django.core.management import CommandError, call_command
from stapel_core.comm import call

from stapel_promotions.models import PromoCode
from stapel_promotions.services import PromotionsError
from stapel_promotions.services import codes as code_text

from .factories import make_code, make_promotion

pytestmark = pytest.mark.django_db


def test_normalize():
    assert code_text.normalize(" summer 10\t") == "SUMMER10"
    assert code_text.is_well_formed("A-B_9") and not code_text.is_well_formed("-AB")
    assert not code_text.is_well_formed("X" * 65)


def test_generated_codes_avoid_look_alikes():
    p = make_promotion()
    _, codes = code_text.generate(p, 200)
    assert not set("".join(codes)) & set("01IO")


def test_collisions_are_retried_not_duplicated(monkeypatch):
    p = make_promotion()
    make_code(p, "AAAAAAAAAA")
    picks = iter(["A"] * 10 + ["B"] * 10)
    monkeypatch.setattr(code_text.secrets, "choice", lambda alphabet: next(picks))
    batch, codes = code_text.generate(p, 1)
    assert codes == ["BBBBBBBBBB"] and PromoCode.objects.count() == 2


def test_a_dense_code_space_is_refused():
    p = make_promotion()
    with pytest.raises(PromotionsError) as exc:
        code_text.generate(p, 2000, length=6)
    assert exc.value.key == "error.409.code_space_exhausted"


def test_exhausted_rounds_roll_the_batch_back(monkeypatch):
    p = make_promotion()
    make_code(p, "AAAAAAAAAA")
    monkeypatch.setattr(code_text.secrets, "choice", lambda alphabet: "A")
    with pytest.raises(PromotionsError):
        code_text.generate(p, 1)
    assert PromoCode.objects.count() == 1


def test_generate_command_writes_unique_codes(tmp_path):
    p = make_promotion(key="launch")
    out = tmp_path / "codes.txt"
    call_command("promotions_generate_codes", "launch", "300", "--prefix", "L", "--usage-limit", "1",
                 "--out", str(out), stderr=io.StringIO())
    codes = out.read_text().split()
    assert len(set(codes)) == 300 and all(c.startswith("L") for c in codes)
    assert PromoCode.objects.filter(promotion=p, usage_limit=1).count() == 300
    stdout = io.StringIO()
    call_command("promotions_generate_codes", "launch", "2", stdout=stdout, stderr=io.StringIO())
    assert len(stdout.getvalue().split()) == 2
    with pytest.raises(CommandError):
        call_command("promotions_generate_codes", "nope", "1", stderr=io.StringIO())


def test_housekeeping_command():
    out = io.StringIO()
    call_command("promotions_housekeeping", stdout=out)
    assert out.getvalue().strip() == "expired 0, purged 0"


def test_codes_issue_mints_assigned_single_use_codes(user):
    make_promotion(key="referral-reward", kind="fixed", value="5.00", currency="USD")
    answer = call("promotions.codes.issue", {"promotion_key": "referral-reward", "assigned_user_id": str(user.pk)})
    [code] = answer["codes"]
    row = PromoCode.objects.get(code=code)
    assert (row.usage_limit, row.assigned_user_id) == (1, str(user.pk))
    with pytest.raises(Exception):
        call("promotions.codes.issue", {"promotion_key": "missing"})
