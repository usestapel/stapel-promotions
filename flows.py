"""Business flows of stapel-promotions (stapel_core.flows).

The Flow objects live here; HTTP steps are attached in ``views.py`` with
``@flow_step``. Literals are the canonical English source texts.
"""
from stapel_core.flows import Flow

REDEEM = Flow(
    "promotions.redeem",
    title="Use a promo code at checkout",
    description=(
        "A shopper sees the live sales, checks a promo code, and checks out with "
        "it. The orders checkout asks promotions.quote for the discount, then "
        "promotions.reserve holds the use under the promotion's row lock, and "
        "promotions.commit makes it permanent once the order is placed; a failed "
        "or cancelled checkout calls promotions.release."
    ),
    actors=["Shopper (guest or signed in)"],
)
REDEEM.human(order=0, note="The shopper types a code on the cart page")
REDEEM.action("promotions.redemption.committed", order=3, note="The use is recorded on the ledger")
REDEEM.action("promotions.redemption.released", order=4, note="A cancelled order gives the use back")

MANAGE = Flow(
    "promotions.manage",
    title="Run a promotion",
    description=(
        "Staff create a promotion (percent, fixed amount or free shipping; the "
        "scope, window, minimum subtotal and usage caps), attach codes or "
        "generate a batch, and watch the ledger."
    ),
    actors=["Staff"],
)
MANAGE.action("promotions.promotion.exhausted", order=12, note="The total cap is reached")
