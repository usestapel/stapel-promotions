## What this is

Promo codes and automatic discounts for a Stapel shop: "a promo code", "a
sale", "free shipping over N". Staff define **promotions**; shoppers unlock
them with **codes** (or get them automatically); the orders checkout asks
this brick for the discount and records every use on a **ledger**, so
usage caps hold even when two shoppers race for the last use.

The brick never imports stapel-orders: it answers the orders checkout
contract by comm Function name (`promotions.quote` is the discount
adjuster, `promotions.reserve/commit/release` the checkout hook).

## Quick start

```python
INSTALLED_APPS = [
    # ...
    "stapel_promotions",
]

# Next to stapel-orders: paste the shipped fragment (a scaffold that merges
# companion presets does this for you).
from stapel_promotions.preset import SETTINGS_DEFAULTS as _PROMOTIONS

STAPEL_ORDERS = {
    **STAPEL_ORDERS,
    "ADJUSTERS": {**_PROMOTIONS["STAPEL_ORDERS"]["ADJUSTERS"], **STAPEL_ORDERS.get("ADJUSTERS", {})},
    "CHECKOUT_HOOKS": {**_PROMOTIONS["STAPEL_ORDERS"]["CHECKOUT_HOOKS"],
                       **STAPEL_ORDERS.get("CHECKOUT_HOOKS", {})},
}

# urls.py
path("promotions/api/", include("stapel_promotions.urls"))   # -> /promotions/api/v1/...

# settings.py — expire lapsed holds, purge old attempts (Celery optional)
from stapel_promotions.beat import get_promotions_beat_schedule
CELERY_BEAT_SCHEDULE = {**get_promotions_beat_schedule()}
```

The shopper's codes travel in the orders checkout `context`:
`{"codes": ["SUMMER10"]}` on quote and on checkout.

## Concepts

- **Promotion** — `percent` (capped by `max_discount`), `fixed` (in one
  currency) or `free_shipping`; scope `cart`, one `purchasable_type`, or a
  list of `keys`; a `[starts_at, ends_at)` window; `min_subtotal`;
  `stackable`; `priority`; `usage_limit_total` and `usage_limit_per_user`;
  `automatic` = applies with no code.
- **Promo code** — many per promotion, stored upper-cased (case-insensitive
  by construction), optional per-code `usage_limit` (1 = single-use) and
  `assigned_user_id` (only that shopper). Bulk generation:
  `manage.py promotions_generate_codes <key> <count>` or the staff API.
- **Ledger** — one `Redemption` per promotion per checkout attempt:
  `reserved` → `committed` → (`released` on cancel); a hold that lapses is
  `expired`. Caps count committed uses plus live holds, under the
  promotion's row lock.
- **Stacking** — all stackable promotions together, or one non-stackable
  alone, whichever saves the shopper more. Free shipping breaks ties.
- **Free shipping** — a zero-amount `discount` adjustment with
  `meta.free_shipping: true`; the shipping adjuster (order 200, after
  discounts) sees it in `adjustments_so_far` and quotes its line at 0.
- **Money** — Decimal end to end, strings on the wire. A percent rounds
  half-up to the cent; shares across lines round down with the leftover
  cents to the largest remainders. A discount never exceeds the line totals
  it applies to.

## API

| Method | Path | Who |
|---|---|---|
| GET | `active/` | anyone — live automatic promotions for a banner |
| POST | `codes/validate/` | anyone (throttled) — would this code apply |
| GET/POST | `staff/promotions/` | staff |
| GET/PATCH/DELETE | `staff/promotions/{id}/` | staff |
| GET/POST | `staff/promotions/{id}/codes/` | staff |
| POST | `staff/promotions/{id}/codes/generate/` | staff |
| GET | `staff/promotions/{id}/redemptions/` | staff |
| PATCH/DELETE | `staff/codes/{id}/` | staff |

Functions: `promotions.quote`, `promotions.reserve`, `promotions.commit`,
`promotions.release`, `promotions.codes.issue`, `promotions.codes.revoke`. Facts:
`promotions.redemption.committed`, `promotions.redemption.released`,
`promotions.promotion.exhausted`. Settings: `CONFIG.MD`. Design:
`docs/SPEC.md`. Extension points: `MODULE.md`.
