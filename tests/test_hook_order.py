"""The checkout hook order is explicit: inventory 50, promotions 100, delivery 200.

Orders reserves in ascending order and releases in reverse; a tie would be
broken by name, which is an accident, not a decision.
"""
from stapel_promotions import preset

#: The decided order of the commerce companions' hooks.
HOOK_ORDERS = {"inventory": 50, "promotions": 100, "delivery": 200}


def test_the_hook_carries_its_decided_order():
    hooks = preset.SETTINGS_DEFAULTS["STAPEL_ORDERS"]["CHECKOUT_HOOKS"]
    assert {name: entry["order"] for name, entry in hooks.items()} == {"promotions": HOOK_ORDERS["promotions"]}


def test_no_two_companions_share_an_order():
    assert len(set(HOOK_ORDERS.values())) == len(HOOK_ORDERS)
