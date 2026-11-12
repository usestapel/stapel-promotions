"""System checks with and without stapel-orders, each with the case that must
fire and the one that must not."""
import pytest
from django.core import checks

from stapel_promotions import checks as promo_checks
from stapel_promotions.preset import SETTINGS_DEFAULTS

WIRED = SETTINGS_DEFAULTS["STAPEL_ORDERS"]


def ids(findings):
    return [f.id for f in findings]


def _orders_installed(monkeypatch, installed=True):
    monkeypatch.setattr(promo_checks, "_orders_installed", lambda: installed)


@pytest.mark.django_db
def test_without_orders_everything_is_silent(settings):
    assert promo_checks.check_orders_wiring(None) == []
    assert promo_checks.check_code_space(None) == []
    assert not [f for f in checks.run_checks() if f.id.startswith("stapel_promotions")]


def test_orders_without_the_fragment_is_w001(monkeypatch):
    _orders_installed(monkeypatch)
    assert ids(promo_checks.check_orders_wiring(None)) == ["stapel_promotions.W001"]


def test_the_shipped_fragment_is_silent(monkeypatch, settings):
    _orders_installed(monkeypatch)
    settings.STAPEL_ORDERS = WIRED
    assert promo_checks.check_orders_wiring(None) == []


def test_adjuster_without_hook_is_e001_even_without_orders(settings):
    settings.STAPEL_ORDERS = {"ADJUSTERS": WIRED["ADJUSTERS"]}
    findings = promo_checks.check_orders_wiring(None)
    assert ids(findings) == ["stapel_promotions.E001"] and isinstance(findings[0], checks.Error)


def test_a_removed_hook_entry_is_e001(settings):
    settings.STAPEL_ORDERS = {**WIRED, "CHECKOUT_HOOKS": {"promotions": None}}
    assert ids(promo_checks.check_orders_wiring(None)) == ["stapel_promotions.E001"]


def test_hold_shorter_than_the_orphan_sweep_is_w002(monkeypatch, settings):
    _orders_installed(monkeypatch)
    settings.STAPEL_ORDERS = WIRED
    settings.STAPEL_PROMOTIONS = {"RESERVATION_TTL_MINUTES": 15}
    assert ids(promo_checks.check_orders_wiring(None)) == ["stapel_promotions.W002"]
    settings.STAPEL_ORDERS = {**WIRED, "CHECKOUT_HOOK_ORPHAN_SECONDS": 600}
    assert promo_checks.check_orders_wiring(None) == []


def test_a_guessable_code_space_is_e002(settings):
    settings.STAPEL_PROMOTIONS = {"CODE_LENGTH": 6}
    assert ids(promo_checks.check_code_space(None)) == ["stapel_promotions.E002"]
    settings.STAPEL_PROMOTIONS = {"CODE_ALPHABET": "abcdefghjkmnpqrstuvwxyz23456789"}
    assert ids(promo_checks.check_code_space(None)) == ["stapel_promotions.E002"]


@pytest.mark.django_db
def test_orders_presence_is_asked_over_comm():
    from stapel_core.comm.registry import function_registry

    assert promo_checks._orders_installed() is False
    function_registry.register(promo_checks.ORDERS_PROBE_FUNCTION, lambda payload: {})
    try:
        assert promo_checks._orders_installed() is True
    finally:
        with function_registry._lock:
            function_registry._providers.pop(promo_checks.ORDERS_PROBE_FUNCTION, None)
