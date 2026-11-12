"""The settings fragment that wires stapel-promotions into stapel-orders.

Bricks never import each other: orders reaches this package by the comm
Function names below. A host that selects both pastes (or a scaffold
merges, two levels deep, under any explicit value) this fragment::

    from stapel_promotions.preset import SETTINGS_DEFAULTS

    STAPEL_ORDERS = {
        **STAPEL_ORDERS,
        "ADJUSTERS": {**SETTINGS_DEFAULTS["STAPEL_ORDERS"]["ADJUSTERS"],
                      **STAPEL_ORDERS.get("ADJUSTERS", {})},
        "CHECKOUT_HOOKS": {**SETTINGS_DEFAULTS["STAPEL_ORDERS"]["CHECKOUT_HOOKS"],
                           **STAPEL_ORDERS.get("CHECKOUT_HOOKS", {})},
    }

Discounts run at order 100, before shipping (200) and tax (300): the
free-shipping marker is already in ``adjustments_so_far`` when the shipping
adjuster prices its line. The adjuster and the hook are one unit — the
adjuster without the hook grants discounts no cap ever counts
(``stapel_promotions.E001``).
"""

#: The registry name under which promotions plugs into orders.
ORDERS_NAME = "promotions"
ADJUSTER_ORDER = 100

SETTINGS_DEFAULTS = {
    "STAPEL_ORDERS": {
        "ADJUSTERS": {
            ORDERS_NAME: {"quote": "promotions.quote", "order": ADJUSTER_ORDER},
        },
        "CHECKOUT_HOOKS": {
            ORDERS_NAME: {
                "reserve": "promotions.reserve",
                "commit": "promotions.commit",
                "release": "promotions.release",
                "order": ADJUSTER_ORDER,
            },
        },
    },
}

__all__ = ["ADJUSTER_ORDER", "ORDERS_NAME", "SETTINGS_DEFAULTS"]
